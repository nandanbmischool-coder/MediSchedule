# Prescription Label Reader

Turns a confusing medicine label ("Tab 1-0-1 after food") into something
an elderly or low-literacy patient can actually understand — a simple
**icon-based visual schedule** and a **spoken audio playback** of it.

## The Problem

Millions of elderly and low-literacy patients receive medicine strips
labeled with shorthand like `1-0-1`, `1-1-1`, or `BD`/`TDS` that only
make sense to someone trained to read them. Misreading these leads to
real, dangerous outcomes: **accidental overdose or missed doses.**
There's no simple tool that turns this into something a patient (or
their caregiver) can just look at or listen to.

## What This Project Does

1. **OCR** — reads the text off a photo of the medicine label/strip
   (using Tesseract OCR).
2. **Rule-based NLP parsing** — interprets the dosage shorthand
   (`1-0-1`, "twice daily", "before food", etc.) into a structured
   schedule. This is deliberately a transparent rule engine, not a
   black-box ML model — for a safety-critical reading like a dosage,
   predictable and testable logic is more trustworthy than a
   probabilistic guess.
3. **Visual schedule generator** — draws a clean, icon-based schedule
   image (sun/moon icons, pill counts, times) that doesn't require
   reading fluency to understand.
4. **Offline voice playback** — reads the schedule aloud using a fully
   offline text-to-speech engine (espeak-ng), in English or Hindi, so
   it works even with zero internet connection or mobile data.

Everything runs **100% offline** once installed — no API keys, no
internet calls, no cloud services. This matters for the target users,
who often have limited or no reliable data connectivity.

---

## Quick start (Mac/Windows/Linux - no Tesseract, Xcode or Homebrew needed)

