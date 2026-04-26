#!/usr/bin/env python3
"""Generate a markdown report and plots from benchmark parsed results."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from yaml_loader import load_yaml


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(config_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_dir / path).resolve()


def current_matrix_keys(config: dict[str, Any], config_dir: Path) -> set[tuple[str, str, str]]:
    case_file = resolve_path(config_dir, str(config["case_file"]))
    case_data = load_yaml(case_file)
    cases = case_data.get("cases", []) if isinstance(case_data, dict) else []
    models = config.get("models", [])
    keys: set[tuple[str, str, str]] = set()
    for case in cases:
        if not isinstance(case, dict):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            keys.add((str(case.get("id")), str(model.get("id")), str(model.get("reasoning", "off"))))
    return keys


def parse_timestamp(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def seconds_between(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, end - start)


def output_elapsed(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("elapsed_seconds")
    if isinstance(value, int | float):
        return float(value)
    return None


def manifest_timings(run_dir: Path) -> dict[str, dict[str, float]]:
    manifest_path = run_dir / "manifest.jsonl"
    timestamps: dict[str, dict[str, float]] = defaultdict(dict)
    if not manifest_path.exists():
        return {}

    with manifest_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or not event.get("item_id"):
                continue
            ts = parse_timestamp(event.get("ts"))
            if ts is None:
                continue
            state = str(event.get("state") or "")
            timestamps[str(event["item_id"])][state] = ts

    timings: dict[str, dict[str, float]] = {}
    for item_id, states in timestamps.items():
        timing: dict[str, float] = {}
        for output_field, start_state, end_state in [
            ("answer_seconds", "answer_running", "answer_complete"),
            ("judge_seconds", "judge_running", "judge_complete"),
            ("parse_seconds", "judge_complete", "parsed"),
            ("item_seconds", "answer_running", "complete"),
        ]:
            value = seconds_between(states.get(start_state), states.get(end_state))
            if value is not None:
                timing[output_field] = value
        if timing:
            timings[item_id] = timing
    return timings


def artifact_timing(artifact_dir: Path) -> dict[str, float]:
    timing: dict[str, float] = {}
    answer_seconds = output_elapsed(artifact_dir / "answer" / "output.json")
    judge_seconds = output_elapsed(artifact_dir / "judge" / "output.json")
    if answer_seconds is not None:
        timing["answer_seconds"] = answer_seconds
    if judge_seconds is not None:
        timing["judge_seconds"] = judge_seconds
    return timing


def with_timing_backfill(record: dict[str, Any], artifact_dir: Path, manifest_fallback: dict[str, dict[str, float]]) -> dict[str, Any]:
    fallback = manifest_fallback.get(str(record.get("item_id")), {}) | artifact_timing(artifact_dir)
    existing = record.get("timing")
    if isinstance(existing, dict):
        fallback.update({key: float(value) for key, value in existing.items() if isinstance(value, int | float)})
    if not fallback:
        return record
    return {**record, "timing": fallback}


def collect_records(run_dir: Path, allowed_keys: set[tuple[str, str, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    manifest_fallback = manifest_timings(run_dir)
    for parsed_path in sorted((run_dir / "artifacts").glob("*/parsed.json")):
        record = read_json(parsed_path)
        key = (str(record.get("case_id")), str(record.get("model")), str(record.get("reasoning", "off")))
        if key in allowed_keys:
            records.append(with_timing_backfill(record, parsed_path.parent, manifest_fallback))
    return records


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "benchmark_name",
        "run_id",
        "case_id",
        "model",
        "reasoning",
        "judge_model",
        "status",
        "phase",
        "score",
        "description",
        "error",
        "exit_code",
        "answer_seconds",
        "judge_seconds",
        "parse_seconds",
        "item_seconds",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = {field: record.get(field, "") for field in fields}
            row.update(
                {
                    "answer_seconds": timing_value(record, "answer_seconds"),
                    "judge_seconds": timing_value(record, "judge_seconds"),
                    "parse_seconds": timing_value(record, "parse_seconds"),
                    "item_seconds": timing_value(record, "item_seconds"),
                }
            )
            writer.writerow(row)


def write_jsonl(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, sort_keys=True) + "\n")


def grouped_scores(records: list[dict[str, Any]]) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record.get("status") == "error" or record.get("score") in {"", None}:
            continue
        groups[model_label(record)].append(float(record["score"]))
    return dict(groups)


def model_label(record: dict[str, Any]) -> str:
    return f"{record.get('model')} / thinking={record.get('reasoning')}"


def reasoning_sort_key(value: str) -> tuple[int, str]:
    v = str(value)
    order = {"off": 0, "low": 1, "medium": 2, "high": 3}
    return (order.get(v.lower(), 99), v)


def case_results_markdown(records: list[dict[str, Any]]) -> list[str]:
    lines = [
        "Collated evaluation records: [results.collated.jsonl](results.collated.jsonl).",
        "",
    ]
    models = sorted({str(record.get("model", "")) for record in records})
    for model in models:
        model_records = [record for record in records if str(record.get("model", "")) == model]
        reasonings = sorted(
            {str(record.get("reasoning", "off")) for record in model_records},
            key=reasoning_sort_key,
        )
        lines.append(f"### `{model}`")
        lines.append("")
        for reasoning in reasonings:
            slice_records = [
                record for record in model_records if str(record.get("reasoning", "off")) == reasoning
            ]
            lines.append(f"#### `thinking={reasoning}`")
            lines.append("")
            lines.extend(
                [
                    "| Case | Status | Score | Item Seconds | Description |",
                    "|---|---|---:|---:|---|",
                ]
            )
            for record in sorted(slice_records, key=lambda item: str(item.get("case_id", ""))):
                desc = str(record.get("description", "")).replace("|", "\\|")
                status = str(record.get("status") or "ok")
                item_seconds = format_seconds(timing_value(record, "item_seconds"))
                lines.append(
                    f"| `{record.get('case_id')}` | {status} | {record.get('score')} | {item_seconds} | {desc} |"
                )
            lines.append("")
    return lines


def timing_value(record: dict[str, Any], field: str) -> float | str:
    timing = record.get("timing")
    if not isinstance(timing, dict):
        return ""
    value = timing.get(field)
    if isinstance(value, int | float):
        return float(value)
    return ""


def grouped_timings(records: list[dict[str, Any]], field: str) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for record in records:
        value = timing_value(record, field)
        if isinstance(value, float):
            groups[model_label(record)].append(value)
    return dict(groups)


def timing_totals(records: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for field in ["answer_seconds", "judge_seconds", "parse_seconds", "item_seconds"]:
        values = [timing_value(record, field) for record in records]
        totals[field] = sum(value for value in values if isinstance(value, float))
    return totals


def format_seconds(value: float | str) -> str:
    if value == "":
        return ""
    return f"{float(value):.2f}"


def average_seconds(values: list[float]) -> str:
    if not values:
        return ""
    return f"{mean(values):.2f}"


def maybe_write_plots(records: list[dict[str, Any]], plots_dir: Path) -> list[str]:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        return []

    plot_paths: list[str] = []
    groups = grouped_scores(records)
    if groups:
        plots_dir.mkdir(parents=True, exist_ok=True)
        labels = list(groups)
        averages = [mean(groups[label]) for label in labels]

        width = max(8, len(labels) * 1.2)
        fig, ax = plt.subplots(figsize=(width, 4.5))
        ax.bar(labels, averages)
        ax.set_ylim(0, 2)
        ax.set_ylabel("Average Score")
        ax.set_title("Average BullshitBench Score By Model")
        ax.tick_params(axis="x", labelrotation=30)
        fig.tight_layout()

        output = plots_dir / "average-score-by-model.png"
        fig.savefig(output)
        plt.close(fig)
        plot_paths.append(str(output.relative_to(plots_dir.parent)))

    phase_fields = ["answer_seconds", "judge_seconds", "parse_seconds"]
    phase_totals = timing_totals(records)
    if any(phase_totals[field] for field in phase_fields):
        plots_dir.mkdir(parents=True, exist_ok=True)
        labels = ["answer", "judge", "parse"]
        totals = [phase_totals[field] for field in phase_fields]
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.bar(labels, totals)
        ax.set_ylabel("Total Seconds")
        ax.set_title("Total Runtime By Phase")
        fig.tight_layout()

        output = plots_dir / "total-runtime-by-phase.png"
        fig.savefig(output)
        plt.close(fig)
        plot_paths.append(str(output.relative_to(plots_dir.parent)))

    item_groups = grouped_timings(records, "item_seconds")
    if item_groups:
        plots_dir.mkdir(parents=True, exist_ok=True)
        labels = list(item_groups)
        averages = [mean(item_groups[label]) for label in labels]
        width = max(8, len(labels) * 1.2)
        fig, ax = plt.subplots(figsize=(width, 4.5))
        ax.bar(labels, averages)
        ax.set_ylabel("Average Seconds")
        ax.set_title("Average Item Runtime By Model")
        ax.tick_params(axis="x", labelrotation=30)
        fig.tight_layout()

        output = plots_dir / "average-runtime-by-model.png"
        fig.savefig(output)
        plt.close(fig)
        plot_paths.append(str(output.relative_to(plots_dir.parent)))

    return plot_paths


def markdown_report(config: dict[str, Any], records: list[dict[str, Any]], plot_paths: list[str]) -> str:
    benchmark_name = config.get("benchmark_name", "benchmark")
    run_id = config.get("run_id", "run")
    groups = grouped_scores(records)
    errors = [record for record in records if record.get("status") == "error"]

    lines = [
        f"# {benchmark_name} Report",
        "",
        f"Run: `{run_id}`",
        "",
        f"Result records: {len(records)}",
        f"Errors: {len(errors)}",
        "",
        "## Summary",
        "",
    ]

    if not groups:
        lines.append("No scored results found.")
    else:
        lines.extend(
            [
                "| Model | Cases | Average Score |",
                "|---|---:|---:|",
            ]
        )
        for label, scores in sorted(groups.items()):
            lines.append(f"| `{label}` | {len(scores)} | {mean(scores):.2f} |")

    totals = timing_totals(records)
    if any(totals.values()):
        lines.extend(
            [
                "",
                "## Timing",
                "",
                "| Phase | Total Seconds |",
                "|---|---:|",
                f"| Answer | {totals['answer_seconds']:.2f} |",
                f"| Judge | {totals['judge_seconds']:.2f} |",
                f"| Parse | {totals['parse_seconds']:.2f} |",
                f"| Item total | {totals['item_seconds']:.2f} |",
                "",
                "| Model | Cases | Avg Answer Seconds | Avg Judge Seconds | Avg Parse Seconds | Avg Item Seconds |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        labels = sorted({model_label(record) for record in records})
        for label in labels:
            matching = [record for record in records if model_label(record) == label]
            answer = [timing_value(record, "answer_seconds") for record in matching]
            judge = [timing_value(record, "judge_seconds") for record in matching]
            parse = [timing_value(record, "parse_seconds") for record in matching]
            item = [timing_value(record, "item_seconds") for record in matching]
            answer_values = [value for value in answer if isinstance(value, float)]
            judge_values = [value for value in judge if isinstance(value, float)]
            parse_values = [value for value in parse if isinstance(value, float)]
            item_values = [value for value in item if isinstance(value, float)]
            lines.append(
                "| "
                f"`{label}` | {len(matching)} | "
                f"{average_seconds(answer_values)} | "
                f"{average_seconds(judge_values)} | "
                f"{average_seconds(parse_values)} | "
                f"{average_seconds(item_values)} |"
            )

    if plot_paths:
        lines.extend(["", "## Plots", ""])
        for plot_path in plot_paths:
            lines.append(f"![{plot_path}]({plot_path})")

    if records:
        lines.extend(["", "## Case Results", ""])
        lines.extend(case_results_markdown(records))

    lines.append("")
    return "\n".join(lines)


def generate_report(config_path: Path) -> dict[str, Any]:
    config_path = config_path.resolve()
    run_dir = config_path.parent
    config = load_yaml(config_path)
    if not isinstance(config, dict):
        raise SystemExit("Config must be a YAML mapping")

    records = collect_records(run_dir, current_matrix_keys(config, config_path.parent))
    write_jsonl(records, run_dir / "results.collated.jsonl")
    write_csv(records, run_dir / "results.csv")
    plot_paths = maybe_write_plots(records, run_dir / "plots")
    report_path = run_dir / "report.md"
    report_path.write_text(markdown_report(config, records, plot_paths), encoding="utf-8")
    return {"report_path": report_path, "records": len(records), "plots": plot_paths}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()

    summary = generate_report(args.config)
    print(f"Wrote report: {summary['report_path']}")
    print(f"Records: {summary['records']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
