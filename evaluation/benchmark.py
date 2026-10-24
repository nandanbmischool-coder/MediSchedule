"""
evaluation/benchmark.py
Builds a small labeled benchmark of synthetic medicine-label photos for
quantitative evaluation of the OCR + dosage-parsing pipeline.

Why synthetic rather than real photos:
- Real prescription photos can't be shared/redistributed (PII + PHI)
- Synthetic labels let us *control* the independent variable (image
  degradation severity) and know exact ground truth for every case,
  which is what makes a quantitative accuracy-vs-difficulty curve
  possible at all
- The 10 cases below were chosen to cover every rule branch in
  src/dosage_parser.py (numeric 3-part, numeric 4-part, clinical
  abbreviations, spelled-out phrases, PRN/SOS) -- see CASES below

Known limitation (stated up front, not buried): this is a small,
synthetic benchmark (10 label templates x 4 degradation levels = 40
images) built to exercise the parser's rule branches and give a
reproducible, controlled accuracy-vs-degradation curve. It is useful
for regression testing and relative comparisons (full parser vs.
baseline, clean vs. degraded), but it is NOT a substitute for
evaluation on a large sample of real, de-identified prescription
photos, which would be needed before any clinical-adjacent claim.
"""

import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(BENCHMARK_DIR, "benchmark_images")

RNG_SEED = 42  # fixed seed -> the benchmark is byte-for-byte reproducible

# Each case is designed to exercise one specific rule path in
# src/dosage_parser.py, so accuracy-by-pattern-type doubles as an
# ablation over the parser's own rule branches.
CASES = [
    {
        "id": "paracetamol_numeric3",
        "lines": ["PARACETAMOL 500mg", "Tab 1-0-1", "After food"],
        "medicine": "Paracetamol",
        "pattern_type": "numeric3",
        "expected_method": "numeric_pattern",
        "expected_slots": {"morning": 1, "afternoon": 0, "night": 1},
        "expected_food": "after",
    },
    {
        "id": "amoxicillin_numeric3",
        "lines": ["AMOXICILLIN 250mg", "Capsule 1-1-1", "Before food"],
        "medicine": "Amoxicillin",
        "pattern_type": "numeric3",
        "expected_method": "numeric_pattern",
        "expected_slots": {"morning": 1, "afternoon": 1, "night": 1},
        "expected_food": "before",
    },
    {
        "id": "azithromycin_numeric4",
        "lines": ["AZITHROMYCIN 500mg", "Tab 1-1-1-1", "Before food"],
        "medicine": "Azithromycin",
        "pattern_type": "numeric4",
        "expected_method": "numeric_pattern",
        "expected_slots": {"morning": 1, "afternoon": 1, "evening": 1, "night": 1},
        "expected_food": "before",
    },
    {
        "id": "metformin_bd",
        "lines": ["METFORMIN 500mg", "Tab BD", "After food"],
        "medicine": "Metformin",
        "pattern_type": "abbreviation",
        "expected_method": "abbreviation",
        "expected_slots": {"morning": 1, "night": 1},
        "expected_food": "after",
    },
    {
        "id": "ibuprofen_tds",
        "lines": ["IBUPROFEN 400mg", "Tab TDS", "After food"],
        "medicine": "Ibuprofen",
        "pattern_type": "abbreviation",
        "expected_method": "abbreviation",
        "expected_slots": {"morning": 1, "afternoon": 1, "night": 1},
        "expected_food": "after",
    },
    {
        "id": "atorvastatin_hs",
        "lines": ["ATORVASTATIN 10mg", "Tab HS"],
        "medicine": "Atorvastatin",
        "pattern_type": "abbreviation",
        "expected_method": "abbreviation",
        "expected_slots": {"night": 1},
        "expected_food": "none",
    },
    {
        "id": "cetirizine_once_daily",
        "lines": ["CETIRIZINE 10mg", "Tablet - once daily"],
        "medicine": "Cetirizine",
        "pattern_type": "phrase",
        "expected_method": "frequency_phrase",
        "expected_slots": {"morning": 1},
        "expected_food": "none",
    },
    {
        "id": "doxycycline_twice_daily",
        "lines": ["DOXYCYCLINE 100mg", "Take twice daily", "After food"],
        "medicine": "Doxycycline",
        "pattern_type": "phrase",
        "expected_method": "frequency_phrase",
        "expected_slots": {"morning": 1, "night": 1},
        "expected_food": "after",
    },
    {
        "id": "omeprazole_thrice_daily",
        "lines": ["OMEPRAZOLE 20mg", "Take thrice daily", "Before food"],
        "medicine": "Omeprazole",
        "pattern_type": "phrase",
        "expected_method": "frequency_phrase",
        "expected_slots": {"morning": 1, "afternoon": 1, "night": 1},
        "expected_food": "before",
    },
    {
        "id": "crocin_sos",
        "lines": ["CROCIN 650mg", "Tab SOS", "For fever, max 3 per day"],
        "medicine": "Crocin",
        "pattern_type": "prn",
        "expected_method": "prn_or_stat",
        "expected_slots": None,   # no fixed schedule -- checked separately
        "expected_food": "none",
    },
]

