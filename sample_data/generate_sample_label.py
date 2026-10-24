"""
generate_sample_label.py
Creates a few synthetic "photo of a medicine label" images so the
project can be demoed and tested end-to-end without needing a real
prescription photo on hand.

Run directly:
    python sample_data/generate_sample_label.py
"""

import os
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

SAMPLE_LABELS = [
    {
        "filename": "sample_label_1.png",
        "lines": ["PARACETAMOL 500mg", "Tab 1-0-1", "Take after food"],
    },
    {
        "filename": "sample_label_2.png",
        "lines": ["AMOXICILLIN 250mg", "Capsule 1-1-1", "Before food"],
    },
    {
        "filename": "sample_label_3.png",
        "lines": ["CETIRIZINE 10mg", "Tablet - once daily", "At night"],
    },
]


def make_label_image(lines, path, size=(700, 300)):
    img = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(img)

    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 26)
    except Exception:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Simple "strip" border to look label-like
    draw.rectangle([10, 10, size[0] - 10, size[1] - 10], outline=(80, 80, 80), width=3)

    y = 40
    for i, line in enumerate(lines):
        font = font_big if i == 0 else font_small
        draw.text((40, y), line, fill=(10, 10, 10), font=font)
        y += 55

    img.save(path)
    return path


def main():
    for label in SAMPLE_LABELS:
        out_path = os.path.join(OUT_DIR, label["filename"])
        make_label_image(label["lines"], out_path)
        print(f"Created: {out_path}")


if __name__ == "__main__":
    main()
