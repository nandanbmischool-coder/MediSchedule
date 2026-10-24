#!/usr/bin/env python3
"""
app.py
Prescription Label Reader - Main CLI application.

Turns a photo of a medicine label into:
  1. A plain-text visual schedule (printed to console + saved as .txt)
  2. A visual schedule image with icons (saved as .png)
  3. A spoken audio version of the schedule (saved as .wav)

Usage:
    python app.py --image sample_data/sample_label_1.png
    python app.py --image my_photo.jpg --lang hi
    python app.py --text "PARACETAMOL 500mg Tab 1-0-1 after food"
    python app.py --demo

Run `python app.py --help` for all options.
"""

import argparse
import os
import sys

from src.ocr_engine import extract_text, OCRError
from src.dosage_parser import parse_dosage
from src.schedule_generator import generate_text_schedule, generate_visual_image
from src.voice_output import generate_voice_schedule, VoiceOutputError, is_tts_available

OUTPUT_DIR = "output"


def run_pipeline(raw_text: str, medicine_name_override: str, lang: str, out_prefix: str):
    """Runs parse -> schedule -> image -> voice, printing progress and
    handling each stage's failures gracefully so one broken stage
    (e.g. missing TTS engine) doesn't crash the whole run."""

    print("\nStep 1/4: Parsing dosage instructions...")
    info = parse_dosage(raw_text, medicine_name_override=medicine_name_override)
    print(f"  -> Medicine identified: {info.medicine_name}")
    print(f"  -> Dosage slots: {info.slots}")
    print(f"  -> Food instruction: {info.food_instruction}")
    if info.warnings:
        for w in info.warnings:
            print(f"  ! WARNING: {w}")

    print("\nStep 2/4: Building text schedule...")
    text_schedule = generate_text_schedule(info)
    print(text_schedule)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    txt_path = os.path.join(OUTPUT_DIR, f"{out_prefix}_schedule.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text_schedule)
    print(f"  -> Saved: {txt_path}")

    print("\nStep 3/4: Generating visual schedule image...")
    img_path = os.path.join(OUTPUT_DIR, f"{out_prefix}_schedule.png")
    try:
        generate_visual_image(info, img_path)
        print(f"  -> Saved: {img_path}")
    except Exception as exc:
        print(f"  ! Could not generate image: {exc}")

    print("\nStep 4/4: Generating voice playback...")
    if not is_tts_available():
        print("  ! Skipped: espeak-ng is not installed on this system.")
        print("    Install it (see README.md) to enable voice playback.")
    else:
        wav_path = os.path.join(OUTPUT_DIR, f"{out_prefix}_schedule.wav")
        try:
            generate_voice_schedule(info, wav_path, lang=lang)
            print(f"  -> Saved: {wav_path}")
        except VoiceOutputError as exc:
            print(f"  ! Could not generate voice: {exc}")

    print("\nDone. All outputs are in the 'output/' folder.\n")
    return info


def run_demo():
    """Runs the pipeline on all bundled sample labels."""
    from sample_data.generate_sample_label import main as generate_samples, SAMPLE_LABELS

    print("Generating sample label images (if not already present)...")
    generate_samples()

    for i, label in enumerate(SAMPLE_LABELS, start=1):
        img_path = os.path.join("sample_data", label["filename"])
        print(f"\n{'#' * 60}\nDEMO {i}: {img_path}\n{'#' * 60}")
        try:
            raw_text = extract_text(img_path)
        except OCRError as exc:
            print(f"OCR failed for {img_path}: {exc}")
            continue
        run_pipeline(raw_text, medicine_name_override=None, lang="en",
                     out_prefix=f"demo_{i}")


def main():
    parser = argparse.ArgumentParser(
        description="Prescription Label Reader - converts a medicine label "
                     "photo (or typed text) into a visual + spoken schedule."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image", help="Path to a photo of the medicine label/strip")
    group.add_argument("--text", help="Manually type the label text instead of scanning an image")
    group.add_argument("--demo", action="store_true", help="Run on bundled sample labels")

    parser.add_argument("--medicine-name", default=None,
                         help="Override the auto-detected medicine name")
    parser.add_argument("--lang", default="en", choices=["en", "hi"],
                         help="Language for voice playback (default: en)")
    parser.add_argument("--out-prefix", default="my_medicine",
                         help="Filename prefix for saved outputs (default: my_medicine)")

    args = parser.parse_args()

    if args.demo:
        run_demo()
        return

    if args.image:
        print(f"Reading label image: {args.image}")
        try:
            raw_text = extract_text(args.image)
        except OCRError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        if not raw_text:
            print("ERROR: No text could be read from this image. Try a clearer, "
                  "well-lit photo, or use --text to type it manually.", file=sys.stderr)
            sys.exit(1)
        print(f"OCR extracted text:\n---\n{raw_text}\n---")
    else:
        raw_text = args.text

    run_pipeline(raw_text, args.medicine_name, args.lang, args.out_prefix)


if __name__ == "__main__":
    main()
