#!/usr/bin/env python3
import argparse
import json
import os
import resource
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ITEMS = ROOT / "eval" / "items.jsonl"
DEFAULT_PROFILES = ROOT / "profiles"


def load_profiles(path):
    profiles = {}
    for file in sorted(Path(path).glob("*.yaml")):
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        categories = [c for c in data["categories"] if c.get("enabled", True)]
        if len(categories) > 64:
            raise SystemExit(f"{file}: {len(categories)} categories exceeds tev1 limit of 64")
        data["categories"] = categories
        profiles[data["id"]] = data
    if not profiles:
        raise SystemExit(f"no profile YAML files found in {path}")
    return profiles


def load_items(path):
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(json.loads(line))
    return items


def validate(items, profiles):
    problems = []
    for item in items:
        profile = profiles.get(item["profile"])
        if profile is None:
            problems.append(f"{item['id']}: unknown profile {item['profile']}")
            continue
        ids = {c["id"] for c in profile["categories"]}
        unknown = set(item["labels"]) - ids
        if unknown:
            problems.append(f"{item['id']}: unknown labels {sorted(unknown)}")
        if not item.get("title"):
            problems.append(f"{item['id']}: empty title")
    return problems


def build_state(item, max_chars):
    state = f"{item['title']}\n\n{item['summary']}".strip()
    if len(state) > max_chars:
        state = state[:max_chars].rsplit(" ", 1)[0]
    return state


def build_questions(profile):
    questions = {}
    for category in profile["categories"]:
        questions[category["id"]] = {
            "type": "noul",
            "instructions": (
                f"Does this item focus on {category['name']}? "
                f"{category['description']}"
            ),
            "criteria": {
                "true": category["criteria_true"],
                "false": category["criteria_false"],
            },
        }
    return questions


def normalize_probability(answer):
    if isinstance(answer, bool):
        return 1.0 if answer else 0.0
    if isinstance(answer, (int, float)):
        return float(answer)
    if isinstance(answer, dict):
        for key in ("noul", "probability", "score"):
            if key in answer:
                value = answer[key]
                if isinstance(value, bool):
                    return 1.0 if value else 0.0
                return float(value)
    raise ValueError(f"unsupported answer shape: {answer!r}")


class Client:
    def __init__(self, base_url, model, timeout, retries):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()

    def preflight(self):
        try:
            version = self.session.get(f"{self.base_url}/api/version", timeout=10)
            version.raise_for_status()
            tags = self.session.get(f"{self.base_url}/api/tags", timeout=10)
            tags.raise_for_status()
        except requests.RequestException as exc:
            raise SystemExit(
                f"cannot reach Ollama at {self.base_url}: {exc}\n"
                "Start it with `ollama serve` and try again."
            )
        names = {m.get("name") for m in tags.json().get("models", [])}
        if self.model not in names:
            raise SystemExit(
                f"model {self.model} is not present locally.\n"
                f"Run: ollama pull {self.model}"
            )
        return version.json()

    def decide(self, state, questions):
        payload = {
            "model": self.model,
            "state": state,
            "questions": questions,
            "keep_alive": "30m",
        }
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}/v1/systemone",
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code in (429, 500, 502, 503, 504):
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    time.sleep(1.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                body = response.json()
                if "answers" not in body:
                    raise ValueError(f"missing answers field: {body!r}")
                return {
                    key: normalize_probability(value)
                    for key, value in body["answers"].items()
                }
            except (requests.RequestException, ValueError) as exc:
                last_error = str(exc)
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"decision failed after {self.retries + 1} attempts: {last_error}")


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def score_item(profile, probabilities, threshold):
    labels = [
        c["id"]
        for c in profile["categories"]
        if probabilities.get(c["id"], 0.0) >= threshold
    ]
    weight = {c["id"]: float(c.get("weight", 1.0)) for c in profile["categories"]}
    best = max((probabilities[cid] * weight[cid] for cid in labels), default=0.0)
    return labels, best


