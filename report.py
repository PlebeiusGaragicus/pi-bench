#!/usr/bin/env python3
"""Generate a markdown report and plots from benchmark parsed results."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
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


def collect_records(run_dir: Path, allowed_keys: set[tuple[str, str, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for parsed_path in sorted((run_dir / "artifacts").glob("*/parsed.json")):
        record = read_json(parsed_path)
        key = (str(record.get("case_id")), str(record.get("model")), str(record.get("reasoning", "off")))
        if key in allowed_keys:
            records.append(record)
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
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})


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
        key = f"{record.get('model')} / thinking={record.get('reasoning')}"
        groups[key].append(float(record["score"]))
    return dict(groups)


def maybe_write_plots(records: list[dict[str, Any]], plots_dir: Path) -> list[str]:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        return []

    groups = grouped_scores(records)
    if not groups:
        return []

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
    return [str(output.relative_to(plots_dir.parent))]


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

    if plot_paths:
        lines.extend(["", "## Plots", ""])
        for plot_path in plot_paths:
            lines.append(f"![{plot_path}]({plot_path})")

    if records:
        lines.extend(["", "## Case Results", ""])
        lines.extend(["| Case | Model | Status | Score | Description |", "|---|---|---|---:|---|"])
        for record in sorted(records, key=lambda item: (item.get("case_id", ""), item.get("model", ""))):
            desc = str(record.get("description", "")).replace("|", "\\|")
            status = str(record.get("status") or "ok")
            lines.append(
                f"| `{record.get('case_id')}` | `{record.get('model')}` | {status} | {record.get('score')} | {desc} |"
            )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()

    config_path = args.config.resolve()
    run_dir = config_path.parent
    config = load_yaml(config_path)
    if not isinstance(config, dict):
        raise SystemExit("Config must be a YAML mapping")

    records = collect_records(run_dir, current_matrix_keys(config, config_path.parent))
    write_jsonl(records, run_dir / "results.collated.jsonl")
    write_csv(records, run_dir / "results.csv")
    plot_paths = maybe_write_plots(records, run_dir / "plots")
    (run_dir / "report.md").write_text(markdown_report(config, records, plot_paths), encoding="utf-8")

    print(f"Wrote report: {run_dir / 'report.md'}")
    print(f"Records: {len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
