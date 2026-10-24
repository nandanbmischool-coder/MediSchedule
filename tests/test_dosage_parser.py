"""
test_dosage_parser.py
Unit tests for the rule-based dosage parsing engine.

Run with:
    python -m pytest tests/
or:
    python tests/test_dosage_parser.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.dosage_parser import parse_dosage


class TestDosageParser(unittest.TestCase):

    def test_numeric_3_pattern(self):
        info = parse_dosage("PARACETAMOL 500mg Tab 1-0-1 after food")
        self.assertEqual(info.medicine_name, "Paracetamol")
        self.assertEqual(info.slots["morning"], 1)
        self.assertEqual(info.slots.get("afternoon", 0), 0)
        self.assertEqual(info.slots["night"], 1)
        self.assertEqual(info.food_instruction, "after")
        self.assertTrue(info.has_dosage())

    def test_numeric_4_pattern(self):
        info = parse_dosage("AZITHROMYCIN 500mg 1-1-1-1 before food")
        self.assertEqual(info.slots["morning"], 1)
        self.assertEqual(info.slots["afternoon"], 1)
        self.assertEqual(info.slots["evening"], 1)
        self.assertEqual(info.slots["night"], 1)
        self.assertEqual(info.food_instruction, "before")

    def test_twice_daily_keyword(self):
        info = parse_dosage("AMOXICILLIN 250mg capsules - take twice daily before food")
        self.assertEqual(info.slots.get("morning"), 1)
        self.assertEqual(info.slots.get("night"), 1)
        self.assertEqual(info.food_instruction, "before")

    def test_thrice_daily_keyword(self):
        info = parse_dosage("METFORMIN 500mg thrice daily after food")
        self.assertEqual(info.slots.get("morning"), 1)
        self.assertEqual(info.slots.get("afternoon"), 1)
        self.assertEqual(info.slots.get("night"), 1)

    def test_once_daily_keyword(self):
        info = parse_dosage("CETIRIZINE 10mg once daily")
        self.assertEqual(info.slots.get("morning"), 1)
        self.assertEqual(info.food_instruction, "none")

    def test_no_pattern_found_defaults_safely(self):
        info = parse_dosage("SOME RANDOM UNREADABLE TEXT")
        # Should not crash, should default to something safe, and warn.
        self.assertFalse(info.has_dosage())
        self.assertTrue(info.needs_review)
        self.assertTrue(len(info.warnings) > 0)

    def test_medicine_name_override(self):
        info = parse_dosage("garbled ocr text 1-0-1", medicine_name_override="Dolo 650")
        self.assertEqual(info.medicine_name, "Dolo 650")

    def test_empty_text_does_not_crash(self):
        info = parse_dosage("")
        self.assertIsNotNone(info)
        self.assertFalse(info.has_dosage())  # v2: never invent a dose

    def test_food_before_variants(self):
        for phrase in ["before food", "empty stomach", "on empty stomach", "before meal"]:
            info = parse_dosage(f"XYZMED 1-0-1 {phrase}")
            self.assertEqual(info.food_instruction, "before", msg=f"failed for '{phrase}'")

    def test_food_after_variants(self):
        for phrase in ["after food", "after meal", "post meal"]:
            info = parse_dosage(f"XYZMED 1-0-1 {phrase}")
            self.assertEqual(info.food_instruction, "after", msg=f"failed for '{phrase}'")


class TestClinicalAbbreviations(unittest.TestCase):
    """OD/BD/TDS/QDS/HS are the dominant shorthand on real Indian/UK-style
    prescriptions -- more common in practice than spelled-out phrases like
    'twice daily'. These must be recognized with high confidence and must
    not be confused with the medicine name."""

    def test_od_once_daily(self):
        info = parse_dosage("AZITHROMYCIN 500mg OD x 3 days")
        self.assertEqual(info.slots, {"morning": 1})
        self.assertEqual(info.method_used, "abbreviation")
        self.assertGreaterEqual(info.dosage_confidence, 0.85)

    def test_bd_twice_daily(self):
        info = parse_dosage("METFORMIN 500mg BD after food x 30 days")
        self.assertEqual(info.slots.get("morning"), 1)
        self.assertEqual(info.slots.get("night"), 1)
        self.assertEqual(info.food_instruction, "after")
        self.assertEqual(info.method_used, "abbreviation")

    def test_tds_thrice_daily(self):
        info = parse_dosage("IBUPROFEN 400mg TDS after food")
        self.assertEqual(info.slots.get("morning"), 1)
        self.assertEqual(info.slots.get("afternoon"), 1)
        self.assertEqual(info.slots.get("night"), 1)

    def test_qds_four_times_daily(self):
        info = parse_dosage("AMOXICILLIN 500mg QDS before food")
        for slot in ("morning", "afternoon", "evening", "night"):
            self.assertEqual(info.slots.get(slot), 1)

    def test_hs_bedtime(self):
        info = parse_dosage("ATORVASTATIN 10mg HS")
        self.assertEqual(info.slots, {"night": 1})

    def test_lowercase_bd_not_matched_as_abbreviation(self):
        # Lowercase 'bd' inside ordinary prose should NOT be treated as the
        # clinical abbreviation -- avoids false positives on noisy OCR text
        # (abbreviations are conventionally written in caps on real labels).
        info = parse_dosage("some garbled ocr text with bd in it, no clear pattern")
        self.assertNotEqual(info.method_used, "abbreviation")

    def test_abbreviation_not_picked_as_medicine_name(self):
        info = parse_dosage("PARACETAMOL 500mg BD after food")
        self.assertEqual(info.medicine_name, "Paracetamol")

    def test_numeric_pattern_takes_priority_over_abbreviation(self):
        # If a label somehow has both, the more explicit numeric pattern wins.
        info = parse_dosage("SOMEDRUG 1-0-1 BD")
        self.assertEqual(info.method_used, "numeric_pattern")


class TestPRNAndSTAT(unittest.TestCase):
    """'As needed' (SOS/PRN) and one-off (STAT) doses have no fixed daily
    schedule -- treating them like a regular pattern would misinform the
    user, so they must always carry an explicit warning."""

    def test_sos_flagged_with_warning(self):
        info = parse_dosage("CROCIN 650mg SOS for fever, max 3 per day")
        self.assertEqual(info.method_used, "prn_or_stat")
        self.assertTrue(any("as needed" in w.lower() or "sos" in w.lower() for w in info.warnings))

    def test_prn_flagged_with_warning(self):
        info = parse_dosage("PANTOPRAZOLE 40mg PRN")
        self.assertEqual(info.method_used, "prn_or_stat")
        self.assertTrue(len(info.warnings) > 0)

    def test_prn_confidence_lower_than_numeric(self):
        prn_info = parse_dosage("CROCIN SOS")
        numeric_info = parse_dosage("CROCIN 1-0-1")
        self.assertLess(prn_info.dosage_confidence, numeric_info.dosage_confidence)


class TestConfidenceScoring(unittest.TestCase):
    """Confidence must meaningfully rank-order by how explicit the source
    signal was: numeric > abbreviation > phrase > PRN > blind default."""

    def test_confidence_ordering(self):
        numeric = parse_dosage("MED 1-0-1")
        abbrev = parse_dosage("MED BD")
        phrase = parse_dosage("MED twice daily")
        default = parse_dosage("completely unparseable garbage")

        self.assertGreater(numeric.dosage_confidence, abbrev.dosage_confidence)
        self.assertGreater(abbrev.dosage_confidence, phrase.dosage_confidence)
        self.assertGreater(phrase.dosage_confidence, default.dosage_confidence)

    def test_name_override_gets_full_confidence(self):
        info = parse_dosage("garbled 1-0-1", medicine_name_override="Dolo 650")
        self.assertEqual(info.name_confidence, 1.0)

    def test_unknown_name_gets_zero_confidence(self):
        info = parse_dosage("1-0-1 after food")  # no letters at all -> unknown name
        self.assertEqual(info.medicine_name, "Unknown Medicine")
        self.assertEqual(info.name_confidence, 0.0)

    def test_overall_confidence_is_conservative_minimum(self):
        info = parse_dosage("1-0-1")  # strong dosage signal, no name at all
        self.assertEqual(info.overall_confidence(), min(info.dosage_confidence, info.name_confidence))
    # ---- v2 accuracy regressions ----
    def test_once_daily_at_night_is_night_not_morning(self):
        self.assertEqual(parse_dosage("CETIRIZINE 10mg once daily at night").slots, {"night": 1})

    def test_with_food_is_distinct(self):
        self.assertEqual(parse_dosage("XYZMED 1-0-1 with food").food_instruction, "with")

    def test_ocr_confusions_and_fractions(self):
        self.assertEqual(parse_dosage("Tab Foo 1-O-1").slots, {"morning": 1, "night": 1})
        self.assertEqual(parse_dosage("Tab Foo 1/2-0-1/2").slots, {"morning": 0.5, "night": 0.5})

    def test_batch_codes_not_read_as_schedule(self):
        self.assertFalse(parse_dosage("Batch 12-05-24 Mfg 3.4.5").has_dosage())

    def test_strength_and_duration(self):
        i = parse_dosage("Tab Amoxicillin 250 mg BD x 5 days")
        self.assertEqual((i.strength, i.duration_days), ("250 mg", 5))

    def test_multiple_medicines(self):
        from src.dosage_parser import parse_prescription
        m = parse_prescription("Rx\nTab Paracetamol 500mg 1-0-1 x 5 days\nTab Pantoprazole 40mg OD before food\nCap Amoxicillin 500mg TDS")
        self.assertEqual([x.medicine_name for x in m], ["Paracetamol", "Pantoprazole", "Amoxicillin"])


if __name__ == "__main__":
    unittest.main()
