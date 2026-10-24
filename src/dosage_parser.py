"""
dosage_parser.py  (v2)
Deterministic, rule-based prescription parser. Safety principles:
  * NEVER invent a dose. If no schedule is found, slots stay empty and the
    result is flagged needs_review (v1 silently defaulted to "morning").
  * Every field carries a confidence; anything uncertain is flagged for
    human confirmation in the UI.
  * Handles several medicines per prescription, strength, duration,
    half tablets, OCR digit confusions (O->0, l/I->1) and time-of-day words.
"""
import re
from dataclasses import dataclass, field

SLOTS_3 = ["morning", "afternoon", "night"]
SLOTS_4 = ["morning", "afternoon", "evening", "night"]

def _all4(): return {s: 1 for s in SLOTS_4}

FREQUENCY_KEYWORDS = {
    r"\b(once|1\s*time|one\s+time)\s+(a\s+day|daily|per\s+day|every\s+day)\b": "once",
    r"\b(twice|2\s*times|two\s+times)\s+(a\s+day|daily|per\s+day)\b": {"morning": 1, "night": 1},
    r"\b(thrice|3\s*times|three\s+times)\s+(a\s+day|daily|per\s+day)\b": {"morning": 1, "afternoon": 1, "night": 1},
    r"\b(4\s*times|four\s+times)\s+(a\s+day|daily|per\s+day)\b": _all4(),
    r"\bevery\s+(morning|night|evening)\b": "once",
    r"\bat\s+(bed\s?time|night)\b": {"night": 1},
}
ABBREVIATION_KEYWORDS = {
    r"\bOD\b": "once", r"\bQD\b": "once",
    r"\b(BD|BID)\b": {"morning": 1, "night": 1},
    r"\b(TDS|TID)\b": {"morning": 1, "afternoon": 1, "night": 1},
    r"\b(QDS|QID)\b": _all4(),
    r"\bHS\b": {"night": 1},
}
PRN_PATTERN = re.compile(r"\b(SOS|PRN|as\s+needed|as\s+required|if\s+needed|when\s+required)\b", re.I)
STAT_PATTERN = re.compile(r"\bSTAT\b")

TIME_WORDS = {  # used to place a once-daily dose correctly
    "night": r"\b(night|bed\s?time|HS)\b", "morning": r"\b(morning|breakfast|a\.?m\.?)\b",
    "evening": r"\bevening\b", "afternoon": r"\b(afternoon|lunch|noon)\b",
}
FOOD_PATTERNS = [  # order matters: first match wins
    ("before", r"before\s+(food|meals?|breakfast|lunch|dinner)|empty\s+stomach|\bAC\b|\bB/F\b"),
    ("with", r"\bwith\s+(food|meals?)\b|during\s+meals?"),
    ("after", r"after\s+(food|meals?|breakfast|lunch|dinner)|post\s+meal|\bPC\b|\bA/F\b"),
]
NON_NAME_WORDS = {
    "tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule", "capsules", "syp", "syrup", "inj",
    "mg", "mcg", "ml", "strip", "batch", "mfg", "exp", "rx", "dr", "dosage", "sig", "od", "bd",
    "tds", "qds", "bid", "tid", "qid", "hs", "sos", "prn", "stat", "days", "day", "course", "weeks",
    "before", "after", "food", "meal", "meals", "empty", "stomach", "with", "post", "take", "once",
    "twice", "thrice", "daily", "times", "week", "for", "fever", "max", "per", "morning",
    "afternoon", "evening", "night", "bedtime", "at", "the", "and", "pain", "needed", "required",
    "patient", "name", "date", "age", "sex", "tablet", "doctor", "clinic", "hospital", "signature",
}
CONFIDENCE_NUMERIC, CONFIDENCE_ABBREVIATION, CONFIDENCE_FREQUENCY_PHRASE = 0.95, 0.90, 0.85
CONFIDENCE_PRN_STAT, CONFIDENCE_DEFAULT_FALLBACK = 0.55, 0.0
CONFIDENCE_NAME_CAPS_TOKEN, CONFIDENCE_NAME_LOWER_TOKEN = 0.80, 0.45
CONFIDENCE_NAME_OVERRIDE, CONFIDENCE_NAME_UNKNOWN = 1.0, 0.0
REVIEW_THRESHOLD = 0.7


