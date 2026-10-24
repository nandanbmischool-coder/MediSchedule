"""
evaluation/baseline_parser.py
A deliberately minimal "naive" dosage parser, used ONLY as an ablation
baseline in the evaluation harness -- never in the actual application.

It recognizes numeric shorthand only (e.g. "1-0-1"), has no concept of
clinical abbreviations (BD/TDS/...), spelled-out frequency phrases, food
timing, or PRN/SOS handling, and always guesses "Unknown Medicine".

Comparing this against src/dosage_parser.py's full rule engine is how
the evaluation quantifies how much each additional rule (abbreviations,
phrases, food-timing, PRN handling) actually contributes to accuracy,
rather than just asserting that more rules must be better.
"""

import re


def parse_dosage_naive(raw_text: str) -> dict:
    match = re.search(r"\b(\d)\s*-\s*(\d)\s*-\s*(\d)\b", raw_text)
    if match:
        m, a, n = (int(g) for g in match.groups())
        slots = {"morning": m, "afternoon": a, "night": n}
    else:
        slots = {"morning": 1}  # blind default, no warning, no signal of low confidence

    return {
        "medicine_name": "Unknown Medicine",
        "slots": slots,
        "food_instruction": "none",   # never detected
        "method_used": "naive_numeric_only",
    }
