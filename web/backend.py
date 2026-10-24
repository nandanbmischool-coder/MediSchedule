#!/usr/bin/env python3
"""
web/backend.py
Flask backend for the Prescription Label Reader.

Wraps the existing offline pipeline (src/ocr_engine, src/dosage_parser,
src/schedule_generator, src/voice_output) behind a small REST API, and
serves the frontend in web/static/.

Endpoints:
    GET  /                  -> frontend (index.html)
    POST /api/scan          -> run the full pipeline, returns JSON
    GET  /api/file/<name>   -> serve a generated image/audio file
    GET  /api/health        -> {"status": "ok", "tts_available": bool}

Run with:
    python web/backend.py
Then open http://localhost:5000
"""

import ipaddress
import os
import re
import sys
import time
import uuid

from flask import Flask, request, jsonify, send_from_directory, send_file


def _load_dotenv(path):
    """Tiny .env reader (no extra dependency). Real environment variables win."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                os.environ.setdefault(k.strip(), v)


# Looks for <project root>/.env  (next to app.py and requirements.txt)
_load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))

# Make the project root importable so we can reuse src/ as-is
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ocr_engine import extract_text_with_confidence, OCRError
from src.dosage_parser import parse_dosage, parse_prescription
from src.schedule_generator import generate_text_schedule, generate_visual_image
from src.voice_output import generate_voice_schedule, VoiceOutputError, is_tts_available
from src import vision_engine

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
UPLOAD_DIR = os.path.join(BASE_DIR, "web", "uploads")
GENERATED_DIR = os.path.join(BASE_DIR, "web", "generated")
STATIC_DIR = os.path.join(BASE_DIR, "web", "static")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)

ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_CONTENT_LENGTH = 15 * 1024 * 1024  # 15 MB upload cap (phone photos can be large)

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


def _purge_old_files(max_age_s=3600):
    """Generated files are throw-away; don't let them pile up on disk."""
    now = time.time()
    for d in (GENERATED_DIR, UPLOAD_DIR):
        for name in os.listdir(d):
            fp = os.path.join(d, name)
            try:
                if os.path.isfile(fp) and now - os.path.getmtime(fp) > max_age_s:
                    os.remove(fp)
            except OSError:
                pass


def _usable(meds):
    """Did OCR find at least one real dosing instruction?"""
    return any(m.has_dosage() or m.method_used == "prn_or_stat" for m in meds)


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "That image is too large (max 15 MB). Try a smaller photo."}), 413


@app.errorhandler(500)
def server_error(_):
    return jsonify({"error": "Unexpected server error. Check the server log."}), 500


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "tts_available": is_tts_available(),
                    "handwriting_available": vision_engine.is_available(),
                    "handwriting_provider": vision_engine.provider()})


def _clean_key(raw):
    """Forgiving key parser: accepts the bare key, 'GEMINI_API_KEY=...', quoted keys,
    or a whole pasted line/URL containing an AIza... key. Returns None if unusable."""
    raw = (raw or "").strip().strip("\"'")
    m = re.search(r"AIza[0-9A-Za-z_\-]{30,}", raw)
    if m:
        return m.group(0)
    raw = re.sub(r"^\s*\w*KEY\s*[=:]\s*", "", raw).strip().strip("\"'")
    return raw if re.fullmatch(r"[A-Za-z0-9_\-\.]{20,}", raw) else None


@app.route("/api/key", methods=["POST"])
def set_key():
    """Let the user paste their free Gemini key in the web page (kept in memory;
    optionally saved to .env). Only accepted from this computer / the local network."""
    try:
        allowed = ipaddress.ip_address(request.remote_addr or "").is_private
    except ValueError:
        allowed = False
    if not allowed:
        return jsonify({"error": "Setting the key is only allowed from the local network."}), 403
    body = request.get_json(silent=True) or {}
    key = _clean_key(str(body.get("key") or ""))
    if not key:
        return jsonify({"error": "That does not look like an API key. Paste only the key itself - "
                                 "it normally starts with AIza and has no spaces. Create one at "
                                 "aistudio.google.com/apikey."}), 400
    os.environ["GEMINI_API_KEY"] = key
    saved = False
    if body.get("remember"):
        env_path = os.path.join(BASE_DIR, ".env")
        try:
            lines = open(env_path, encoding="utf-8").read().splitlines() if os.path.isfile(env_path) else []
            lines = [l for l in lines if not l.startswith("GEMINI_API_KEY=")] + [f"GEMINI_API_KEY={key}"]
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            saved = True
        except OSError as exc:
            app.logger.warning(f"Could not write .env: {exc}")
    return jsonify({"ok": True, "saved": saved, "handwriting_available": vision_engine.is_available(),
                    "handwriting_provider": vision_engine.provider()})


