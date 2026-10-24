# Prescription Label Reader — Technical Report

*A rule-based, fully offline pipeline for converting photographed medicine
labels into accessible visual + audio schedules, with a reproducible
evaluation methodology.*

## Abstract

Patients with low health literacy or limited reading ability -- a group
disproportionately made up of elderly and low-income patients -- are
significantly more likely to misread prescription instructions and take
medication incorrectly. This project addresses one narrow slice of that
problem: converting a photograph (or typed transcription) of a medicine
label into an unambiguous, accessible schedule, entirely offline, with no
cloud dependency and no account required. This report documents the
system design, situates it against related work on medication-label
accessibility, and -- the part most prototypes skip -- presents a
reproducible, quantitative evaluation of the pipeline's accuracy under
controlled image degradation, including an ablation study and a
confidence-calibration analysis.

## 1. Problem statement and related work

Poor health literacy is a well-documented, large-effect-size risk factor
for medication errors. A 2024 systematic review across 16 studies found
that patients with low health literacy had roughly 2.6 times the rate of
unintentional non-adherence and about 68% more instances of misreading
their prescriptions than patients with adequate health literacy, with the
gap widening further in elderly patients specifically ([Manias et al.,
*Health Literacy and Medication Adherence in Polypharmacy*, PMC,
2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC12360272/)). The same
review found that simplified, pictogram-based labeling interventions
measurably reduced adverse drug events, and that pharmacist-led visual
interventions cut dosage errors by roughly half. This motivates the
project's two core design choices: (1) a visual, icon-based schedule
rather than another block of text, and (2) an audio option for patients
who read poorly in any language, or in this language.

Several existing interventions informed the design:

- **PictureRx / PlainLanguageRx** (Kripalani et al., NCT01622686), a
  randomized trial of ~470 patients, tested pictogram-illustrated
  prescription labels against standard labels and is one of the larger
  controlled trials in this space.
- **Talking Pill Bottles** (NCT02827773) tested audio-recorded label
  instructions that patients could replay at home -- directly analogous
  to this project's voice-schedule feature, but for a single bottle
  rather than a general label-reading pipeline.
- **PresRx OCR** (Labovitz et al., 2016, PubMed 27564971) is the closest
  prior system: a smartphone app that OCRs prescription labels to
  auto-populate a medication tracker. In an 8-patient beta test, it
  reported OCR accuracy of 95% for drug name, 98% for dose, and 96% for
  frequency, and a System Usability Scale score of 74 (above the 70
  threshold considered acceptable). That study is a useful accuracy
  reference point, though it evaluated real prescription photos with a
  commercial OCR backend and a small usability panel, not a controlled
  benchmark with graded image difficulty -- which is the gap this
  project's evaluation harness targets instead.
