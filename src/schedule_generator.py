"""
schedule_generator.py
Converts a DosageInfo object into:
  1. A plain-text visual schedule (icons + times) - printable / readable
  2. A PNG image schedule - drawn with PIL primitives only (no external
     font/icon files needed), so it always renders identically on any
     machine with zero missing-asset errors.

Design choice: icons are drawn as simple geometric shapes (sun circle,
moon crescent, pill dots) rather than relying on emoji fonts, because
emoji rendering varies wildly across OS/font installs and can silently
show as blank boxes for low-literacy users -- the exact audience this
project is trying to help.
"""

import os
from PIL import Image, ImageDraw, ImageFont

from .utils import SLOT_ICONS, SLOT_CLOCK_TIME
from .dosage_parser import DosageInfo, SLOTS_4

SLOT_ORDER = SLOTS_4  # morning, afternoon, evening, night

SLOT_COLORS = {
    "morning": (255, 205, 60),     # warm yellow
    "afternoon": (255, 150, 60),   # orange
    "evening": (150, 110, 220),    # dusk purple
    "night": (50, 60, 110),        # night blue
}


def generate_text_schedule(info: DosageInfo) -> str:
    """Return a human-readable, terminal/README-friendly text schedule."""
    lines = []
    lines.append("=" * 46)
    lines.append(f"  MEDICINE SCHEDULE: {info.medicine_name}")
    lines.append("=" * 46)

    any_dose = False
    for slot in SLOT_ORDER:
        qty = info.slots.get(slot, 0)
        if qty and qty > 0:
            any_dose = True
            icon_label = SLOT_ICONS[slot]
            clock = SLOT_CLOCK_TIME[slot]
            pills = "💊 " * max(1, int(qty))
            food_note = ""
            if info.food_instruction == "before":
                food_note = "  (before food)"
            elif info.food_instruction == "after":
                food_note = "  (after food)"
            elif info.food_instruction == "with":
                food_note = "  (with food)"
            lines.append(f"{icon_label}  (~{clock})")
            lines.append(f"    Take: {pills.strip()}  x{qty} tablet(s){food_note}")
            lines.append("-" * 46)

    if not any_dose:
        lines.append("No dosage times could be determined.")

    if info.warnings:
        lines.append("")
        lines.append("⚠️  NOTES / PLEASE VERIFY:")
        for w in info.warnings:
            lines.append(f"   - {w}")

    lines.append("=" * 46)
    return "\n".join(lines)


def _draw_sun(draw: ImageDraw.ImageDraw, cx, cy, r):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 210, 70))
    # rays
    import math
    for angle in range(0, 360, 45):
        rad = math.radians(angle)
        x1 = cx + (r + 4) * math.cos(rad)
        y1 = cy + (r + 4) * math.sin(rad)
        x2 = cx + (r + 12) * math.cos(rad)
        y2 = cy + (r + 12) * math.sin(rad)
        draw.line([x1, y1, x2, y2], fill=(255, 210, 70), width=3)


def _draw_moon(draw: ImageDraw.ImageDraw, cx, cy, r):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(230, 230, 245))
    # crescent cut-out
    draw.ellipse([cx - r + 8, cy - r, cx + r + 8, cy + r], fill=(50, 60, 110))


def _draw_pill(draw: ImageDraw.ImageDraw, cx, cy, w=26, h=12):
    draw.rounded_rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
                            radius=h / 2, fill=(255, 255, 255), outline=(60, 60, 60), width=2)
    draw.line([cx, cy - h / 2, cx, cy + h / 2], fill=(60, 60, 60), width=2)


def generate_visual_image(info: DosageInfo, output_path: str) -> str:
    """
    Draw a simple, high-contrast visual schedule card and save as PNG.
    Returns the output_path for convenience.
    """
    active_slots = [s for s in SLOT_ORDER if info.slots.get(s, 0) > 0]
    if not active_slots:
        active_slots = ["morning"]  # always show something rather than a blank image

    card_w, card_h = 340, 220
    margin = 20
    cols = len(active_slots)
    width = margin * (cols + 1) + card_w * cols
    height = card_h + 140  # room for title + footer

    img = Image.new("RGB", (width, height), (250, 250, 252))
    draw = ImageDraw.Draw(img)

    # Fonts: use PIL's built-in default font (no external .ttf needed => zero missing-file errors)
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
        label_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
        body_font = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception:
        title_font = ImageFont.load_default()
        label_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    # Title
    title = f"{info.medicine_name} - Daily Schedule"
    draw.text((margin, 15), title, fill=(20, 20, 30), font=title_font)

    y0 = 70
    for i, slot in enumerate(active_slots):
        x0 = margin + i * (card_w + margin)
        x1 = x0 + card_w
        y1 = y0 + card_h
        color = SLOT_COLORS[slot]

        # Card background
        draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=(255, 255, 255),
                                outline=color, width=4)

        # Icon (sun for morning/afternoon, moon for evening/night)
        icon_cx, icon_cy = x0 + card_w // 2, y0 + 55
        if slot in ("morning", "afternoon"):
            _draw_sun(draw, icon_cx, icon_cy, 28)
        else:
            _draw_moon(draw, icon_cx, icon_cy, 28)

        # Slot label + clock time
        slot_label = slot.upper()
        clock = SLOT_CLOCK_TIME[slot]
        draw.text((x0 + 20, y0 + 100), slot_label, fill=color, font=label_font)
        draw.text((x0 + 20, y0 + 128), f"~{clock}", fill=(90, 90, 90), font=body_font)

        # Pills
        qty = info.slots.get(slot, 0)
        pill_y = y0 + 165
        start_x = x0 + 20
        for p in range(max(1, int(qty))):
            _draw_pill(draw, start_x + p * 34 + 14, pill_y)
        draw.text((x0 + 20, y0 + 185), f"{qty} tablet(s)", fill=(60, 60, 60), font=body_font)

    # Footer: food instruction
    food_text = {
        "before": "Take BEFORE food",
        "after": "Take AFTER food",
        "with": "Take WITH food",
        "none": "",
    }[info.food_instruction]
    if food_text:
        draw.text((margin, height - 40), f"Note: {food_text}", fill=(180, 40, 40), font=label_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
    return output_path
