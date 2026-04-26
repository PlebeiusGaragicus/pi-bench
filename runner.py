#!/usr/bin/env python3
"""Run a single-turn pi benchmark and judge the answers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from report import generate_report
from yaml_loader import load_yaml


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def elapsed_since(started: float) -> float:
    return time.monotonic() - started


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(value, sort_keys=True) + "\n")


class RunLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        line = f"{utc_now()} {message}"
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def resolve_path(config_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_dir / path).resolve()


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    return value.strip("-") or "item"


def stable_item_id(case_id: str, model_id: str, reasoning: str) -> str:
    base = f"{case_id}__{model_id}__{reasoning}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
    return f"{slugify(case_id)}__{slugify(model_id)}__{slugify(reasoning)}__{digest}"


def text_from_message(message: dict[str, Any]) -> str:
    chunks = []
    for item in message.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text", "")))
    return "\n".join(chunks).strip()


def compact_message_metadata(message: dict[str, Any]) -> dict[str, Any]:
    fields = ["api", "provider", "model", "usage", "stopReason", "timestamp", "responseId"]
    return {field: message[field] for field in fields if field in message}


def parse_final_output(event_stream: str) -> dict[str, Any]:
    final_message: dict[str, Any] | None = None
    event_count = 0
    for line in event_stream.splitlines():
        line = line.strip()
        if not line:
            continue
        event_count += 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "message_end":
            continue
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            continue
        final_message = message

    if final_message is None:
        return {"text": "", "metadata": {}, "event_count": event_count}
    return {
        "text": text_from_message(final_message),
        "metadata": compact_message_metadata(final_message),
        "event_count": event_count,
    }


def load_case_data(case_file: Path) -> dict[str, Any]:
    data = load_yaml(case_file)
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise SystemExit(f"Expected {case_file} to contain a top-level 'cases' list")
    return data


def load_cases_from_data(case_file: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for idx, case in enumerate(data["cases"], start=1):
        if not isinstance(case, dict):
            raise SystemExit(f"Case {idx} in {case_file} must be a mapping")
        case_id = str(case.get("id") or f"case-{idx}")
        question = str(case.get("question") or "").strip()
        if not question:
            raise SystemExit(f"Case {case_id} in {case_file} is missing 'question'")
        cases.append({**case, "id": case_id, "question": question})
    return cases


def load_cases(case_file: Path) -> list[dict[str, Any]]:
    return load_cases_from_data(case_file, load_case_data(case_file))


def expand_matrix(config: dict[str, Any], cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    models = config.get("models")
    if not isinstance(models, list) or not models:
        raise SystemExit("Config must contain a non-empty 'models' list")

    items: list[dict[str, Any]] = []
    for case in cases:
        for model in models:
            if not isinstance(model, dict) or not model.get("id"):
                raise SystemExit("Each model must be a mapping with an 'id'")
            reasoning = str(model.get("reasoning", "off"))
            item_id = stable_item_id(case["id"], str(model["id"]), reasoning)
            items.append(
                {
                    "item_id": item_id,
                    "case": case,
                    "model": model,
                    "reasoning": reasoning,
                }
            )
    return items


def replay_manifest(path: Path) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return states
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item_id = event.get("item_id")
            if item_id:
                states[str(item_id)] = event
    return states


def append_manifest(manifest_path: Path, item_id: str, state: str, **extra: Any) -> None:
    append_jsonl(manifest_path, {"ts": utc_now(), "item_id": item_id, "state": state, **extra})


def should_skip(
    item_id: str,
    states: dict[str, dict[str, Any]],
    resume: bool,
) -> bool:
    if not resume:
        return False
    state = (states.get(item_id) or {}).get("state")
    if state in {"complete", "failed"}:
        return True
    return False


def pi_args(
    prompt: str,
    system_prompt: str,
    model: str,
    reasoning: str,
    session_dir: Path,
    system_prompt_path: Path,
) -> list[str]:
    args = [
        "pi",
        "--mode",
        "json",
        "--no-tools",
        "--no-skills",
        "--no-prompt-templates",
        "--session-dir",
        str(session_dir),
        "--model",
        model,
    ]
    if reasoning:
        args.extend(["--thinking", reasoning])
    if system_prompt.strip():
        write_text(system_prompt_path, system_prompt)
        args.extend(["--append-system-prompt", str(system_prompt_path)])
    args.extend(["-p", prompt])
    return args


def run_pi(
    *,
    prompt: str,
    system_prompt: str,
    model: str,
    reasoning: str,
    artifact_dir: Path,
    dry_run_text: str | None,
) -> dict[str, Any]:
    session_dir = artifact_dir / "sessions"
    system_prompt_path = artifact_dir / "system-prompt.md"
    output_path = artifact_dir / "output.json"
    stderr_path = artifact_dir / "stderr.txt"
    args_path = artifact_dir / "args.json"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    session_dir.mkdir(parents=True, exist_ok=True)

    args = pi_args(prompt, system_prompt, model, reasoning, session_dir, system_prompt_path)
    write_json(args_path, args)

    if dry_run_text is not None:
        started = time.monotonic()
        elapsed = elapsed_since(started)
        output = {
            "text": dry_run_text,
            "metadata": {"dry_run": True},
            "event_count": 1,
            "elapsed_seconds": elapsed,
        }
        write_json(output_path, output)
        write_text(stderr_path, "[dry-run]\n")
        return {
            "exit_code": 0,
            "text": dry_run_text,
            "output": output,
            "stderr": "[dry-run]\n",
            "timed_out": False,
            "elapsed_seconds": elapsed,
        }

    env = os.environ.copy()
    env.pop("PI_CODING_AGENT_DIR", None)
    started = time.monotonic()
    # Intentionally no timeout: agentic runs can take several minutes.
    # If this becomes configurable later, keep the default unbounded.
    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        stdout, stderr = proc.communicate()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        elapsed = elapsed_since(started)
        output = parse_final_output(stdout or "")
        output["elapsed_seconds"] = elapsed
        output["aborted"] = True
        write_json(output_path, output)
        write_text(stderr_path, (stderr or "") + "\n[aborted by keyboard interrupt]\n")
        return {
            "exit_code": 130,
            "text": output["text"],
            "output": output,
            "stderr": (stderr or "") + "\n[aborted by keyboard interrupt]\n",
            "aborted": True,
            "timed_out": False,
            "elapsed_seconds": elapsed,
        }
    elapsed = elapsed_since(started)
    output = parse_final_output(stdout)
    output["elapsed_seconds"] = elapsed
    write_json(output_path, output)
    write_text(stderr_path, stderr)
    return {
        "exit_code": proc.returncode or 0,
        "text": output["text"],
        "output": output,
        "stderr": stderr,
        "timed_out": False,
        "elapsed_seconds": elapsed,
    }


def render_template(template: str, values: dict[str, Any]) -> str:
    return template.format(**{key: "" if value is None else value for key, value in values.items()})


def run_parser(parser_script: Path, metadata_path: Path, judge_stdout_path: Path, output_path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            str(parser_script),
            "--metadata",
            str(metadata_path),
            "--judge-output",
            str(judge_stdout_path),
            "--output",
            str(output_path),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"parser failed: {proc.stderr.strip() or proc.stdout.strip()}")
    if not output_path.exists():
        raise RuntimeError(f"parser did not write {output_path}")
    return json.loads(read_text(output_path))


def error_record(metadata: dict[str, Any], phase: str, error: str, **extra: Any) -> dict[str, Any]:
    return {
        **metadata,
        "status": "error",
        "phase": phase,
        "score": "",
        "description": error,
        "error": error,
        **extra,
    }


def run_item(
    *,
    item: dict[str, Any],
    index: int,
    total: int,
    config: dict[str, Any],
    config_dir: Path,
    run_dir: Path,
    manifest_path: Path,
    dry_run: bool,
    logger: RunLogger,
) -> bool:
    item_id = item["item_id"]
    case = item["case"]
    model = item["model"]
    reasoning = item["reasoning"]
    artifact_dir = run_dir / "artifacts" / item_id
    answer_dir = artifact_dir / "answer"
    judge_dir = artifact_dir / "judge"
    parsed_path = artifact_dir / "parsed.json"
    metadata_path = artifact_dir / "metadata.json"

    runner_config = config.get("runner") or {}
    judge_config = config.get("judge") or {}

    metadata = {
        "benchmark_name": config.get("benchmark_name"),
        "run_id": config.get("run_id"),
        "item_id": item_id,
        "case_id": case["id"],
        "question": case["question"],
        "nonsensical_element": case.get("nonsensical_element", ""),
        "tags": case.get("tags", []),
        "model": model["id"],
        "reasoning": reasoning,
        "judge_model": judge_config.get("model"),
        "judge_reasoning": judge_config.get("reasoning", "off"),
    }
    write_json(metadata_path, metadata)
    item_started = time.monotonic()
    timing: dict[str, float] = {}

    logger.log(
        f"[{index}/{total}] case={case['id']} model={model['id']} reasoning={reasoning} item={item_id}"
    )
    logger.log(f"[{index}/{total}] answer start: model={model['id']} case={case['id']}")
    append_manifest(manifest_path, item_id, "answer_running", case_id=case["id"], model=model["id"])
    answer_result = run_pi(
        prompt=case["question"],
        system_prompt=str(config.get("answer_system_prompt") or ""),
        model=str(model["id"]),
        reasoning=reasoning,
        artifact_dir=answer_dir,
        dry_run_text=f"Dry-run answer for {case['id']} from {model['id']}." if dry_run else None,
    )
    timing["answer_seconds"] = float(answer_result.get("elapsed_seconds") or 0.0)
    answer_text = str(answer_result.get("text") or "")
    write_text(answer_dir / "answer.txt", answer_text)
    if answer_result.get("aborted"):
        logger.log(f"[{index}/{total}] answer aborted: case={case['id']}")
        timing["item_seconds"] = elapsed_since(item_started)
        append_manifest(manifest_path, item_id, "interrupted", phase="answer", exit_code=130, timing=timing)
        raise KeyboardInterrupt
    if answer_result["exit_code"] != 0:
        logger.log(f"[{index}/{total}] answer error: exit_code={answer_result['exit_code']} case={case['id']}")
        timing["item_seconds"] = elapsed_since(item_started)
        record = error_record(
            metadata,
            "answer",
            answer_result["stderr"].strip() or "answer model exited with an error",
            exit_code=answer_result["exit_code"],
            timing=timing,
        )
        write_json(parsed_path, record)
        append_jsonl(run_dir / "results.jsonl", record)
        append_manifest(
            manifest_path,
            item_id,
            "complete",
            status="error",
            phase="answer",
            exit_code=answer_result["exit_code"],
            timing=timing,
        )
        return False
    append_manifest(manifest_path, item_id, "answer_complete")
    logger.log(f"[{index}/{total}] answer complete: model={model['id']} case={case['id']}")

    judge_template = read_text(resolve_path(config_dir, str(judge_config["template_file"])))
    judge_prompt = render_template(
        judge_template,
        {
            **case,
            "response": answer_text,
            "nonsensical_element": case.get("nonsensical_element", ""),
        },
    )
    write_text(judge_dir / "prompt.txt", judge_prompt)

    logger.log(
        f"[{index}/{total}] judge start: model={judge_config['model']} reasoning={judge_config.get('reasoning', 'off')} case={case['id']}"
    )
    append_manifest(manifest_path, item_id, "judge_running")
    judge_result = run_pi(
        prompt=judge_prompt,
        system_prompt="",
        model=str(judge_config["model"]),
        reasoning=str(judge_config.get("reasoning", "off")),
        artifact_dir=judge_dir,
        dry_run_text="Score: 2\nDescription: Dry-run judge output confirms parser and manifest flow." if dry_run else None,
    )
    timing["judge_seconds"] = float(judge_result.get("elapsed_seconds") or 0.0)
    judge_text = str(judge_result.get("text") or "")
    write_text(judge_dir / "judge.txt", judge_text)
    if judge_result.get("aborted"):
        logger.log(f"[{index}/{total}] judge aborted: case={case['id']}")
        timing["item_seconds"] = elapsed_since(item_started)
        append_manifest(manifest_path, item_id, "interrupted", phase="judge", exit_code=130, timing=timing)
        raise KeyboardInterrupt
    if judge_result["exit_code"] != 0:
        logger.log(f"[{index}/{total}] judge error: exit_code={judge_result['exit_code']} case={case['id']}")
        timing["item_seconds"] = elapsed_since(item_started)
        record = error_record(
            metadata,
            "judge",
            judge_result["stderr"].strip() or "judge model exited with an error",
            exit_code=judge_result["exit_code"],
            timing=timing,
        )
        write_json(parsed_path, record)
        append_jsonl(run_dir / "results.jsonl", record)
        append_manifest(
            manifest_path,
            item_id,
            "complete",
            status="error",
            phase="judge",
            exit_code=judge_result["exit_code"],
            timing=timing,
        )
        return False
    append_manifest(manifest_path, item_id, "judge_complete")
    logger.log(f"[{index}/{total}] judge complete: model={judge_config['model']} case={case['id']}")

    try:
        parser_script = resolve_path(config_dir, str(runner_config["parser_script"]))
        logger.log(f"[{index}/{total}] parse start: parser={parser_script} case={case['id']}")
        parse_started = time.monotonic()
        parsed = run_parser(parser_script, metadata_path, judge_dir / "judge.txt", parsed_path)
        timing["parse_seconds"] = elapsed_since(parse_started)
    except Exception as exc:  # noqa: BLE001 - preserve parser failure in manifest
        if "parse_started" in locals():
            timing["parse_seconds"] = elapsed_since(parse_started)
        timing["item_seconds"] = elapsed_since(item_started)
        logger.log(f"[{index}/{total}] parse error: case={case['id']} error={exc}")
        record = error_record(metadata, "parse", str(exc), timing=timing)
        write_json(parsed_path, record)
        append_jsonl(run_dir / "results.jsonl", record)
        append_manifest(manifest_path, item_id, "complete", status="error", phase="parse", error=str(exc), timing=timing)
        return False

    timing["item_seconds"] = elapsed_since(item_started)
    parsed["timing"] = timing
    parsed.setdefault("status", "ok")
    write_json(parsed_path, parsed)
    append_jsonl(run_dir / "results.jsonl", parsed)
    append_manifest(manifest_path, item_id, "parsed", score=parsed.get("score"))
    append_manifest(manifest_path, item_id, "complete", score=parsed.get("score"), timing=timing)
    logger.log(f"[{index}/{total}] complete: case={case['id']} score={parsed.get('score')}")
    return True


def write_latest_manifest(manifest_path: Path, latest_path: Path) -> None:
    states = replay_manifest(manifest_path)
    write_json(latest_path, states)


def write_auto_report(config_path: Path, logger: RunLogger) -> bool:
    try:
        summary = generate_report(config_path)
    except Exception as exc:  # noqa: BLE001 - keep benchmark results even if report rendering fails
        logger.log(f"report error: {exc}")
        return False
    logger.log(f"report complete: path={summary['report_path']} records={summary['records']}")
    return True


def main() -> int:
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT})
    signal.signal(signal.SIGINT, signal.default_int_handler)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--resume", action="store_true", help="skip completed items and continue incomplete work")
    parser.add_argument("--dry-run", action="store_true", help="write fake pi outputs for harness validation")
    parser.add_argument("--limit", type=int, default=0, help="limit number of runnable matrix items")
    args = parser.parse_args()

    config_path = args.config.resolve()
    config_dir = config_path.parent
    run_dir = config_dir
    config = load_yaml(config_path)
    if not isinstance(config, dict):
        raise SystemExit("Config must be a YAML mapping")

    case_file = resolve_path(config_dir, str(config["case_file"]))
    case_data = load_case_data(case_file)
    cases = load_cases_from_data(case_file, case_data)
    if not config.get("answer_system_prompt") and case_data.get("answer_system_prompt"):
        config["answer_system_prompt"] = case_data["answer_system_prompt"]
    items = expand_matrix(config, cases)
    manifest_path = run_dir / "manifest.jsonl"
    latest_path = run_dir / "manifest.latest.json"
    logger = RunLogger(run_dir / "run.log")
    states = replay_manifest(manifest_path)
    run_started = time.monotonic()

    runnable = [
        item
        for item in items
        if not should_skip(item["item_id"], states, resume=args.resume)
    ]
    if args.limit > 0:
        runnable = runnable[: args.limit]

    append_jsonl(
        run_dir / "run-events.jsonl",
        {
            "ts": utc_now(),
            "event": "runner_start",
            "config": str(config_path),
            "resume": args.resume,
            "dry_run": args.dry_run,
            "items_total": len(items),
            "items_runnable": len(runnable),
            "limit": args.limit,
        },
    )
    logger.log(f"run start: config={config_path} run_dir={run_dir}")
    logger.log(
        f"run matrix: items_total={len(items)} items_runnable={len(runnable)} resume={args.resume} dry_run={args.dry_run}"
    )
    if args.limit > 0:
        logger.log(f"run limit: {args.limit} runnable matrix items")

    skipped = len(items) - len(runnable)
    if skipped:
        reason = "completed items" if args.resume else "items outside this run"
        logger.log(f"run skipped: {skipped} {reason}")

    failed = 0
    try:
        for index, item in enumerate(runnable, start=1):
            ok = run_item(
                item=item,
                index=index,
                total=len(runnable),
                config=config,
                config_dir=config_dir,
                run_dir=run_dir,
                manifest_path=manifest_path,
                dry_run=args.dry_run,
                logger=logger,
            )
            if not ok:
                failed += 1
                logger.log(f"item recorded with error: item={item['item_id']}")
            write_latest_manifest(manifest_path, latest_path)
    except KeyboardInterrupt:
        write_latest_manifest(manifest_path, latest_path)
        append_jsonl(
            run_dir / "run-events.jsonl",
            {
                "ts": utc_now(),
                "event": "runner_interrupted",
                "attempted": len(runnable),
                "failed": failed,
                "elapsed_seconds": elapsed_since(run_started),
            },
        )
        logger.log("run aborted by keyboard interrupt")
        write_auto_report(config_path, logger)
        print("\nAborted.")
        return 130

    write_latest_manifest(manifest_path, latest_path)
    run_elapsed = elapsed_since(run_started)
    append_jsonl(
        run_dir / "run-events.jsonl",
        {
            "ts": utc_now(),
            "event": "runner_complete",
            "attempted": len(runnable),
            "failed": failed,
            "elapsed_seconds": run_elapsed,
        },
    )
    logger.log(f"run complete: run_dir={run_dir} attempted={len(runnable)} failed={failed} elapsed_seconds={run_elapsed:.3f}")
    report_ok = write_auto_report(config_path, logger)
    print(f"Run complete: {run_dir}")
    print(f"Items total: {len(items)}; attempted: {len(runnable)}")
    print(f"Report: {run_dir / 'report.md'}")
    if failed:
        print(f"Failed: {failed}", file=sys.stderr)
        return 1
    if not report_ok:
        print("Report generation failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        raise SystemExit(130) from None
