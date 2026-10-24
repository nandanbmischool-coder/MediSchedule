"""Tests for the handwriting engine's safety behaviour and the Flask API."""
import io, os, sys
import pytest
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src import vision_engine as ve


# --- what a vision model would return for the handwritten sample prescription ---
PAYLOAD = {"legible": True, "notes": "Circled numbers next to each line not interpreted.", "medicines": [
    {"name": "Opox-CV", "strength": "200 mg", "frequency": "1-0-1", "food": "none", "duration_days": None, "confidence": 0.8},
    {"name": "Allthose-SP", "strength": "", "frequency": "1-1-1", "food": "none", "duration_days": None, "confidence": 0.6},
    {"name": "Breezy", "strength": "", "frequency": "0-0-1", "food": "none", "duration_days": None, "confidence": 0.7},
    {"name": "Shipen-D", "strength": "40 mg", "frequency": "1-0-1", "food": "before", "duration_days": 5, "confidence": 0.5},
]}


def test_handwriting_is_always_flagged_for_review():
    infos, transcript = ve.items_to_dosage_infos(PAYLOAD)
    assert len(infos) == 4
    assert all(i.needs_review for i in infos)
    assert all(i.method_used.startswith("handwriting:") for i in infos)
    assert all(i.overall_confidence() <= 0.8 for i in infos)
    assert "Opox-CV" in transcript


def test_schedule_comes_from_parser_not_model():
    infos, _ = ve.items_to_dosage_infos(PAYLOAD)
    assert infos[0].slots == {"morning": 1, "night": 1}
    assert infos[1].slots == {"morning": 1, "afternoon": 1, "night": 1}
    assert infos[2].slots == {"night": 1}
    assert infos[3].food_instruction == "before" and infos[3].duration_days == 5


def test_missing_frequency_is_not_invented():
    infos, _ = ve.items_to_dosage_infos({"medicines": [{"name": "Dolo", "frequency": ""}]})
    assert infos[0].slots == {} and infos[0].needs_review


def test_unreadable_page_gives_no_medicines():
    assert ve.items_to_dosage_infos({"legible": False, "medicines": []}) == ([], "")


def test_json_fences_are_tolerated():
    assert ve._parse_json('```json\n{"legible": true, "medicines": []}\n```')["legible"] is True
    with pytest.raises(ve.VisionError):
        ve._parse_json("sorry, I can't read that")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from web import backend
    backend.app.config["TESTING"] = True
    return backend.app.test_client()


def _png(text_free=True):
    buf = io.BytesIO(); Image.new("RGB", (400, 300), "white").save(buf, "PNG"); buf.seek(0); return buf


def test_typed_text_works(client):
    r = client.post("/api/scan", data={"text": "Tab Paracetamol 500mg 1-0-1 after food x 5 days"})
    j = r.get_json()
    assert r.status_code == 200 and j["medicines"][0]["slots"] == {"morning": 1, "night": 1}
    assert j["engine"] == "typed" and j["audio_url"] is None   # assets are opt-in


def test_blank_image_returns_clean_error(client):
    r = client.post("/api/scan", data={"image": (_png(), "x.png")}, content_type="multipart/form-data")
    assert r.status_code == 422 and "error" in r.get_json()


def test_bad_extension_and_empty_request(client):
    assert client.post("/api/scan", data={"image": (io.BytesIO(b"x"), "x.exe")}).status_code == 400
    assert client.post("/api/scan", data={}).status_code == 400