def category_metrics(items, predictions, categories):
    rows = []
    for category in categories:
        cid = category["id"]
        tp = fp = fn = 0
        for item in items:
            gold = cid in item["labels"]
            pred = cid in predictions[item["id"]]["labels"]
            tp += gold and pred
            fp += (not gold) and pred
            fn += gold and (not pred)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append(
            {
                "id": cid,
                "name": category["name"],
                "weight": float(category.get("weight", 1.0)),
                "support": tp + fn,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )
    return rows


def aggregate(items, predictions, rows, profile):
    macro_rows = [r for r in rows if r["support"] > 0]
    macro_f1 = statistics.fmean(r["f1"] for r in macro_rows) if macro_rows else 0.0
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    micro_precision = tp / (tp + fp) if tp + fp else 0.0
    micro_recall = tp / (tp + fn) if tp + fn else 0.0
    micro_f1 = (
        2 * micro_precision * micro_recall / (micro_precision + micro_recall)
        if micro_precision + micro_recall
        else 0.0
    )
    ranked = sorted(
        items, key=lambda item: predictions[item["id"]]["score"], reverse=True
    )[:10]
    relevant = sum(1 for item in ranked if item["labels"])
    precision_at_10 = relevant / 10.0 if items else 0.0
    none_items = [i for i in items if not i["labels"]]
    none_correct = sum(
        1
        for i in none_items
        if not predictions[i["id"]]["labels"]
    )
    brier_values = []
    for item in items:
        for category in profile["categories"]:
            cid = category["id"]
            truth = 1.0 if cid in item["labels"] else 0.0
            brier_values.append((predictions[item["id"]]["probabilities"][cid] - truth) ** 2)
    return {
        "items": len(items),
        "macro_f1": macro_f1,
        "micro_f1": micro_f1,
        "micro_precision": micro_precision,
        "micro_recall": micro_recall,
        "precision_at_10": precision_at_10,
        "brier": statistics.fmean(brier_values) if brier_values else 0.0,
        "gt_none_items": len(none_items),
        "gt_none_predicted_none": none_correct,
        "categories_with_support": len(macro_rows),
        "categories_without_support": [r["id"] for r in rows if r["support"] == 0],
    }


def run_live(args, profiles, items):
    client = Client(args.base_url, args.model, args.timeout, args.retries)
    version = client.preflight()
    predictions = {}
    latencies = []
    failures = []
    total = len(items)
    started0 = time.perf_counter()
    for index, item in enumerate(items, 1):
        profile = profiles[item["profile"]]
        state = build_state(item, args.state_chars)
        questions = build_questions(profile)
        started = time.perf_counter()
        try:
            probabilities = client.decide(state, questions)
            missing_answers = set(questions) - set(probabilities)
            if missing_answers:
                raise ValueError(f"missing answers: {sorted(missing_answers)}")
        except Exception as exc:
            failures.append({"id": item["id"], "error": str(exc)})
            print(f"[{index}/{total}] FAIL {item['id']}: {exc}", file=sys.stderr)
            continue
        elapsed = time.perf_counter() - started
        latencies.append(elapsed)
        labels, score = score_item(profile, probabilities, args.threshold)
        predictions[item["id"]] = {
            "labels": labels,
            "score": score,
            "probabilities": probabilities,
            "latency_s": elapsed,
        }
        if index % 10 == 0 or index == total:
            print(f"[{index}/{total}] {item['id']} {elapsed:.2f}s", file=sys.stderr)
    elapsed0 = time.perf_counter() - started0
    print(f"Total time: {elapsed0:.2f}s", file=sys.stderr)

    results = {}
    for profile_id, profile in profiles.items():
        subset = [i for i in items if i["profile"] == profile_id]
        if not subset:
            continue
        missing = [i["id"] for i in subset if i["id"] not in predictions]
        if missing:
            results[profile_id] = {"error": "incomplete", "missing": missing}
            continue
        rows = category_metrics(subset, predictions, profile["categories"])
        results[profile_id] = {
            "profile": profile["name"],
            "threshold": args.threshold,
            **aggregate(subset, predictions, rows, profile),
            "categories": rows,
        }

    harness_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "ollama_version": version.get("version"),
        "base_url": args.base_url,
        "threshold": args.threshold,
        "state_chars": args.state_chars,
        "items": len(items),
        "failures": failures,
        "latency_s": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies, default=0.0),
        },
        "harness_peak_rss_mb": harness_rss_mb,
        "profiles": results,
    }
    if failures:
        report["gate"] = {"passed": False, "reasons": ["request failures"]}
    elif args.gate:
        reasons = []
        for profile_id, result in results.items():
            if result.get("error"):
                reasons.append(f"{profile_id}: incomplete")
                continue
            if result["macro_f1"] < args.min_macro_f1:
                reasons.append(
                    f"{profile_id}: macro-F1 {result['macro_f1']:.3f} < {args.min_macro_f1}"
                )
            if result["precision_at_10"] < args.min_precision_at_10:
                reasons.append(
                    f"{profile_id}: precision@10 {result['precision_at_10']:.3f} < {args.min_precision_at_10}"
                )
        if report["latency_s"]["p95"] > args.max_p95:
            reasons.append(
                f"p95 latency {report['latency_s']['p95']:.2f}s > {args.max_p95}s"
            )
        report["gate"] = {"passed": not reasons, "reasons": reasons}
    return report


