"""
ocr_engine.py (v2) - multi-pass Tesseract. Tries several preprocessing
variants x page-segmentation modes and keeps the result whose text the
parser can make the most sense of (tie-break: Tesseract word confidence).
"""
import os, re, shutil, sys
from PIL import Image, ImageOps, ImageFilter
import pytesseract

# On Windows, Tesseract is usually installed but not on PATH.
# Explicitly point pytesseract to the standard Windows install location.
_WIN_TESS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
if sys.platform == "win32" and os.path.isfile(_WIN_TESS):
    pytesseract.pytesseract.tesseract_cmd = _WIN_TESS


class OCRError(Exception):
    pass


def tesseract_available():
    cmd = pytesseract.pytesseract.tesseract_cmd
    return bool(shutil.which(cmd) or os.path.isfile(cmd))


def _rapid():
    """Optional pure-pip offline OCR (no Xcode/Homebrew needed):
    pip install rapidocr-onnxruntime   (needs Python 3.12 or older)"""
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception:
        return None


_FIXES = [
    (r"(?<=[A-Za-z])(?=\d)", " "),                                              # Tab1-0-1 -> Tab 1-0-1
    (r"(?i)\b(take|once|twice|thrice)(?=(a|daily|before|after|with|at|one|two|\d))", r"\1 "),
    (r"(?i)\b(before|after|with|during)(food|meals?|breakfast|lunch|dinner)\b", r"\1 \2"),
    (r"(?i)\b(a)(day|daily)\b", r"\1 \2"),
]


def _normalize(text):
    """The pip OCR often drops spaces between words; put the important ones back."""
    for pat, rep in _FIXES:
        text = re.sub(pat, rep, text)
    return text


def _rapid_extract(path, engine):
    img = _load(path)
    tmp = img.convert("RGB")
    import numpy as np
    res, _ = engine(np.array(tmp))
    if not res:
        return "", 0.0
    text = _normalize("\n".join(r[1] for r in res))
    conf = sum(float(r[2]) for r in res) / len(res)
    return text, round(conf, 3)


def _load(path):
    if not os.path.exists(path):
        raise OCRError(f"Image file not found: {path}")
    try:
        img = ImageOps.exif_transpose(Image.open(path))
    except Exception as exc:
        raise OCRError(f"Could not open image '{path}': {exc}") from exc
    return img.convert("L")


def _upscale(img, target=1400, max_side=2200):
    """Upscale small images (Tesseract likes ~300 DPI) and downscale huge phone
    photos - a 12 MP image makes every OCR pass several times slower for no gain."""
    w, h = img.size
    if w < target:   # BUG FIX: old code computed target/w twice, so h was scaled wrongly
        img = img.resize((target, max(1, int(h * target / w))), Image.LANCZOS)
    elif max(w, h) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    return img


def _otsu(img):
    hist = img.histogram(); total = sum(hist); sum_all = sum(i * c for i, c in enumerate(hist))
    best, w0, s0, thr = 0, 0, 0, 128
    for t in range(256):
        w0 += hist[t]
        if not w0: continue
        w1 = total - w0
        if not w1: break
        s0 += t * hist[t]
        var = w0 * w1 * (s0 / w0 - (sum_all - s0) / w1) ** 2
        if var > best: best, thr = var, t
    return thr


def variants(img):
    base = _upscale(ImageOps.autocontrast(img, cutoff=1))
    yield "plain", base.filter(ImageFilter.SHARPEN)
    den = base.filter(ImageFilter.MedianFilter(3))
    yield "denoised", den
    t = _otsu(den)
    yield "otsu", den.point(lambda p: 255 if p > t else 0)
    blur = den.filter(ImageFilter.GaussianBlur(25))      # flatten uneven lighting / shadows
    flat = _flatten(den, blur)
    thr = _otsu(flat)
    yield "flat_otsu", flat.point(lambda p: 255 if p > thr else 0)


def _flatten(img, background):
    """Remove uneven lighting: paper (~background) -> white, ink stays dark."""
    from PIL import ImageChops
    return ImageOps.autocontrast(ImageChops.add(img, ImageOps.invert(background)))


def _score(text, mean_conf):
    from .dosage_parser import parse_prescription
    meds = parse_prescription(text) if text.strip() else []
    sig = sum(1 for m in meds if m.method_used not in ("default_fallback",))
    named = sum(1 for m in meds if m.name_confidence >= 0.7)
    return (sig * 2 + named, mean_conf)


def _lines_from_data(data):
    rows = {}
    for i, w in enumerate(data["text"]):
        if w.strip() and float(data["conf"][i]) >= 0:
            rows.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(w)
    return "\n".join(" ".join(ws) for _, ws in sorted(rows.items()))


def _confident(text, mc):
    from .dosage_parser import parse_prescription
    meds = parse_prescription(text)
    return mc > 0.80 and all(m.dosage_confidence >= 0.85 and m.name_confidence >= 0.7 for m in meds)


def extract_text_with_confidence(image_path, lang="eng"):
    """Returns (text, mean_word_confidence 0-1). Stops at the first pass that
    yields a fully confident parse; otherwise keeps the best-scoring pass."""
    img = _load(image_path)
    if not tesseract_available():
        eng = _rapid()
        if eng is None:
            raise OCRError("No offline OCR engine is installed (Tesseract / rapidocr). "
                           "That is fine for handwriting: use the free Gemini key box on the page.")
        return _rapid_extract(image_path, eng)
    best = None
    try:
        for _, v in variants(img):
            for psm in (6, 11):
                data = pytesseract.image_to_data(v, lang=lang, config=f"--oem 3 --psm {psm}",
                                                 output_type=pytesseract.Output.DICT)
                confs = [float(c) for w, c in zip(data["text"], data["conf"]) if w.strip() and float(c) >= 0]
                if not confs: continue
                text, mc = _lines_from_data(data).strip(), sum(confs) / len(confs) / 100
                sc = _score(text, mc)
                if best is None or sc > best[0]: best = (sc, text, mc)
                if _confident(text, mc): return text, round(mc, 3)
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRError("Tesseract engine is not installed or not found on PATH. See README.md.") from exc
    except Exception as exc:
        raise OCRError(f"OCR failed: {exc}") from exc
    return (best[1], round(best[2], 3)) if best else ("", 0.0)


def extract_text(image_path, lang="eng"):
    return extract_text_with_confidence(image_path, lang)[0]