- UGA/Medical College of Wisconsin research on reformatted prescription
  labels found that adding explicit plain-language instructions
  measurably improved adherence to antihypertensives, contraceptives, and
  asthma medication ([UGA News,
  2023](https://news.uga.edu/patient-friendly-prescription-labels-boost-medication-adherence/)).

**Where this project differs:** most systems above are either (a) a
labeling *format* change made upstream at the pharmacy, which this
project's users don't control, or (b) a cloud-based OCR service, which
assumes reliable connectivity and is willing to send a photo of a
medication label to a third-party server. This project instead targets
the *patient's* side of the pipeline, runs entirely offline (no image or
text ever leaves the device), and is explicit -- via confidence scoring
and warnings -- about when it is unsure, rather than presenting every
output with equal, false confidence.

## 2. System design

Four-stage deterministic pipeline (`src/`):

1. **OCR** (`ocr_engine.py`) -- Tesseract, with basic preprocessing.
2. **Dosage parsing** (`dosage_parser.py`) -- a rule-based extraction
   engine, not a trained model. Section 2.1 explains why.
3. **Schedule generation** (`schedule_generator.py`) -- text + a
   visually distinct PNG (sun/moon iconography, no external assets).
4. **Voice output** (`voice_output.py`) -- offline TTS via `espeak-ng`,
   English and Hindi.

A Flask backend (`web/backend.py`) and static frontend
(`web/static/index.html`) wrap the same pipeline for browser use.

### 2.1 Why rule-based, not a trained model

A wrong dosage read is a patient-safety issue, not a minor UX bug. Given
that, a small, fully transparent, unit-tested rule set has three concrete
advantages over a trained classifier here: every decision is traceable to
a specific regular expression that a reviewer can read and test in
isolation; it requires zero training data (real prescription photos
carry PHI and can't be casually collected for training); and it fails
*predictably* -- when no rule matches, the system defaults to the safest
interpretable option and attaches an explicit warning, rather than
outputting a confident-looking but silently wrong prediction the way a
miscalibrated classifier might.

The parser recognizes, in priority order: numeric shorthand (`1-0-1`,
`1-1-1-1`), standard clinical abbreviations (`OD`/`BD`/`TDS`/`QDS`/`HS`,
dominant on Indian and UK-style prescriptions), spelled-out frequency
phrases (`twice daily`), and `SOS`/`PRN`/`STAT` (explicitly flagged as
*not* a fixed schedule, since treating them like one would misinform the
patient). Every parse also returns `method_used` (which rule fired) and a
heuristic confidence score, so a low-confidence result can be
distinguished from a high-confidence one both internally and in the UI.

## 3. Evaluation methodology

Real, de-identified prescription photos with verified ground truth are
not something this project has access to (and collecting them would
raise its own consent/PHI issues). Instead, `evaluation/` builds a small,
**reproducible, synthetic benchmark**: 10 label templates, each targeting
one specific rule branch in the parser (numeric 3-part, numeric 4-part,
abbreviation, spelled-out phrase, PRN), rendered as clean label images
and then degraded at four severities (`clean`/`light`/`medium`/`heavy`)
combining rotation, Gaussian blur, sensor-style noise, and reduced
brightness/contrast -- simulating a hurried phone photo rather than a
flatbed scan. This gives 40 benchmark images with exact, known ground
truth, generated with a fixed random seed for byte-for-byte
reproducibility.

Three things are measured:

1. **OCR character error rate (CER)**, the standard Levenshtein-distance-based
   OCR quality metric, as a function of degradation severity.
2. **End-to-end schedule accuracy** (medicine name + every timing slot +
   food instruction all correct), both overall and broken down by
   degradation level and by dosage-notation type.
3. **An ablation**: the full rule engine vs. a deliberately naive
   baseline (`evaluation/baseline_parser.py`) that recognizes only the
   bare `1-0-1` numeric pattern and nothing else -- isolating how much
   the abbreviation/phrase/PRN rules actually contribute, rather than
   just asserting they help.
4. **Confidence calibration**: whether the parser's self-reported
   confidence score actually predicts whether the parse was correct.

Run it yourself: `python evaluation/run_evaluation.py`. Full output
(CSV, JSON, charts, `RESULTS.md`) is written to `evaluation/results/`.

## 4. Results

*(Numbers below are from the checked-in `evaluation/results/`; re-running
the harness regenerates them exactly, since the benchmark is seeded.)*

**Overall, across all 40 images:** 67.5% end-to-end exact-match accuracy
for the full pipeline vs. 0% for the naive baseline; mean OCR CER 31.8%.
The baseline's 0% overall is expected by construction -- it never
attempts medicine-name detection at all -- and is not itself the
interesting number; see the isolated ablation below.

**Accuracy vs. image quality:**

| Degradation | Mean OCR CER | End-to-end accuracy |
|---|---|---|
| Clean | 2.0% | 100% |
| Light | 2.0% | 100% |
| Medium | 23.2% | 70% |
| Heavy | 100%* | 0% |

*At the heaviest simulated degradation (15° rotation + strong blur +
noise + low contrast), Tesseract returned empty output on every image in
this benchmark -- a real, reproducible failure mode, not a metric
artifact. In practice this means: below a certain photo quality, the
system should say "I can't read this" rather than guess, which is
exactly what the safe-default + warning path does when OCR returns
nothing usable.

**Ablation (dosage-timing accuracy only, isolated from name/food
detection so the comparison is fair):**

| Notation type | Full rule engine | Naive numeric-only baseline |
|---|---|---|
| Numeric (`1-0-1`) | 75% | 75% (identical -- expected) |
| Numeric 4-part (`1-1-1-1`) | 75% | 0% |
| Clinical abbreviation (`BD`/`TDS`/`HS`) | 75% | 0% |
| Spelled-out phrase (`twice daily`) | 75% | 33%* |
| PRN/SOS | 75% | 0% |

*The baseline's 33% on phrase-type labels is not phrase understanding --
it's coincidence: the baseline's blind default (`{"morning": 1}`)
happens to match the one phrase case whose correct answer is also a
single morning dose, on the two least-degraded images. This is flagged
explicitly rather than smoothed over, because a baseline that "wins" by
accident is exactly the kind of result an evaluation should surface, not
hide.

On every notation type the baseline can't parse at all, the full engine
holds accuracy at 75% purely from the added rule coverage -- direct,
measured evidence that the abbreviation/phrase/PRN rules are pulling
their weight, not just adding complexity.

**Confidence calibration:**

| Reported confidence | n | Empirical accuracy |
|---|---|---|
| 0.0 – 0.4 (fallback / unknown) | 12 | 0% |
| 0.4 – 0.7 (PRN/SOS match) | 3 | 100% |
| 0.7 – 0.9 (abbreviation/phrase/numeric match) | 25 | 96% |

The confidence score is a genuinely useful signal here, not decoration:
every case where the parser reported low confidence was, in fact, wrong,
and the higher-confidence buckets cluster near-perfectly with actual
correctness. This is what justifies surfacing the confidence badge in
the web UI rather than presenting every schedule with the same visual
authority.

See `evaluation/results/RESULTS.md` and the three PNG charts in that
directory for the full breakdown.

## 5. Limitations (stated plainly, not buried)

- **The benchmark is small and synthetic** (40 images, 10 templates,
  English only, one font, one layout style). It is well-suited to
  regression testing and relative comparisons (this version vs. that
  version, full parser vs. baseline) but is not evidence of real-world
  accuracy on actual handwritten or printed prescriptions across
  languages, fonts, and camera hardware. A real evaluation would need a
  large, de-identified, IRB-approved sample of real prescription photos.
- **OCR fails hard, not gracefully, under heavy degradation** -- it
  returns nothing rather than a partially-correct guess. That's arguably
  the safer failure mode for a medical tool, but it does mean the system
  offers no help at all on a genuinely bad photo, only a request to
  retake it.
- **Rule-based parsing has a coverage ceiling.** New dosage phrasings
  (different abbreviation conventions, non-English label formats) require
  a new rule and a new test, not automatic generalization. This is a
  deliberate tradeoff (Section 2.1), not an oversight, but it's a real
  ceiling.
- **This is not a certified medical device** and makes no clinical
  claims. Every generated schedule carries an implicit "verify with a
  pharmacist" expectation, made explicit whenever confidence is low.

## 6. Future work

- Expand the benchmark with real (de-identified, consented) prescription
  photos once such a dataset can be ethically assembled.
- Multi-language OCR/parsing beyond English/Hindi shorthand.
- A confidence-aware UI flow that actively prompts for manual
  confirmation below some confidence threshold, rather than just showing
  a badge.
- Drug-interaction / duplicate-medicine checks when multiple labels are
  scanned in one session.

## References

1. Manias et al., *Health Literacy and Medication Adherence in
   Polypharmacy: A Systematic Review for Clinical Practice*, PMC, 2024.
   https://pmc.ncbi.nlm.nih.gov/articles/PMC12360272/
2. Kripalani et al., *PlainLanguageRx: Improving Medication Labels to
   Reduce Health Disparities*, ClinicalTrials.gov NCT01622686.
   https://clinicaltrials.gov/study/NCT01622686
3. *Improving Medication Adherence: A Study With Talking Pill Bottles*,
   ClinicalTrials.gov NCT02827773.
   https://clinicaltrials.gov/study/NCT02827773
4. Labovitz et al., *Beta Testing a Novel Smartphone Application to
   Improve Medication Adherence (PresRx OCR)*, 2016, PubMed 27564971.
   https://pubmed.ncbi.nlm.nih.gov/27564971/
5. University of Georgia News, *Patient-friendly prescription labels
   help patients take medications*, 2023.
   https://news.uga.edu/patient-friendly-prescription-labels-boost-medication-adherence/