def print_report(report):
    print(f"\nmodel: {report['model']}  threshold: {report['threshold']}")
    print(
        f"items: {report['items']}  p50: {report['latency_s']['p50']:.2f}s  "
        f"p95: {report['latency_s']['p95']:.2f}s  harness RSS: {report['harness_peak_rss_mb']:.0f} MB"
    )
    for profile_id, result in report["profiles"].items():
        print(f"\n{profile_id} — {result.get('profile', '')}")
        if result.get("error"):
            print(f"  ERROR: {result['error']} {result.get('missing', '')}")
            continue
        print(
            f"  macro-F1 {result['macro_f1']:.3f}  micro-F1 {result['micro_f1']:.3f}  "
            f"P@10 {result['precision_at_10']:.2f}  Brier {result['brier']:.3f}"
        )
        print(
            f"  gt-none {result['gt_none_items']}  predicted-none correctly "
            f"{result['gt_none_predicted_none']}"
        )
        print(f"  {'category':24} {'sup':>3} {'P':>5} {'R':>5} {'F1':>5}")
        for row in sorted(result["categories"], key=lambda r: -r["support"]):
            print(
                f"  {row['id']:24} {row['support']:>3} "
                f"{row['precision']:>5.2f} {row['recall']:>5.2f} {row['f1']:>5.2f}"
            )
    if report.get("failures"):
        print(f"\nfailures: {len(report['failures'])}")
        for failure in report["failures"]:
            print(f"  {failure['id']}: {failure['error']}")
    if "gate" in report:
        gate = report["gate"]
        print(f"\ngate: {'PASS' if gate['passed'] else 'FAIL'}")
        for reason in gate["reasons"]:
            print(f"  - {reason}")


def dry_run(profiles, items):
    problems = validate(items, profiles)
    if problems:
        print("validation problems:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 2
    print(f"profiles: {', '.join(profiles)}")
    print(f"items: {len(items)}")
    for profile_id, profile in profiles.items():
        subset = [i for i in items if i["profile"] == profile_id]
        counts = {c["id"]: 0 for c in profile["categories"]}
        none_count = 0
        lengths = []
        for item in subset:
            lengths.append(len(build_state(item, 10_000)))
            if not item["labels"]:
                none_count += 1
            for label in item["labels"]:
                counts[label] += 1
        print(f"\n{profile_id}: {len(subset)} items, {none_count} with no labels")
        print(
            f"  state chars: min {min(lengths)} / median {int(statistics.median(lengths))} "
            f"/ max {max(lengths)}"
        )
        for category in profile["categories"]:
            count = counts[category["id"]]
            flag = "  <-- low support" if 0 < count < 2 else ""
            print(f"  {category['id']:24} {count:>3}{flag}")
        questions = build_questions(profile)
        print(f"  questions per request: {len(questions)}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Evaluate tev1 against the local profile eval set")
    parser.add_argument("--base-url", default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--model", default=os.environ.get("TEV_MODEL", "tev1:0.8b"))
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--items", type=Path, default=DEFAULT_ITEMS)
    parser.add_argument("--profiles-dir", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--profile", action="append", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--state-chars", type=int, default=1200)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--min-macro-f1", type=float, default=0.75)
    parser.add_argument("--min-precision-at-10", type=float, default=0.70)
    parser.add_argument("--max-p95", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    profiles = load_profiles(args.profiles_dir)
    if args.profile:
        profiles = {k: v for k, v in profiles.items() if k in args.profile}
        if not profiles:
            raise SystemExit(f"no matching profiles: {args.profile}")
    items = load_items(args.items)
    items = [i for i in items if i["profile"] in profiles]
    if args.limit:
        items = items[: args.limit]
    if not items:
        raise SystemExit("no eval items loaded")

    if args.dry_run:
        return dry_run(profiles, items)

    report = run_live(args, profiles, items)
    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    if report.get("failures"):
        return 2
    if "gate" in report and not report["gate"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