def test_vision_path_used_when_key_present(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(ve, "_call_api", lambda b64: __import__("json").dumps(PAYLOAD))
    r = client.post("/api/scan", data={"image": (_png(), "rx.webp")}, content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200 and j["engine"] == "handwriting-vision" and len(j["medicines"]) == 4
    assert all(m["needs_review"] for m in j["medicines"])


def test_vision_failure_falls_back_to_tesseract(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    def boom(_): raise ve.VisionError("no internet")
    monkeypatch.setattr(ve, "_call_api", boom)
    r = client.post("/api/scan", data={"image": (_png(), "rx.png")}, content_type="multipart/form-data")
    assert r.status_code in (200, 422)   # blank image -> clean 422, never a crash


# ---------------- Gemini (free) provider ----------------
import json, urllib.error


class _Resp:
    def __init__(self, obj): self._b = json.dumps(obj).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_gemini_preferred_over_anthropic(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g"); monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert ve.provider() == "gemini"
    monkeypatch.delenv("GEMINI_API_KEY"); monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert ve.provider() == "anthropic"


def test_gemini_request_and_response(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g"); monkeypatch.delenv("PRESCRIPTION_VISION_MODEL", raising=False)
    seen = []
    def fake(req, timeout=0):
        seen.append(req.full_url)
        if "gemini-3.5-flash" in req.full_url:       # first id "retired" -> must fall through
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"not found"))
        assert req.headers["X-goog-api-key"] == "g"
        body = json.loads(req.data)
        assert body["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "image/jpeg"
        return _Resp({"candidates": [{"content": {"parts": [{"text": json.dumps(PAYLOAD)}]}}]})
    monkeypatch.setattr(ve.urllib.request, "urlopen", fake)
    infos, _ = ve.items_to_dosage_infos(ve._parse_json(ve._call_api("AAAA")))
    assert len(seen) == 2 and len(infos) == 4


def test_gemini_rate_limit_message(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    def fake(req, timeout=0): raise urllib.error.HTTPError(req.full_url, 429, "x", {}, io.BytesIO(b"quota"))
    monkeypatch.setattr(ve.urllib.request, "urlopen", fake)
    with pytest.raises(ve.VisionError, match="limit"):
        ve._call_api("AAAA")


def test_dotenv_loading(tmp_path, monkeypatch):
    from web import backend
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    f = tmp_path / ".env"; f.write_text('# c\nGEMINI_API_KEY="abc123"\n')
    backend._load_dotenv(str(f))
    assert os.environ["GEMINI_API_KEY"] == "abc123"


def test_vision_error_is_not_hidden_when_tesseract_missing(client, monkeypatch):
    from web import backend
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    def boom(_): raise ve.VisionError("Gemini rejected the API key")
    def no_tess(_): raise backend.OCRError("Tesseract engine is not installed")
    monkeypatch.setattr(ve, "_call_api", boom)
    monkeypatch.setattr(backend, "extract_text_with_confidence", no_tess)
    r = client.post("/api/scan", data={"image": (_png(), "rx.png")}, content_type="multipart/form-data")
    assert "Gemini rejected" in r.get_json()["error"]


def test_missing_key_is_explained(client, monkeypatch):
    from web import backend
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"): monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(backend, "extract_text_with_confidence",
                        lambda _: (_ for _ in ()).throw(backend.OCRError("Tesseract engine is not installed")))
    r = client.post("/api/scan", data={"image": (_png(), "rx.png")}, content_type="multipart/form-data")
    assert "Gemini key" in r.get_json()["error"]


def test_key_endpoint_validates_and_sets(client, monkeypatch, tmp_path):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"): monkeypatch.delenv(k, raising=False)
    from web import backend
    monkeypatch.setattr(backend, "BASE_DIR", str(tmp_path))
    assert client.post("/api/key", json={"key": "short"}).status_code == 400
    r = client.post("/api/key", json={"key": "AIza" + "x" * 30, "remember": True})
    assert r.status_code == 200 and r.get_json()["handwriting_provider"] == "gemini"
    assert "GEMINI_API_KEY=AIza" in (tmp_path / ".env").read_text()
    assert client.get("/api/health").get_json()["handwriting_available"] is True
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_ocr_text_normalizer():
    from src.ocr_engine import _normalize
    assert _normalize("Tab1-0-1\nTakeafterfood") .startswith("Tab 1-0-1")
    from src.dosage_parser import parse_dosage
    i = parse_dosage(_normalize("PARACETAMOL 500mg\nTab1-0-1\nTakeafterfood"))
    assert i.slots == {"morning": 1, "night": 1} and i.food_instruction == "after"
