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


def read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def resolve_path(config_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_dir / path).resolve()


def current_matrix_keys(config: dict[str, Any], config_dir: Path) -> set[tuple[str, str, str, str]]:
    case_file = resolve_path(config_dir, str(config["case_file"]))
    case_data = load_yaml(case_file)
    cases = case_data.get("cases", []) if isinstance(case_data, dict) else []
    models = config.get("models", [])
    answer_prompts = config.get("answer_prompts", [])
    keys: set[tuple[str, str, str, str]] = set()
    for case in cases:
        if not isinstance(case, dict):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            for prompt_id in answer_prompts:
                keys.add(
                    (
                        str(case.get("id")),
                        str(model.get("id")),
                        str(model.get("reasoning", "off")),
                        str(prompt_id),
                    )
                )
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


def collect_records(run_dir: Path, allowed_keys: set[tuple[str, str, str, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    manifest_fallback = manifest_timings(run_dir)
    for parsed_path in sorted((run_dir / "artifacts").glob("*/parsed.json")):
        record = read_json(parsed_path)
        key = (
            str(record.get("case_id")),
            str(record.get("model")),
            str(record.get("reasoning", "off")),
            str(record.get("answer_prompt_id")),
        )
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
        "answer_prompt_id",
        "answer_prompt_description",
        "answer_prompt_sha256",
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


def grouped_prompt_scores(records: list[dict[str, Any]]) -> dict[str, list[float]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record.get("status") == "error" or record.get("score") in {"", None}:
            continue
        groups[prompt_label(record)].append(float(record["score"]))
    return dict(groups)


def model_label(record: dict[str, Any]) -> str:
    return f"{record.get('model')} / thinking={record.get('reasoning')} / prompt={prompt_label(record)}"


def short_model_name(model: Any) -> str:
    return str(model or "").rstrip("/").split("/")[-1]


def plot_matrix_label(record: dict[str, Any]) -> str:
    model = short_model_name(record.get("model"))
    reasoning = str(record.get("reasoning") or "off")
    prompt = prompt_label(record)
    return f"{model} | {reasoning} | {prompt}"


def plot_labels_for_records(records: list[dict[str, Any]], labels: list[str]) -> list[str]:
    records_by_label: dict[str, dict[str, Any]] = {}
    for record in records:
        label = model_label(record)
        if label not in records_by_label:
            records_by_label[label] = record

    display_labels = [plot_matrix_label(records_by_label[label]) for label in labels]
    counts: dict[str, int] = defaultdict(int)
    for display_label in display_labels:
        counts[display_label] += 1

    return [
        display_label if counts[display_label] == 1 else full_label
        for display_label, full_label in zip(display_labels, labels, strict=True)
    ]


def prompt_label(record: dict[str, Any]) -> str:
    return str(record.get("answer_prompt_id") or "")


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
            reasoning_records = [
                record for record in model_records if str(record.get("reasoning", "off")) == reasoning
            ]
            lines.append(f"#### `thinking={reasoning}`")
            lines.append("")
            prompt_ids = sorted({prompt_label(record) for record in reasoning_records})
            for prompt_id in prompt_ids:
                slice_records = [
                    record for record in reasoning_records if prompt_label(record) == prompt_id
                ]
                lines.append(f"##### `prompt={prompt_id}`")
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


def artifact_dir_for_record(run_dir: Path, record: dict[str, Any]) -> Path:
    return run_dir / "artifacts" / str(record.get("item_id") or "")


def output_usage_summary(output_path: Path) -> str:
    try:
        output = read_json(output_path)
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(output, dict):
        return ""
    metadata = output.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    usage = metadata.get("usage")
    if not isinstance(usage, dict):
        return ""
    parts = []
    for label, field in [("input", "input"), ("output", "output"), ("total", "totalTokens")]:
        value = usage.get(field)
        if isinstance(value, int | float):
            parts.append(f"{label}={value}")
    return ", ".join(parts)


def thoughts_from_session_dir(session_dir: Path) -> str:
    for session_path in sorted(session_dir.glob("*.jsonl")):
        final_message: dict[str, Any] | None = None
        try:
            lines = session_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "message":
                continue
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                final_message = message
        if final_message is None:
            continue
        chunks = []
        for item in final_message.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "thinking":
                chunks.append(str(item.get("thinking") or item.get("text") or ""))
        thoughts = "\n".join(chunk for chunk in chunks if chunk).strip()
        if thoughts:
            return thoughts
    return ""


def student_output_markdown(config: dict[str, Any], records: list[dict[str, Any]], run_dir: Path) -> str:
    benchmark_name = config.get("benchmark_name", "benchmark")
    run_id = config.get("run_id", "run")
    lines = [
        f"# {benchmark_name} Student Output",
        "",
        f"Run: `{run_id}`",
        "",
        "This report collects answer-model prompts, captured thoughts, and final answers for quick researcher review.",
        "",
    ]

    models = sorted({str(record.get("model", "")) for record in records})
    for model in models:
        model_records = [record for record in records if str(record.get("model", "")) == model]
        lines.extend([f"## `{model}`", ""])
        reasonings = sorted({str(record.get("reasoning", "off")) for record in model_records}, key=reasoning_sort_key)
        for reasoning in reasonings:
            reasoning_records = [record for record in model_records if str(record.get("reasoning", "off")) == reasoning]
            lines.extend([f"### `thinking={reasoning}`", ""])
            prompt_ids = sorted({prompt_label(record) for record in reasoning_records})
            for prompt_id in prompt_ids:
                prompt_records = [record for record in reasoning_records if prompt_label(record) == prompt_id]
                description = str(prompt_records[0].get("answer_prompt_description") or "")
                lines.extend([f"#### `prompt={prompt_id}`", ""])
                if description:
                    lines.extend([description, ""])
                for record in sorted(prompt_records, key=lambda item: str(item.get("case_id", ""))):
                    artifact_dir = artifact_dir_for_record(run_dir, record)
                    answer_dir = artifact_dir / "answer"
                    answer_output_path = answer_dir / "output.json"
                    system_prompt = read_text_if_exists(answer_dir / "system-prompt.md")
                    thoughts = read_text_if_exists(answer_dir / "thoughts.txt")
                    if not thoughts:
                        try:
                            answer_output = read_json(answer_output_path)
                        except (OSError, json.JSONDecodeError):
                            answer_output = {}
                        if isinstance(answer_output, dict):
                            thoughts = str(answer_output.get("thoughts") or "").strip()
                    if not thoughts:
                        thoughts = thoughts_from_session_dir(answer_dir / "sessions")
                    answer = read_text_if_exists(answer_dir / "answer.txt")
                    usage = output_usage_summary(answer_output_path)
                    status = str(record.get("status") or "ok")
                    score = record.get("score", "")
                    item_seconds = format_seconds(timing_value(record, "item_seconds"))

                    lines.extend(
                        [
                            f"##### `{record.get('case_id')}`",
                            "",
                            "**Context**",
                            "",
                            f"- Status: `{status}`",
                            f"- Score: `{score}`",
                            f"- Model: `{record.get('model')}`",
                            f"- Reasoning: `{record.get('reasoning')}`",
                            f"- Answer prompt: `{record.get('answer_prompt_id')}`",
                            f"- Answer prompt SHA-256: `{record.get('answer_prompt_sha256')}`",
                            f"- Judge: `{record.get('judge_model')}` / reasoning=`{record.get('judge_reasoning')}`",
                            f"- Item seconds: `{item_seconds}`",
                        ]
                    )
                    if usage:
                        lines.append(f"- Answer usage: `{usage}`")
                    lines.append("")

                    lines.extend(["**System Prompt**", ""])
                    lines.extend(fenced_block(system_prompt or "_No system prompt captured._", "text"))
                    lines.extend(["", "**Test Prompt**", ""])
                    lines.extend(fenced_block(str(record.get("question") or ""), "text"))
                    lines.extend(["", "**Thoughts**", ""])
                    lines.extend(fenced_block(thoughts or "_No thoughts captured._", "text"))
                    lines.extend(["", "**Answer**", ""])
                    lines.extend(fenced_block(answer or "_No answer captured._", "markdown"))
                    lines.append("")

    return "\n".join(lines)


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


def fenced_block(value: str, language: str = "text") -> list[str]:
    fence = "```"
    while fence in value:
        fence += "`"
    return [f"{fence}{language}", value.rstrip() or "_Empty._", fence]


def average_seconds(values: list[float]) -> str:
    if not values:
        return ""
    return f"{mean(values):.2f}"

# poorly formatted
# def left_align_y_tick_labels(ax: Any) -> None:
#     for label in ax.get_yticklabels():
#         label.set_horizontalalignment("left")
#         label.set_x(-0.02)


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
        display_labels = plot_labels_for_records(records, labels)
        averages = [mean(groups[label]) for label in labels]

        height = max(4.5, len(labels) * 0.6)
        fig, ax = plt.subplots(figsize=(10, height))
        ax.barh(display_labels, averages)
        ax.set_xlim(0, 2)
        ax.set_xlabel("Average Score")
        ax.set_title("Average BullshitBench Score By Matrix Item")
        ax.invert_yaxis()
        # left_align_y_tick_labels(ax)
        fig.tight_layout()

        output = plots_dir / "average-score-by-matrix-item.png"
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
        display_labels = plot_labels_for_records(records, labels)
        averages = [mean(item_groups[label]) for label in labels]
        height = max(4.5, len(labels) * 0.6)
        fig, ax = plt.subplots(figsize=(10, height))
        ax.barh(display_labels, averages)
        ax.set_xlabel("Average Seconds")
        ax.set_title("Average Item Runtime By Matrix Item")
        ax.invert_yaxis()
        # left_align_y_tick_labels(ax)
        fig.tight_layout()

        output = plots_dir / "average-runtime-by-matrix-item.png"
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
                "| Matrix Item | Cases | Average Score |",
                "|---|---:|---:|",
            ]
        )
        for label, scores in sorted(groups.items()):
            lines.append(f"| `{label}` | {len(scores)} | {mean(scores):.2f} |")

    prompt_groups = grouped_prompt_scores(records)
    if len(prompt_groups) > 1:
        lines.extend(
            [
                "",
                "## Prompt Comparison",
                "",
                "| Answer Prompt | Cases | Average Score |",
                "|---|---:|---:|",
            ]
        )
        for label, scores in sorted(prompt_groups.items()):
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
                "| Matrix Item | Cases | Avg Answer Seconds | Avg Judge Seconds | Avg Parse Seconds | Avg Item Seconds |",
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
    student_output_path = run_dir / "student_output.md"
    student_output_path.write_text(student_output_markdown(config, records, run_dir), encoding="utf-8")
    return {"report_path": report_path, "student_output_path": student_output_path, "records": len(records), "plots": plot_paths}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()

    summary = generate_report(args.config)
    print(f"Wrote report: {summary['report_path']}")
    print(f"Wrote student output: {summary['student_output_path']}")
    print(f"Records: {summary['records']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
