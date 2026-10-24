#!/usr/bin/env python3
"""
evaluation/run_evaluation.py
Quantitative evaluation of the Prescription Label Reader pipeline.

Measures, across the synthetic benchmark in evaluation/benchmark.py:
  1. OCR character error rate (CER), by image degradation severity
  2. End-to-end dosage-schedule accuracy (medicine name + timing slots +
     food instruction all correct), by degradation severity and by
     dosage-pattern type
  3. An ablation: the full rule engine (src/dosage_parser.py) vs. a
     deliberately naive numeric-only baseline (evaluation/baseline_parser.py),
     to quantify what each additional rule class (abbreviations, phrases,
     food timing, PRN handling) actually buys in accuracy
  4. Confidence calibration: does the parser's self-reported confidence
     score actually track whether the parse was correct?

Run with:
    python evaluation/run_evaluation.py

Outputs (written to evaluation/results/):
    per_image_results.csv   raw results, one row per (case, degradation level)
    metrics_summary.json    aggregated metrics
    RESULTS.md              human-readable results table
    accuracy_by_degradation.png
    full_vs_baseline.png
    confidence_calibration.png
"""

import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.ocr_engine import extract_text, OCRError
from src.dosage_parser import parse_dosage
from evaluation.benchmark import build_benchmark, CASES, DEGRADATION_LEVELS
from evaluation.baseline_parser import parse_dosage_naive

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
LEVEL_ORDER = ["clean", "light", "medium", "heavy"]


def char_error_rate(hypothesis: str, reference: str) -> float:
    """Levenshtein edit distance normalized by reference length.
    Standard OCR quality metric (lower is better; 0 = perfect)."""
    ref = reference.strip()
    hyp = hypothesis.strip()
    if not ref:
        return 0.0 if not hyp else 1.0

    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, start=1):
        cur = [i] + [0] * len(hyp)
        for j, hc in enumerate(hyp, start=1):
            cost = 0 if rc == hc else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[len(hyp)] / max(len(ref), 1)


def slots_match(predicted: dict, expected: dict) -> bool:
    if expected is None:
        return False
    keys = set(predicted.keys()) | set(expected.keys())
    return all(predicted.get(k, 0) == expected.get(k, 0) for k in keys)


def evaluate_case(entry: dict) -> dict:
    image_path = entry["image_path"]
    try:
        ocr_text = extract_text(image_path)
    except OCRError as exc:
        ocr_text = ""
        print(f"  ! OCR failed for {image_path}: {exc}")

    cer = char_error_rate(ocr_text, entry["canonical_text"])

    info = parse_dosage(ocr_text)
    is_prn_case = entry["expected_slots"] is None

    name_correct = info.medicine_name.strip().lower() == entry["medicine"].strip().lower()
    food_correct = info.food_instruction == entry["expected_food"]
    method_correct = info.method_used == entry["expected_method"]
    slots_correct = (
        method_correct if is_prn_case else slots_match(info.slots, entry["expected_slots"])
    )
    full_correct = name_correct and food_correct and slots_correct and method_correct

    naive = parse_dosage_naive(ocr_text)
    # NOTE on baseline design: the naive baseline never attempts medicine-name
    # detection or food-timing detection at all (see baseline_parser.py) -- it
    # only recognizes the bare "1-0-1" numeric pattern. So `baseline_slots_correct`
    # is the fair, isolated comparison for the "which dosage notations can be
    # parsed at all" ablation. `baseline_full_correct` additionally requires name
    # + food, which the baseline was never designed to attempt -- it's reported
    # separately as a measure of "what a bare-minimum implementation gets you
    # end-to-end", not as a per-rule ablation.
    naive_slots_correct = (
        False if is_prn_case else slots_match(naive["slots"], entry["expected_slots"])
    )
    naive_name_correct = naive["medicine_name"].strip().lower() == entry["medicine"].strip().lower()
    naive_food_correct = naive["food_instruction"] == entry["expected_food"]
    naive_full_correct = naive_slots_correct and naive_name_correct and naive_food_correct

    return {
        "case_id": entry["id"],
        "pattern_type": entry["pattern_type"],
        "degradation_level": entry["degradation_level"],
        "ocr_text": ocr_text.replace("\n", " | "),
        "ocr_cer": round(cer, 4),
        "predicted_name": info.medicine_name,
        "predicted_slots": json.dumps(info.slots),
        "predicted_food": info.food_instruction,
        "predicted_method": info.method_used,
        "dosage_confidence": info.dosage_confidence,
        "name_confidence": info.name_confidence,
        "overall_confidence": info.overall_confidence(),
        "name_correct": name_correct,
        "food_correct": food_correct,
        "slots_correct": slots_correct,
        "method_correct": method_correct,
        "full_pipeline_correct": full_correct,
        "baseline_full_correct": naive_full_correct,
        "baseline_slots_correct": naive_slots_correct,
    }