```bash
python3 -m venv venv && source venv/bin/activate     # Windows: venv\\Scripts\\activate
pip install -r requirements.txt
PORT=5050 python web/backend.py                       # open http://localhost:5050
```
On the page, paste your **free Gemini key** into the key box (get it at https://aistudio.google.com/apikey)
and press Save. That turns on handwriting reading and (optionally) saves the key to `.env`.

Optional extras:
* `pip install -r requirements-offline.txt` - offline OCR for printed labels with no Tesseract (Python <= 3.12).
* Tesseract (`brew install tesseract`) - used first if present, otherwise ignored.

## Handwritten prescriptions (important)

Printed-text OCR cannot read handwriting, so handwriting uses the Gemini API (free tier, no billing):

* Key via the page's key box, or a `.env` file next to `app.py` containing `GEMINI_API_KEY=...`,
  or the environment variable. `ANTHROPIC_API_KEY` (paid) also works; Gemini is used first.
* If you get a "model not found" error, set `PRESCRIPTION_VISION_MODEL=<current id from AI Studio>`.
* A 429 error means the free-tier limit was hit: wait a minute.
* The transcription goes through the **same rule-based parser** as everything else. Handwriting
  results are always flagged "Please check" and must be confirmed by the user.
* If no reader finds a dosing instruction, the app says so and lets you add medicines manually.
* **Privacy:** with a key, the photo is sent to Google; on the free tier Google may use it to improve
  its products. Don't use real patient data you aren't allowed to share. The server listens on
  127.0.0.1 only unless you set `HOST=0.0.0.0`.

Uploaded photos are deleted right after processing; generated files are purged after 1 hour.

## Project Structure

```
prescription_reader/
├── app.py                        # Main CLI application
├── gui.py                        # Optional desktop GUI (Tkinter)
├── web/
│   ├── backend.py                 # Flask API wrapping the pipeline
│   ├── static/index.html          # Frontend (HTML/CSS/JS, no build step)
│   ├── uploads/                   # Uploaded label photos land here
│   └── generated/                 # Generated schedule PNGs/WAVs land here
├── requirements.txt              # Python package dependencies
├── src/
│   ├── ocr_engine.py              # Image -> raw text (Tesseract)
│   ├── dosage_parser.py           # Raw text -> structured dosage info (rule-based NLP)
│   ├── schedule_generator.py      # Structured info -> text + PNG visual schedule
│   ├── voice_output.py            # Structured info -> spoken .wav file (espeak-ng)
│   └── utils.py                   # Shared constants (icons, phrase templates)
├── sample_data/
│   └── generate_sample_label.py   # Creates synthetic label images for demo/testing
├── tests/
│   └── test_dosage_parser.py      # Unit tests for the parsing engine
└── output/                        # Generated schedules/images/audio land here
```

---

## Setup

### 1. Install Python packages

```bash
pip install -r requirements.txt
```

### 2. Install system dependencies

This project needs two command-line engines that Python talks to.
They are **not** installed via pip — install them for your OS:

**Tesseract OCR** (reads text from the label photo):

| OS | Command |
|---|---|
| Ubuntu/Debian | `sudo apt-get install tesseract-ocr` |
| macOS | `brew install tesseract` |
| Windows | Download installer from [github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki) |

**espeak-ng** (offline voice playback):

| OS | Command |
|---|---|
| Ubuntu/Debian | `sudo apt-get install espeak-ng` |
| macOS | `brew install espeak-ng` |
| Windows | Download installer from [github.com/espeak-ng/espeak-ng/releases](https://github.com/espeak-ng/espeak-ng/releases) |

**Tkinter** (only needed if you want the optional desktop GUI — usually
already bundled with Python):

| OS | Command |
|---|---|
| Ubuntu/Debian | `sudo apt-get install python3-tk` |
| macOS / Windows | Already included with the standard Python installer |

> The core CLI app (`app.py`) works without Tkinter. Only `gui.py`
> needs it. Similarly, if espeak-ng isn't installed, the app still
> produces the text + image schedule — it just skips the voice file
> and tells you why.

---

## Usage

### Quickest way to see it work: run the built-in demo

No photo needed — this generates 3 synthetic sample labels and runs
the full pipeline on all of them:

```bash
python app.py --demo
```

Check the `output/` folder afterward for `demo_1_schedule.txt/png/wav`,
`demo_2_...`, `demo_3_...`.

### Scan a real photo of a medicine label

```bash
python app.py --image path/to/your/photo.jpg
```

### Type the label text manually (skip OCR)

Useful if you don't have a photo handy, or OCR misreads a blurry image:

```bash
python app.py --text "PARACETAMOL 500mg Tab 1-0-1 after food"
```

### Get voice playback in Hindi instead of English

```bash
python app.py --image photo.jpg --lang hi
```

### Optional desktop GUI

```bash
python gui.py
```
A window opens where you can pick an image or type text, choose a
language, and click **Generate Schedule**.

### Web app (frontend + backend)

A full browser-based version lives in `web/`: a Flask backend
(`web/backend.py`) that wraps the same offline pipeline behind a
small REST API, and a static frontend (`web/static/index.html`) built
for the target audience — large touch targets, high contrast, a
day-by-day timeline instead of dense text, and a one-tap audio player.

```bash
pip install -r requirements.txt   # now includes Flask
python web/backend.py
```

Then open **http://localhost:5000** in a browser. From there you can:
- Upload/take a photo of a label, or type the text directly
- Switch the voice language (English / Hindi)
- See the schedule as a visual timeline, the generated PNG card, and
  (if `espeak-ng` is installed) play the spoken version

API endpoints, if you want to call it from something else:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/scan` | Runs the full pipeline. Form fields: `image` (file) or `text`, plus optional `lang` (`en`/`hi`) and `medicine_name`. Returns JSON with the parsed schedule, warnings, and URLs to the generated image/audio. |
| GET | `/api/file/<name>` | Serves a generated image or audio file. |
| GET | `/api/health` | Basic health check + whether voice playback is available. |

This is Flask's built-in dev server — fine for local/demo use. For
anything exposed beyond your own machine, put it behind a real WSGI
server (gunicorn/uWSGI) and add authentication, since it currently has
none.

### All CLI options

```bash
python app.py --help
```

---

## How the Dosage Parsing Works (the core logic)

The label text is checked against these patterns, in order:

1. **Numeric shorthand** — `1-0-1` (3-part: morning/afternoon/night)
   or `1-1-1-1` (4-part: morning/afternoon/evening/night). Separators
   `-`, `x`, `.`, or space are all accepted.
2. **Frequency phrases** — "once daily", "twice daily", "thrice daily"
   / "three times a day", "four times a day".
3. **Food timing** — looks for "before food"/"empty stomach" vs.
   "after food"/"with food".
4. **Medicine name** — the first meaningful capitalized word on the
   label that isn't a generic packaging word (tab, mg, capsule, etc.)

If nothing matches, the app **does not silently guess** — it defaults
to a single morning dose and **explicitly warns** the user to verify
with a pharmacist, since a wrong assumption here is a safety issue.

---

## Running the Tests

```bash
python -m unittest tests.test_dosage_parser -v
```

All 10 unit tests cover the numeric patterns, frequency keywords, food
timing detection, and edge cases (empty text, unparseable text,
medicine name override).

---

## Known Limitations (be upfront about these)

- **OCR accuracy** depends on photo quality — blurry, angled, or very
  low-light photos may misread text. Use `--text` to type it manually
  if OCR gets it wrong.
- **Medicine name detection** is a heuristic (first meaningful
  capitalized word) — it can occasionally pick up a wrong word if the
  label layout is unusual. Use `--medicine-name` to override it.
- **This is a prototype/educational project, not a certified medical
  device.** Always double check the generated schedule against the
  actual prescription/pharmacist before relying on it.
- Hindi voice output uses espeak-ng's built-in Hindi voice, which is
  functional but has a robotic accent, not a natural-sounding voice.

---

## Why These Design Choices

- **Rule-based parser instead of an LLM/ML model**: dosage shorthand
  follows a small, well-known set of conventions. A transparent,
  testable rule engine is safer and more predictable for a
  safety-relevant reading than a probabilistic model that could
  hallucinate a plausible-but-wrong dosage.
- **Icons drawn with basic shapes (not emoji fonts) in the PNG output**:
  emoji rendering varies across OS/font installs and can silently
  show as blank boxes — exactly the population this project serves
  can't tell if something failed to render. Simple drawn shapes
  (sun/moon/pill) always render identically everywhere.
- **espeak-ng instead of an online TTS API (e.g. Google TTS)**: keeps
  the whole tool working with zero internet connection, which matches
  the reality of the target users (limited/no mobile data).

---

## Research & Evaluation

See [`RESEARCH.md`](RESEARCH.md) for the full write-up: related work on
medication-label accessibility, why the dosage parser is rule-based
rather than a trained model, and a reproducible quantitative evaluation
(OCR accuracy vs. simulated photo quality, an ablation study isolating
what the clinical-abbreviation rules actually contribute, and a
confidence-calibration analysis).

Run the evaluation yourself:
```bash
pip install -r requirements-dev.txt
python evaluation/run_evaluation.py
```
Results (CSV, JSON, charts, results table) are written to
`evaluation/results/`.

## Reproducibility

```bash
docker build -t prescription-reader .
docker run -p 5000:5000 prescription-reader
```
`.github/workflows/tests.yml` runs the unit tests and the evaluation
harness on every push.