KNOWN_DRUGS = """paracetamol acetaminophen crocin dolo calpol ibuprofen diclofenac aspirin naproxen aceclofenac
amoxicillin amoxycillin augmentin azithromycin clarithromycin doxycycline ciprofloxacin levofloxacin ofloxacin
cefixime cefuroxime ceftriaxone cephalexin metronidazole ornidazole tinidazole cotrimoxazole nitrofurantoin
cetirizine levocetirizine loratadine fexofenadine montelukast chlorpheniramine omeprazole pantoprazole
rabeprazole esomeprazole lansoprazole ranitidine famotidine domperidone ondansetron metformin glimepiride
gliclazide glipizide sitagliptin vildagliptin insulin atorvastatin rosuvastatin simvastatin amlodipine
telmisartan losartan olmesartan atenolol metoprolol bisoprolol propranolol ramipril enalapril furosemide
hydrochlorothiazide spironolactone clopidogrel warfarin levothyroxine thyroxine prednisolone
dexamethasone methylprednisolone salbutamol theophylline pregabalin gabapentin amitriptyline escitalopram
sertraline fluoxetine alprazolam clonazepam zolpidem folic ferrous calcium cholecalciferol vitamin
multivitamin zinc ors azithral dolo650 pan shelcal""".split()


def _snap_name(name, conf, warnings):
    import difflib
    low = name.lower()
    if low in KNOWN_DRUGS: return name, max(conf, 0.95)
    m = difflib.get_close_matches(low, KNOWN_DRUGS, n=1, cutoff=0.75)
    if m:
        warnings.append(f"Name read as '{name}' - corrected to '{m[0].title()}'. Please confirm.")
        return m[0].title(), 0.65
    warnings.append(f"'{name}' is not in the built-in medicine list - check the spelling against the prescription.")
    return name, min(conf, 0.6)


@dataclass
class DosageInfo:
    medicine_name: str
    slots: dict = field(default_factory=dict)
    food_instruction: str = "none"      # before | after | with | none
    raw_text: str = ""
    warnings: list = field(default_factory=list)
    method_used: str = "unknown"
    dosage_confidence: float = 0.0
    name_confidence: float = 0.0
    strength: str = ""                  # e.g. "500 mg"
    duration_days: int = 0              # 0 = not stated
    needs_review: bool = False

    def has_dosage(self) -> bool:
        return any(q > 0 for q in self.slots.values())

    def overall_confidence(self) -> float:
        return round(min(self.dosage_confidence, self.name_confidence), 3)


_Q = r"(?:\d+\s*/\s*\d+|\d+(?:\.\d+)?|½|¼|[OoIl])"   # a dose quantity (OCR-confusable letters allowed)
_SEP = r"\s*[-–—]\s*"


def _qty(tok: str) -> float:
    tok = tok.strip()
    if tok in "Oo": return 0
    if tok in ("I", "l"): return 1
    if tok == "½": return 0.5
    if tok == "¼": return 0.25
    if "/" in tok:
        a, b = tok.split("/"); return round(int(a) / int(b), 2) if int(b) else 1
    v = float(tok)
    return int(v) if v == int(v) else v


def _parse_numeric_pattern(text: str):
    """1-0-1 / 1-1-1-1 / ½-0-½ / 1-O-1 (OCR). Requires dash separators so
    batch codes, dates and strengths are not misread as a schedule."""
    for n, slots in ((4, SLOTS_4), (3, SLOTS_3)):
        pat = r"(?<![\w.\-/])" + _SEP.join([f"({_Q})"] * n) + r"(?![\w\-/]|\.\d)"
        m = re.search(pat, text)
        if m:
            q = [_qty(g) for g in m.groups()]
            if all(0 <= x <= 4 for x in q):
                return dict(zip(slots, q))
    return None


def _kw(table, text, flags=0, case_sensitive=False):
    for pat, val in table.items():
        if re.search(pat, text, 0 if case_sensitive else re.I):
            return val
    return None


def _place_once(text: str) -> dict:
    for slot, pat in TIME_WORDS.items():
        if re.search(pat, text, re.I if slot != "night" else 0) or (slot == "night" and re.search(r"\b(night|bed\s?time)\b", text, re.I)):
            return {slot: 1}
    return {"morning": 1}


def _parse_food(text: str) -> str:
    for kind, pat in FOOD_PATTERNS:
        if re.search(pat, text, re.I if kind != "x" else 0):
            return kind
    return "none"


def _parse_strength(text: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mg|mcg|µg|g|ml|iu)\b", text, re.I)
    return f"{m.group(1)} {m.group(2).lower()}" if m else ""


def _parse_duration(text: str) -> int:
    m = re.search(r"(?:x|for)\s*(\d{1,3})\s*(day|week|month)s?\b|\b(\d{1,3})\s*(day|week|month)s?\b", text, re.I)
    if not m: return 0
    n, unit = int(m.group(1) or m.group(3)), (m.group(2) or m.group(4)).lower()
    return n * {"day": 1, "week": 7, "month": 30}[unit]