def summarize(rows: list) -> dict:
    def avg(xs):
        xs = list(xs)
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    by_level = {}
    for level in LEVEL_ORDER:
        level_rows = [r for r in rows if r["degradation_level"] == level]
        by_level[level] = {
            "n": len(level_rows),
            "mean_ocr_cer": avg(r["ocr_cer"] for r in level_rows),
            "full_pipeline_accuracy": avg(r["full_pipeline_correct"] for r in level_rows),
            "baseline_accuracy": avg(r["baseline_full_correct"] for r in level_rows),
        }

    by_pattern = {}
    for ptype in sorted(set(r["pattern_type"] for r in rows)):
        p_rows = [r for r in rows if r["pattern_type"] == ptype]
        by_pattern[ptype] = {
            "n": len(p_rows),
            # Dosage-timing accuracy only (isolated from name/food detection,
            # which the naive baseline never attempts) -- the fair, apples-to-
            # apples ablation of "can this notation be parsed at all".
            "full_engine_slots_accuracy": avg(r["slots_correct"] for r in p_rows),
            "baseline_slots_accuracy": avg(r["baseline_slots_correct"] for r in p_rows),
            # Full end-to-end accuracy (name + slots + food), for reference.
            "full_pipeline_accuracy": avg(r["full_pipeline_correct"] for r in p_rows),
            "baseline_full_accuracy": avg(r["baseline_full_correct"] for r in p_rows),
        }

    # Confidence calibration: bucket by overall_confidence, check empirical accuracy per bucket
    buckets = [(0.0, 0.4), (0.4, 0.7), (0.7, 0.9), (0.9, 1.01)]
    calibration = []
    for lo, hi in buckets:
        b_rows = [r for r in rows if lo <= r["overall_confidence"] < hi]
        calibration.append({
            "bucket": f"{lo:.1f}-{hi:.1f}",
            "n": len(b_rows),
            "mean_confidence": avg(r["overall_confidence"] for r in b_rows),
            "empirical_accuracy": avg(r["full_pipeline_correct"] for r in b_rows),
        })

    return {
        "n_total": len(rows),
        "overall_full_pipeline_accuracy": avg(r["full_pipeline_correct"] for r in rows),
        "overall_baseline_accuracy": avg(r["baseline_full_correct"] for r in rows),
        "overall_mean_ocr_cer": avg(r["ocr_cer"] for r in rows),
        "by_degradation_level": by_level,
        "by_pattern_type": by_pattern,
        "confidence_calibration": calibration,
    }


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_accuracy_by_degradation(summary, path):
    levels = LEVEL_ORDER
    full = [summary["by_degradation_level"][lv]["full_pipeline_accuracy"] for lv in levels]
    cer = [summary["by_degradation_level"][lv]["mean_ocr_cer"] for lv in levels]

    fig, ax1 = plt.subplots(figsize=(7, 4.5))
    x = range(len(levels))
    width = 0.5
    ax1.bar(list(x), full, width, label="Full pipeline accuracy", color="#1D6B5E")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels([lv.capitalize() for lv in levels])
    ax1.set_ylabel("End-to-end exact-match accuracy")
    ax1.set_ylim(0, 1.05)
    ax1.set_xlabel("Image degradation severity (simulated photo quality)")

    ax2 = ax1.twinx()
    ax2.plot(list(x), cer, color="#C1443B", marker="o", label="OCR character error rate")
    ax2.set_ylabel("OCR character error rate", color="#C1443B")
    ax2.set_ylim(0, max(0.5, max(cer) * 1.3 if cer else 0.5))
    ax2.tick_params(axis="y", labelcolor="#C1443B")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8)

    plt.title("Accuracy and OCR error vs. simulated photo quality")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_full_vs_baseline_by_pattern(summary, path):
    patterns = sorted(summary["by_pattern_type"].keys())
    full = [summary["by_pattern_type"][p]["full_engine_slots_accuracy"] for p in patterns]
    baseline = [summary["by_pattern_type"][p]["baseline_slots_accuracy"] for p in patterns]

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    x = range(len(patterns))
    width = 0.35
    ax.bar([i - width/2 for i in x], full, width, label="Full rule engine", color="#1D6B5E")
    ax.bar([i + width/2 for i in x], baseline, width, label="Naive (numeric-only) baseline", color="#E2A33B")
    ax.set_xticks(list(x))
    ax.set_xticklabels(patterns, rotation=20, ha="right")
    ax.set_ylabel("Dosage-timing accuracy (isolated from name/food detection)")
    ax.set_ylim(0, 1.05)
    ax.set_title("Ablation: dosage-timing accuracy by notation type\n(full parser vs. numeric-only baseline)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_confidence_calibration(summary, path):
    cal = summary["confidence_calibration"]
    buckets = [c["bucket"] for c in cal]
    conf = [c["mean_confidence"] for c in cal]
    acc = [c["empirical_accuracy"] for c in cal]

    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    x = range(len(buckets))
    width = 0.35
    ax.bar([i - width/2 for i in x], conf, width, label="Mean reported confidence", color="#2E3A5C")
    ax.bar([i + width/2 for i in x], acc, width, label="Empirical accuracy", color="#1D6B5E")
    ax.set_xticks(list(x))
    ax.set_xticklabels(buckets)
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("Confidence bucket")
    ax.set_title("Confidence calibration:\ndoes reported confidence track actual correctness?")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def write_results_markdown(summary, path, elapsed_s):
    lines = []
    lines.append("# Evaluation Results\n")
    lines.append(f"Benchmark size: **{summary['n_total']} images** "
                  f"({len(CASES)} label templates x {len(DEGRADATION_LEVELS)} degradation levels). "
                  f"Evaluation runtime: {elapsed_s:.1f}s.\n")
    lines.append(f"- **Overall end-to-end accuracy (full pipeline):** {summary['overall_full_pipeline_accuracy']*100:.1f}%")
    lines.append(f"- **Overall end-to-end accuracy (naive baseline):** {summary['overall_baseline_accuracy']*100:.1f}%")
    lines.append(f"- **Mean OCR character error rate:** {summary['overall_mean_ocr_cer']*100:.1f}%\n")

    lines.append("## Accuracy by image degradation level\n")
    lines.append("| Level | n | Mean OCR CER | Full pipeline accuracy | Baseline accuracy |")
    lines.append("|---|---|---|---|---|")
    for lv in LEVEL_ORDER:
        d = summary["by_degradation_level"][lv]
        lines.append(f"| {lv} | {d['n']} | {d['mean_ocr_cer']*100:.1f}% | "
                      f"{d['full_pipeline_accuracy']*100:.1f}% | {d['baseline_accuracy']*100:.1f}% |")

    lines.append("\n## Ablation: dosage-timing accuracy by notation type\n")
    lines.append(
        "Isolated to dosage-timing detection only (the baseline never attempts "
        "name or food-timing detection, so those are excluded here for a fair, "
        "apples-to-apples comparison of *which notations each parser can read*).\n"
    )
    lines.append("| Pattern type | n | Full rule engine | Naive (numeric-only) baseline |")
    lines.append("|---|---|---|---|")
    for p, d in sorted(summary["by_pattern_type"].items()):
        lines.append(f"| {p} | {d['n']} | {d['full_engine_slots_accuracy']*100:.1f}% | {d['baseline_slots_accuracy']*100:.1f}% |")

    lines.append("\n## Full end-to-end accuracy by notation type (name + timing + food)\n")
    lines.append("| Pattern type | n | Full pipeline | Naive baseline |")
    lines.append("|---|---|---|---|")
    for p, d in sorted(summary["by_pattern_type"].items()):
        lines.append(f"| {p} | {d['n']} | {d['full_pipeline_accuracy']*100:.1f}% | {d['baseline_full_accuracy']*100:.1f}% |")

    lines.append("\n## Confidence calibration\n")
    lines.append("| Confidence bucket | n | Mean reported confidence | Empirical accuracy |")
    lines.append("|---|---|---|---|")
    for c in summary["confidence_calibration"]:
        lines.append(f"| {c['bucket']} | {c['n']} | {c['mean_confidence']:.2f} | {c['empirical_accuracy']*100:.1f}% |")

    lines.append("\n## Figures\n")
    lines.append("![Accuracy by degradation](accuracy_by_degradation.png)\n")
    lines.append("![Full vs baseline by pattern](full_vs_baseline.png)\n")
    lines.append("![Confidence calibration](confidence_calibration.png)\n")

    lines.append("\n## Caveats\n")
    lines.append(
        "This is a small, synthetic, English-only benchmark built to exercise every rule "
        "branch in the parser under controlled, reproducible image degradation. It is "
        "useful for regression testing and for relative comparisons (this version vs. "
        "that version; full parser vs. baseline). It is **not** a substitute for "
        "evaluation on a large, de-identified sample of real prescription photos across "
        "handwriting styles, printers, languages, and camera hardware, which would be "
        "necessary before relying on these numbers for any real-world deployment."
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("Building benchmark images (cached if already present)...")
    manifest = build_benchmark()
    print(f"Benchmark ready: {len(manifest)} images.\n")

    print("Running OCR + parsing pipeline over the benchmark...")
    start = time.time()
    rows = []
    for i, entry in enumerate(manifest, start=1):
        print(f"  [{i}/{len(manifest)}] {entry['id']} ({entry['degradation_level']})")
        rows.append(evaluate_case(entry))
    elapsed = time.time() - start
    print(f"Done in {elapsed:.1f}s.\n")

    summary = summarize(rows)

    write_csv(rows, os.path.join(RESULTS_DIR, "per_image_results.csv"))
    with open(os.path.join(RESULTS_DIR, "metrics_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    plot_accuracy_by_degradation(summary, os.path.join(RESULTS_DIR, "accuracy_by_degradation.png"))
    plot_full_vs_baseline_by_pattern(summary, os.path.join(RESULTS_DIR, "full_vs_baseline.png"))
    plot_confidence_calibration(summary, os.path.join(RESULTS_DIR, "confidence_calibration.png"))
    write_results_markdown(summary, os.path.join(RESULTS_DIR, "RESULTS.md"), elapsed)

    print(f"Overall full-pipeline accuracy: {summary['overall_full_pipeline_accuracy']*100:.1f}%")
    print(f"Overall baseline accuracy:      {summary['overall_baseline_accuracy']*100:.1f}%")
    print(f"Mean OCR CER:                   {summary['overall_mean_ocr_cer']*100:.1f}%")
    print(f"\nFull results written to: {RESULTS_DIR}")


if __name__ == "__main__":
    main()
