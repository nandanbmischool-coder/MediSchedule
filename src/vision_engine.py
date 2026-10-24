"""
vision_engine.py - optional handwriting-capable reader.

Tesseract only reads PRINTED text. Doctors' prescriptions are usually
handwritten, so when an ANTHROPIC_API_KEY is set the web app first asks a
vision model to *transcribe* the prescription into structured fields.

Safety design (important for a medical tool):
  * The model only transcribes. Its output is turned into a normal
    "Tab <name> <strength> <frequency> <food> x <days> days" line and sent
    through the same deterministic parser as everything else - the model
    never decides a schedule by itself.
  * Anything read from handwriting is ALWAYS flagged needs_review so the
    user must confirm it in the UI.
  * Uses only the standard library (no extra pip dependency). If there is
    no key, no internet, or the call fails, callers fall back to Tesseract.

Providers (first key found wins):
  GEMINI_API_KEY / GOOGLE_API_KEY  Google Gemini - has a FREE tier (no billing needed)
  ANTHROPIC_API_KEY                Anthropic Claude - paid
Optional:
  PRESCRIPTION_VISION_MODEL        force a specific model id for the chosen provider
"""
import base64
import io
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

from PIL import Image, ImageOps

from .dosage_parser import parse_dosage

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Google renames/retires models often (2.5 line shut down 16 Oct 2026), so we try
# several ids in order and use the first that exists. Copy a current id from
# https://aistudio.google.com and set PRESCRIPTION_VISION_MODEL if all of these fail.
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-flash-latest"]
MAX_SIDE = 1568          # larger images are downscaled; no accuracy gain, slower upload
TIMEOUT_S = 60

PROMPT = """You are transcribing a photo of a medical prescription for a patient-safety tool.
Transcribe ONLY what is actually written. Never guess or "correct" a dose.

Return ONLY a JSON object, no prose, no markdown fences:
{
  "legible": true or false,            // false if the page is unreadable / not a prescription
  "medicines": [
    {
      "name": "drug name exactly as written (use ? for unreadable letters)",
      "strength": "e.g. 500 mg, or empty string if not written",
      "frequency": "dosage exactly as written, e.g. 1-0-1, 0-0-1, BD, TDS, SOS, once daily; empty string if not written",
      "food": "before | after | with | none",
      "duration_days": integer or null,  // only if a number of days/weeks is clearly written
      "confidence": number 0-1           // your confidence in this line
    }
  ],
  "notes": "anything ambiguous (circled numbers, crossed-out lines, unclear symbols)"
}
Rules: list one entry per medicine line, in order. Ignore patient details, vitals, clinic header and stamps.
Do not convert shorthand: if it says 1-0-1 write 1-0-1. If a field is not written, leave it empty."""


class VisionError(Exception):
    pass


def _ssl_context():
    """python.org Python on macOS ships without root certificates, which gives
    CERTIFICATE_VERIFY_FAILED. Use the 'certifi' bundle (installed with requirements.txt)
    when available; fall back to the system default otherwise."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _net_error(service, exc):
    msg = f"Could not reach the {service} API: {exc}"
    if "CERTIFICATE_VERIFY_FAILED" in str(exc):
        msg += (" -> Fix: run  pip install certifi  (or double-click 'Install Certificates.command' "
                "in /Applications/Python 3.x/) and restart the server.")
    return VisionError(msg)


def _gemini_key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def provider():
    """'gemini' (free tier) is preferred over 'anthropic' (paid); None if no key."""
    if _gemini_key():
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def is_available() -> bool:
    return provider() is not None


def _encode_image(path: str) -> str:
    try:
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except Exception as exc:
        raise VisionError(f"Could not open image: {exc}") from exc
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _call_api(b64_jpeg: str) -> str:
    p = provider()
    if p == "gemini":
        return _call_gemini(b64_jpeg)
    if p == "anthropic":
        return _call_anthropic(b64_jpeg)
    raise VisionError("No API key set (GEMINI_API_KEY or ANTHROPIC_API_KEY).")


GEMINI_LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"
_SKIP = ("image", "tts", "live", "audio", "native", "robotics", "computer", "embedding", "imagen", "veo", "gemma", "learnlm")


def discover_gemini_models():
    """Ask Google which models this key may call (ListModels) and return vision-capable
    'flash' models, stable ones first, 'lite' and previews last. [] if the call fails."""
    req = urllib.request.Request(GEMINI_LIST_URL, headers={"x-goog-api-key": _gemini_key()})
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as r:
            models = json.loads(r.read()).get("models", [])
    except Exception:
        return []
    ok = []
    for m in models:
        name = str(m.get("name", "")).replace("models/", "")
        if ("generateContent" in (m.get("supportedGenerationMethods") or [])
                and "flash" in name and not any(x in name for x in _SKIP)):
            ok.append(name)
    return sorted(ok, key=lambda n: ("preview" in n or "exp" in n, "lite" in n, n), reverse=False)[:6]


def _try_gemini_models(models, body, problems):
    """Returns the model's text, or None if every model was missing/busy. Fatal errors raise."""
    for model in models:
        for attempt in range(2):                       # one quick retry per model for busy errors
            req = urllib.request.Request(GEMINI_URL.format(model=model), data=body, method="POST",
                                         headers={"content-type": "application/json",
                                                  "x-goog-api-key": _gemini_key()})
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_ssl_context()) as r:
                    data = json.loads(r.read())
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:200]
                if exc.code == 404:                    # retired / unknown model id
                    problems.append(f"{model}: not found")
                    break
                if exc.code in (500, 502, 503, 504):   # temporary overload
                    problems.append(f"{model}: busy ({exc.code})")
                    if attempt == 0:
                        time.sleep(2)
                        continue
                    break
                if exc.code == 429:
                    raise VisionError("Gemini free-tier limit reached (429). Wait a minute and try again.") from exc
                if exc.code in (400, 403) and "API key" in detail:
                    raise VisionError("Gemini rejected the API key - check it at aistudio.google.com/apikey.") from exc
                raise VisionError(f"Gemini API returned HTTP {exc.code}: {detail}") from exc
            except Exception as exc:
                raise _net_error("Gemini", exc) from exc
            parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
            text = "".join(p.get("text", "") for p in parts)
            if not text:
                raise VisionError("Gemini returned no text (the image may have been blocked).")
            return text
    return None