DEGRADATION_LEVELS = {
    "clean":  dict(rotate_deg=0,  blur_radius=0.0, noise_sigma=0,  brightness=1.00, contrast=1.00),
    "light":  dict(rotate_deg=3,  blur_radius=0.6, noise_sigma=6,  brightness=0.95, contrast=0.95),
    "medium": dict(rotate_deg=8,  blur_radius=1.4, noise_sigma=14, brightness=0.85, contrast=0.85),
    "heavy":  dict(rotate_deg=15, blur_radius=2.6, noise_sigma=28, brightness=0.65, contrast=0.70),
}


def _render_clean_label(lines, size=(760, 320)) -> Image.Image:
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 26)
    except Exception:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.rectangle([10, 10, size[0] - 10, size[1] - 10], outline=(80, 80, 80), width=3)
    y = 40
    for i, line in enumerate(lines):
        font = font_big if i == 0 else font_small
        draw.text((40, y), line, fill=(10, 10, 10), font=font)
        y += 55
    return img


def _apply_degradation(img: Image.Image, level_cfg: dict, rng: random.Random) -> Image.Image:
    """Simulates a real phone-camera photo: slight rotation (hand not
    perfectly parallel to the label), motion/focus blur, sensor noise
    in low light, and reduced brightness/contrast."""
    out = img

    angle = rng.uniform(-level_cfg["rotate_deg"], level_cfg["rotate_deg"]) if level_cfg["rotate_deg"] else 0
    if angle:
        out = out.rotate(angle, expand=True, fillcolor=(255, 255, 255))

    if level_cfg["blur_radius"] > 0:
        out = out.filter(ImageFilter.GaussianBlur(radius=level_cfg["blur_radius"]))

    if level_cfg["brightness"] != 1.0:
        out = ImageEnhance.Brightness(out).enhance(level_cfg["brightness"])
    if level_cfg["contrast"] != 1.0:
        out = ImageEnhance.Contrast(out).enhance(level_cfg["contrast"])

    if level_cfg["noise_sigma"] > 0:
        arr = np.array(out).astype(np.float32)
        noise = rng_noise(arr.shape, level_cfg["noise_sigma"], rng)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        out = Image.fromarray(arr)

    return out


def rng_noise(shape, sigma, rng: random.Random):
    # Use numpy's generator seeded from the Python rng for reproducibility
    seed = rng.randint(0, 2**31 - 1)
    local_rng = np.random.default_rng(seed)
    return local_rng.normal(0, sigma, size=shape)


def build_benchmark(force: bool = False) -> list:
    """
    Generates (if not already present) every (case x degradation level)
    image and returns the full ground-truth manifest as a list of dicts,
    each with an added 'image_path' and 'degradation_level' key.
    """
    os.makedirs(IMAGES_DIR, exist_ok=True)
    manifest = []
    rng = random.Random(RNG_SEED)

    for case in CASES:
        clean_img = _render_clean_label(case["lines"])
        for level_name, level_cfg in DEGRADATION_LEVELS.items():
            filename = f"{case['id']}__{level_name}.png"
            path = os.path.join(IMAGES_DIR, filename)
            if force or not os.path.exists(path):
                degraded = _apply_degradation(clean_img, level_cfg, rng)
                degraded.save(path)

            entry = dict(case)
            entry["image_path"] = path
            entry["degradation_level"] = level_name
            entry["canonical_text"] = "\n".join(case["lines"])
            manifest.append(entry)

    return manifest


if __name__ == "__main__":
    manifest = build_benchmark(force=True)
    print(f"Built benchmark: {len(manifest)} images "
          f"({len(CASES)} cases x {len(DEGRADATION_LEVELS)} degradation levels)")
    print(f"Saved to: {IMAGES_DIR}")