def _guess_medicine_name(text: str):
    # Strong signals: word right after Tab/Cap/Syp/Inj, or word right before a strength.
    for pat in (r"\b(?:tab|tabs|tablet|cap|caps|capsule|syp|syrup|inj)\.?\s+([A-Za-z][A-Za-z\-]{2,})",
                r"\b([A-Za-z][A-Za-z\-]{2,})\s*\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu)\b"):
        for m in re.finditer(pat, text, re.I):
            if m.group(1).lower() not in NON_NAME_WORDS:
                return m.group(1).title(), 0.85
    cands = []
    for line in (l.strip() for l in text.splitlines() if l.strip()):
        for tok in re.findall(r"[A-Za-z][A-Za-z\-]{2,}", line):
            if tok.lower() not in NON_NAME_WORDS and not re.fullmatch(r"[OoIl\-]+", tok):
                cands.append(tok)
    if cands:
        caps = [c for c in cands if c.isupper()]
        if caps: return caps[0].title(), CONFIDENCE_NAME_CAPS_TOKEN
        return cands[0].title(), CONFIDENCE_NAME_LOWER_TOKEN
    return "Unknown Medicine", CONFIDENCE_NAME_UNKNOWN


def parse_dosage(raw_text: str, medicine_name_override: str = None) -> DosageInfo:
    warnings, method, conf = [], "default_fallback", CONFIDENCE_DEFAULT_FALLBACK
    slots = _parse_numeric_pattern(raw_text)
    if slots is not None:
        method, conf = "numeric_pattern", CONFIDENCE_NUMERIC
    else:
        v = _kw(ABBREVIATION_KEYWORDS, raw_text, case_sensitive=False if False else True)
        if v is None:  # abbreviations are case-sensitive: "bd" in prose is not BD
            v = None
        if v is not None:
            method, conf = "abbreviation", CONFIDENCE_ABBREVIATION
        else:
            v = _kw(FREQUENCY_KEYWORDS, raw_text)
            if v is not None:
                method, conf = "frequency_phrase", CONFIDENCE_FREQUENCY_PHRASE
        if v is not None:
            slots = _place_once(raw_text) if v == "once" else dict(v)
    if slots is None and (PRN_PATTERN.search(raw_text) or STAT_PATTERN.search(raw_text)):
        method, conf, slots = "prn_or_stat", CONFIDENCE_PRN_STAT, {}
        warnings.append("As-needed (SOS/PRN/STAT) medicine: there is NO fixed daily schedule. "
                        "Ask the pharmacist how often it may be taken and the daily maximum.")
    if slots is None:
        slots = {}
        warnings.append("No dosage pattern (like 1-0-1, BD or 'twice daily') was found. "
                        "No schedule has been guessed - please set it from the prescription.")
    slots = {k: v for k, v in slots.items() if v}

    if medicine_name_override:
        name, nconf = medicine_name_override, CONFIDENCE_NAME_OVERRIDE
    else:
        name, nconf = _guess_medicine_name(raw_text)
        if name == "Unknown Medicine":
            warnings.append("Could not identify the medicine name.")
        else:
            name, nconf = _snap_name(name, nconf, warnings)
    info = DosageInfo(name, slots, _parse_food(raw_text), raw_text, warnings, method, conf, nconf,
                      _parse_strength(raw_text), _parse_duration(raw_text))
    info.needs_review = (min(conf, nconf) < REVIEW_THRESHOLD) or not slots
    return info


_START = re.compile(r"^\s*(\d+[.)]\s*)?(tab|tabs|tablet|cap|caps|capsule|syp|syrup|inj|rx|\u211e)\b|"
                    r"^\s*(\d+[.)]\s*)?[A-Za-z][A-Za-z\-]{2,}\s+\d+(?:\.\d+)?\s*(mg|mcg|g|ml|iu)\b", re.I)


def parse_prescription(raw_text: str) -> list:
    """Split a full prescription into one DosageInfo per medicine."""
    segs, cur = [], []
    for line in raw_text.splitlines():
        if not line.strip(): continue
        if _START.match(line) and cur and (_has_schedule("\n".join(cur)) or len(cur) > 2):
            segs.append("\n".join(cur)); cur = []
        cur.append(line)
    if cur: segs.append("\n".join(cur))
    return [parse_dosage(s) for s in segs] or [parse_dosage(raw_text)]


def _has_schedule(t: str) -> bool:
    return bool(_parse_numeric_pattern(t) or _kw(ABBREVIATION_KEYWORDS, t, case_sensitive=True)
                or _kw(FREQUENCY_KEYWORDS, t) or PRN_PATTERN.search(t) or STAT_PATTERN.search(t))