def _call_gemini(b64_jpeg: str) -> str:
    """Order: your PRESCRIPTION_VISION_MODEL (if set) -> built-in list -> models Google reports
    for your key. 404 and busy errors move on; limit (429) and bad-key errors stop at once."""
    forced = os.environ.get("PRESCRIPTION_VISION_MODEL")
    body = json.dumps({
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": b64_jpeg}},
            {"text": PROMPT}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }).encode()
    problems, tried = [], []
    for batch in ([forced] if forced else [], GEMINI_MODELS, None):
        if batch is None:                              # last resort: ask Google what exists
            batch = [m for m in discover_gemini_models() if m not in tried]
        batch = [m for m in batch if m and m not in tried]
        tried += batch
        text = _try_gemini_models(batch, body, problems) if batch else None
        if text is not None:
            return text
    if any("busy" in p for p in problems):
        raise VisionError("Google's Gemini servers are busy right now (" + "; ".join(problems[-3:]) +
                          "). This is temporary - press Read prescription again in a minute.")
    raise VisionError("No Gemini model worked (" + "; ".join(problems[-4:]) + "). If you set "
                      "PRESCRIPTION_VISION_MODEL, remove it - the app can pick a model automatically.")


def _call_anthropic(b64_jpeg: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    body = json.dumps({
        "model": os.environ.get("PRESCRIPTION_VISION_MODEL", DEFAULT_MODEL),
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_jpeg}},
            {"type": "text", "text": PROMPT},
        ]}],
    }).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_ssl_context()) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise VisionError(f"Vision API returned HTTP {exc.code}: {detail}") from exc
    except Exception as exc:  # no internet, timeout, DNS ...
        raise _net_error("Anthropic", exc) from exc
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise VisionError("Vision model did not return JSON.")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise VisionError(f"Vision model returned invalid JSON: {exc}") from exc


_FOOD = {"before": "before food", "after": "after food", "with": "with food"}


def items_to_dosage_infos(payload: dict):
    """Turn the model's transcription into DosageInfo objects via the
    deterministic parser. Returns (list[DosageInfo], transcript_text)."""
    if not payload.get("legible", True) and not payload.get("medicines"):
        return [], ""
    infos, lines = [], []
    for it in payload.get("medicines") or []:
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        strength = str(it.get("strength") or "").strip()
        freq = str(it.get("frequency") or "").strip()
        food = _FOOD.get(str(it.get("food") or "").lower(), "")
        days = it.get("duration_days")
        days_txt = f"x {int(days)} days" if isinstance(days, (int, float)) and days > 0 else ""
        line = " ".join(p for p in ("Tab", name, strength, freq, food, days_txt) if p)
        lines.append(line)

        info = parse_dosage(line)
        if info.medicine_name.lower() == name.lower().replace(" ", "") or info.medicine_name.lower() == name.lower():
            info.medicine_name = name                      # keep the spelling as written
        # Handwriting is never "clear": cap confidence and force human review.
        try:
            model_conf = max(0.0, min(1.0, float(it.get("confidence", 0.7))))
        except (TypeError, ValueError):
            model_conf = 0.7
        info.dosage_confidence = min(info.dosage_confidence, 0.8, max(model_conf, 0.3)) if info.has_dosage() else info.dosage_confidence
        info.name_confidence = min(info.name_confidence, 0.8, max(model_conf, 0.3))
        info.method_used = f"handwriting:{info.method_used}"
        info.needs_review = True
        info.warnings.insert(0, "Read from handwriting - check name, dose and timing against the paper prescription.")
        if "?" in name:
            info.warnings.append("Some letters of the name were unreadable (shown as ?).")
        infos.append(info)
    notes = str(payload.get("notes") or "").strip()
    if notes:
        for i in infos:
            i.warnings.append(f"Reader note: {notes}")
    return infos, "\n".join(lines)


def read_prescription(image_path: str):
    """Returns (list[DosageInfo], transcript). Raises VisionError on failure."""
    payload = _parse_json(_call_api(_encode_image(image_path)))
    return items_to_dosage_infos(payload)
