"""
voice_output.py
Converts a DosageInfo schedule into a spoken audio file (offline TTS)
so low-literacy users can simply listen to their schedule instead of
reading it.

Implementation note:
We call the `espeak-ng` command-line engine directly via subprocess
rather than going through the pyttsx3 wrapper. In testing, pyttsx3's
Linux driver was unreliable for longer, multi-sentence text (it would
silently fail to write a file for ~5-sentence scripts while working
fine for single sentences). Calling espeak-ng directly is simpler,
fully offline, and reliably produces a .wav file every time.

Requires: espeak-ng installed on the system (see README.md).
"""

import os
import shutil
import subprocess

from .utils import VOICE_TEMPLATES, FOOD_NOTE_VOICE
from .dosage_parser import DosageInfo
from .schedule_generator import SLOT_ORDER

# espeak-ng voice codes for supported languages
VOICE_CODES = {
    "en": "en",
    "hi": "hi",
}


class VoiceOutputError(Exception):
    pass


def build_speech_text(info: DosageInfo, lang: str = "en") -> str:
    """Builds the plain-text script that will be spoken aloud."""
    templates = VOICE_TEMPLATES.get(lang, VOICE_TEMPLATES["en"])
    food_key = info.food_instruction if info.food_instruction in ("before", "after", "with") else "none"
    food_note = FOOD_NOTE_VOICE[food_key].get(lang, FOOD_NOTE_VOICE[food_key]["en"])

    lines = [templates["intro"]]
    any_dose = False
    for slot in SLOT_ORDER:
        qty = info.slots.get(slot, 0)
        if qty and qty > 0:
            any_dose = True
            lines.append(templates["slot_line"].format(
                slot=slot, qty=qty, medicine=info.medicine_name, food_note=food_note
            ))
    if not any_dose:
        lines.append(templates["no_dose"].format(slot="today"))
    lines.append(templates["outro"])
    return " ".join(lines)


def is_tts_available() -> bool:
    """Check whether the espeak-ng engine is installed on this system."""
    return shutil.which("espeak-ng") is not None


def speak_to_file(text: str, output_path: str, lang: str = "en", rate: int = 150) -> str:
    """
    Renders `text` to a .wav audio file at `output_path` using the
    offline espeak-ng engine. Returns the output path.

    Raises VoiceOutputError if espeak-ng is not installed or synthesis
    fails -- callers should catch this and fall back to text-only
    output rather than crashing.
    """
    if not is_tts_available():
        raise VoiceOutputError(
            "espeak-ng is not installed on this system.\n"
            "Install it with:\n"
            "  Linux:   sudo apt-get install espeak-ng\n"
            "  macOS:   brew install espeak-ng\n"
            "  Windows: https://github.com/espeak-ng/espeak-ng/releases\n"
            "See README.md for details."
        )

    voice = VOICE_CODES.get(lang, "en")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    try:
        result = subprocess.run(
            ["espeak-ng", "-v", voice, "-s", str(rate), "-w", output_path, text],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise VoiceOutputError("Voice generation timed out.") from exc
    except Exception as exc:
        raise VoiceOutputError(f"Could not run espeak-ng: {exc}") from exc

    if result.returncode != 0:
        raise VoiceOutputError(f"espeak-ng failed: {result.stderr.strip()}")

    if not os.path.exists(output_path):
        raise VoiceOutputError("espeak-ng ran but no audio file was produced.")

    return output_path


def generate_voice_schedule(info: DosageInfo, output_path: str, lang: str = "en") -> str:
    """Convenience wrapper: build the script and speak it to a file."""
    text = build_speech_text(info, lang=lang)
    return speak_to_file(text, output_path, lang=lang)
