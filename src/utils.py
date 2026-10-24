"""
utils.py
Shared constants and helper functions used across the project.
"""

# Icons used for each time-of-day slot in the visual schedule.
# Using plain text/emoji instead of image assets keeps the project
# dependency-free and guarantees it renders on any system/font.
SLOT_ICONS = {
    "morning": "☀️  MORNING",
    "afternoon": "🌤️  AFTERNOON",
    "evening": "🌇  EVENING",
    "night": "🌙  NIGHT",
}

SLOT_CLOCK_TIME = {
    "morning": "8:00 AM",
    "afternoon": "1:00 PM",
    "evening": "6:00 PM",
    "night": "9:00 PM",
}

PILL_ICON = "💊"
FOOD_BEFORE_ICON = "🍽️ (before food)"
FOOD_AFTER_ICON = "🍽️ (after food)"

# Simple, offline phrase templates for voice playback.
# Kept short and literal on purpose -- this is read aloud to
# elderly / low-literacy users, so clarity beats cleverness.
VOICE_TEMPLATES = {
    "en": {
        "intro": "Here is your medicine schedule.",
        "slot_line": "In the {slot}, take {qty} tablet of {medicine}{food_note}.",
        "no_dose": "No dose found for {slot}.",
        "outro": "Please follow this schedule every day. If you feel unwell, contact your doctor.",
    },
    "hi": {
        # Hindi transliteration kept simple for espeak-ng's Hindi voice.
        "intro": "Yeh aapki dawai ka samay hai.",
        "slot_line": "{slot} mein, {medicine} ki {qty} goli lein{food_note}.",
        "no_dose": "{slot} ke liye koi dawai nahi hai.",
        "outro": "Kripya har din is samay ka palan karein. Tabiyat kharab lage to doctor se milein.",
    },
}

FOOD_NOTE_VOICE = {
    "before": {"en": ", before food", "hi": ", khana khane se pehle"},
    "after": {"en": ", after food", "hi": ", khana khane ke baad"},
    "with": {"en": ", with food", "hi": ", khane ke saath"},
    "none": {"en": "", "hi": ""},
}