@app.route("/api/scan", methods=["POST"])
def scan():
    """
    Accepts multipart/form-data:
      - image: (optional) an uploaded label photo
      - text:  (optional) manually typed label text, used if no image given
      - lang:  "en" or "hi" (default "en")
      - medicine_name: (optional) manual override for the detected name

    Exactly one of `image` / `text` must be provided.
    """
    lang = request.form.get("lang", "en")
    if lang not in ("en", "hi"):
        lang = "en"
    medicine_override = request.form.get("medicine_name") or None

    image_file = request.files.get("image")
    typed_text = (request.form.get("text") or "").strip()

    if not image_file and not typed_text:
        return jsonify({"error": "Provide either an image or typed label text."}), 400

    _purge_old_files()
    session_id = uuid.uuid4().hex[:12]
    ocr_text, ocr_conf = None, None
    engine, notice, meds = "typed", None, None

    if image_file and image_file.filename:
        ext = os.path.splitext(image_file.filename)[1].lower()
        if ext not in ALLOWED_IMAGE_EXT:
            return jsonify({"error": f"Unsupported image type '{ext}'. "
                                      f"Use one of: {', '.join(sorted(ALLOWED_IMAGE_EXT))}"}), 400
        saved_path = os.path.join(UPLOAD_DIR, f"{session_id}{ext}")
        image_file.save(saved_path)
        try:
            # 1) Handwriting-capable reader (only if an API key is configured)
            if vision_engine.is_available() and not medicine_override:
                try:
                    meds, ocr_text = vision_engine.read_prescription(saved_path)
                    engine = "handwriting-vision"
                    if not meds:
                        notice = ("The image was read but no medicine lines were found. "
                                  "Make sure the whole prescription is in the photo, or add medicines manually.")
                except vision_engine.VisionError as exc:
                    app.logger.warning(f"Vision engine failed, falling back to Tesseract: {exc}")
                    notice = f"Handwriting reader unavailable ({exc}). Used offline OCR instead."
                    meds = None

            # 2) Offline Tesseract (printed labels)
            if meds is None:
                engine = "tesseract"
                try:
                    ocr_text, ocr_conf = extract_text_with_confidence(saved_path)
                except OCRError as exc:
                    if notice and vision_engine.is_available():
                        # The handwriting reader was tried and failed; the missing offline OCR is irrelevant noise.
                        return jsonify({"error": notice.replace(" Used offline OCR instead.", "")}), 503
                    why = f"{notice} " if notice else ""
                    if not vision_engine.is_available():
                        why += ("No handwriting reader is active yet - paste your free Gemini key into the "
                                "\U0001F511 box at the top of the page. ")
                    return jsonify({"error": f"{why}OCR failed: {exc}"}), 422
                if not ocr_text:
                    return jsonify({"error": "No text could be read from this image. Try a clearer, "
                                             "well-lit photo, or type the label text instead."}), 422
                meds = [parse_dosage(ocr_text, medicine_name_override=medicine_override)] if medicine_override \
                    else parse_prescription(ocr_text)
                if not _usable(meds):
                    # Don't show a made-up "medicine" built from noise.
                    hw = ("" if vision_engine.is_available() else
                          " Handwritten prescriptions need the handwriting reader: set a free "
                          "GEMINI_API_KEY (from aistudio.google.com) and restart the server.")
                    notice = ("No dosing instructions (like 1-0-1 or twice daily) could be read from this image. "
                              "Offline OCR only reads PRINTED text." + hw +
                              " You can also add the medicines manually below.")
                    meds = []
        finally:
            try:
                os.remove(saved_path)   # medical photo: don't keep it on disk
            except OSError:
                pass
    else:
        if not typed_text:
            return jsonify({"error": "Provide either an image or typed label text."}), 400
        raw_text = typed_text
        ocr_text = None
        meds = [parse_dosage(raw_text, medicine_name_override=medicine_override)] if medicine_override \
            else parse_prescription(raw_text)

    info = meds[0] if meds else None
    medicines = [{"medicine_name": m.medicine_name, "strength": m.strength, "duration_days": m.duration_days,
                  "slots": m.slots, "food_instruction": m.food_instruction, "warnings": m.warnings,
                  "method_used": m.method_used, "confidence": m.overall_confidence(),
                  "needs_review": m.needs_review, "source_text": m.raw_text} for m in meds]

    text_schedule, image_url, audio_url, voice_error = "", None, None, None

    # Server-side PNG/WAV are optional extras (the web UI plays audio in the browser),
    # so only build them on request: POST /api/scan with assets=1
    if info and request.form.get("assets") == "1":
        text_schedule = generate_text_schedule(info)
        img_filename = f"{session_id}_schedule.png"
        try:
            generate_visual_image(info, os.path.join(GENERATED_DIR, img_filename))
            image_url = f"/api/file/{img_filename}"
        except Exception as exc:
            app.logger.warning(f"Could not generate schedule image: {exc}")
        if is_tts_available():
            wav_filename = f"{session_id}_schedule.wav"
            try:
                generate_voice_schedule(info, os.path.join(GENERATED_DIR, wav_filename), lang=lang)
                audio_url = f"/api/file/{wav_filename}"
            except VoiceOutputError as exc:
                voice_error = str(exc)
        else:
            voice_error = "espeak-ng is not installed on the server, so voice playback is unavailable."

    return jsonify({
        "session_id": session_id,
        "engine": engine,
        "notice": notice,
        "medicines": medicines,
        "ocr_confidence": ocr_conf,
        "ocr_text": ocr_text,
        "medicine_name": info.medicine_name if info else "",
        "slots": info.slots if info else {},
        "food_instruction": info.food_instruction if info else "none",
        "warnings": info.warnings if info else [],
        "method_used": info.method_used if info else "none",
        "dosage_confidence": info.dosage_confidence if info else 0.0,
        "name_confidence": info.name_confidence if info else 0.0,
        "overall_confidence": info.overall_confidence() if info else 0.0,
        "text_schedule": text_schedule,
        "image_url": image_url,
        "audio_url": audio_url,
        "voice_error": voice_error,
        "lang": lang,
    })


@app.route("/api/file/<path:filename>")
def get_file(filename):
    # Only ever serve files we generated ourselves, from a fixed directory.
    safe_name = os.path.basename(filename)
    full_path = os.path.join(GENERATED_DIR, safe_name)
    if not os.path.exists(full_path):
        return jsonify({"error": "File not found."}), 404
    return send_file(full_path)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Prescription Label Reader web app running at http://localhost:{port}")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, debug=False)
