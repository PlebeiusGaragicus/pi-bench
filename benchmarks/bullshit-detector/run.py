#!/usr/bin/env python3
"""Configure and launch a BullshitBench run."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from yaml_loader import load_yaml  # noqa: E402

DEFAULT_MODELS_FILE = Path.home() / ".pi" / "agent" / "models.json"
BENCHMARK_NAME = "bullshit-detector"
CASE_FILE = "../../questions.yml"
ANSWER_PROMPT_FILE = "../../answer-prompts.yml"
JUDGE_TEMPLATE_FILE = "../../judge-template.md"
PARSER_SCRIPT = "../../collate.py"
REASONING_MODES = ["off", "low", "medium", "high"]
QUESTIONS_FILE = BENCHMARK_DIR / "questions.yml"
ANSWER_PROMPTS_FILE = BENCHMARK_DIR / "answer-prompts.yml"
DEFAULT_ANSWER_PROMPT_IDS = ["baseline-helpful"]


@dataclass
class ModelInfo:
    runner_id: str
    provider: str
    raw_id: str
    name: str
    reasoning: bool | None
    source: str
    metadata: dict[str, Any]


@dataclass
class InterruptedRun:
    config_path: Path
    run_id: str
    complete_items: int
    total_items: int


@dataclass
class AnswerPromptInfo:
    id: str
    description: str
    text: str


def timestamp_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def slugify_run_id(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    return value.strip("-")


def parse_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def prompt_text(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    response = input(f"{prompt}{suffix}: ").strip()
    return response or (default or "")


def prompt_bool(prompt: str, default: bool) -> bool:
    default_label = "Y/n" if default else "y/N"
    while True:
        response = input(f"{prompt} [{default_label}]: ").strip().lower()
        if not response:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        print("Please enter y or n.")


def prompt_int(prompt: str, default: int | None = None, allow_empty: bool = False) -> int | None:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        response = input(f"{prompt}{suffix}: ").strip()
        if not response:
            if allow_empty:
                return None
            if default is not None:
                return default
        try:
            return int(response)
        except ValueError:
            print("Please enter a whole number.")


def print_options(options: list[Any]) -> None:
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")


def select_many(prompt: str, options: list[Any], supplied: list[str] | None, default_indexes: list[int]) -> list[str]:
    if supplied:
        return supplied

    ids = [str(option) for option in options]
    defaults = [ids[index - 1] for index in default_indexes]
    if not sys.stdin.isatty():
        return defaults

    print(prompt)
    print_options(options)
    default_label = ", ".join(str(index) for index in default_indexes)
    while True:
        response = input(f"Choose one or more by number, or enter custom ids [{default_label}]: ").strip()
        if not response:
            return defaults

        selected: list[str] = []
        for token in [item.strip() for item in response.split(",") if item.strip()]:
            if token.isdigit() and 1 <= int(token) <= len(ids):
                selected.append(ids[int(token) - 1])
            else:
                selected.append(token)
        if selected:
            return selected


def select_one(prompt: str, options: list[Any], supplied: str | None, default_index: int = 1) -> str:
    if supplied:
        return supplied

    ids = [str(option) for option in options]
    if not sys.stdin.isatty():
        return ids[default_index - 1]

    print(prompt)
    print_options(options)
    while True:
        response = input(f"Choose one by number, or enter a custom id [{default_index}]: ").strip()
        if not response:
            return ids[default_index - 1]
        if response.isdigit() and 1 <= int(response) <= len(ids):
            return ids[int(response) - 1]
        if response:
            return response


def indented_block(value: str, spaces: int = 2) -> str:
    indent = " " * spaces
    lines = value.rstrip("\n").splitlines() or [""]
    return "\n".join(f"{indent}{line}" if line else indent.rstrip() for line in lines)


def scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def config_text(config: dict[str, Any]) -> str:
    lines = [
        f"benchmark_name: {config['benchmark_name']}",
        f"run_id: {config['run_id']}",
        f"case_file: {config['case_file']}",
        f"answer_prompt_file: {config['answer_prompt_file']}",
        "",
        "answer_prompts:",
    ]
    for prompt_id in config["answer_prompts"]:
        lines.append(f"  - {prompt_id}")
    lines.extend(
        [
            "",
            "models:",
        ]
    )
    for model in config["models"]:
        lines.extend(
            [
                f"  - id: {model['id']}",
                f"    reasoning: {model['reasoning']}",
            ]
        )
    lines.extend(
        [
            "",
            "judge:",
            f"  model: {config['judge']['model']}",
            f"  reasoning: {config['judge']['reasoning']}",
            f"  template_file: {config['judge']['template_file']}",
            "",
            "runner:",
            f"  parser_script: {config['runner']['parser_script']}",
            "",
        ]
    )
    return "\n".join(lines)


def prompt_run_id(supplied: str | None) -> str:
    if supplied is not None:
        candidate = supplied.strip() or timestamp_run_id()
    elif sys.stdin.isatty():
        candidate = prompt_text("Run id", timestamp_run_id())
    else:
        candidate = timestamp_run_id()

    run_id = slugify_run_id(candidate)
    if not run_id:
        return timestamp_run_id()
    return run_id


def handle_existing_config(config_path: Path, assume_yes: bool) -> str:
    if not config_path.exists():
        return "write"
    if assume_yes or not sys.stdin.isatty():
        return "overwrite"

    print(f"Config already exists: {config_path.relative_to(REPO_ROOT)}")
    while True:
        response = input("Use existing, overwrite, choose new run id, or abort? [u/o/n/a]: ").strip().lower()
        if response in {"u", "use", ""}:
            return "use"
        if response in {"o", "overwrite"}:
            return "overwrite"
        if response in {"n", "new"}:
            return "new"
        if response in {"a", "abort"}:
            raise SystemExit("Aborted.")
        print("Please enter u, o, n, or a.")


def resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def count_cases(case_file: Path) -> int:
    data = load_yaml(case_file)
    cases = data.get("cases") if isinstance(data, dict) else None
    return len(cases) if isinstance(cases, list) else 0


def benchmark_question_count() -> int:
    return count_cases(QUESTIONS_FILE)


def load_answer_prompt_catalog(path: Path = ANSWER_PROMPTS_FILE) -> list[AnswerPromptInfo]:
    data = load_yaml(path)
    prompts = data.get("prompts") if isinstance(data, dict) else None
    if not isinstance(prompts, list) or not prompts:
        raise SystemExit(f"Expected {path} to contain a non-empty top-level 'prompts' list")

    catalog: list[AnswerPromptInfo] = []
    seen: set[str] = set()
    for index, prompt in enumerate(prompts, start=1):
        if not isinstance(prompt, dict):
            raise SystemExit(f"Prompt {index} in {path} must be a mapping")
        prompt_id = str(prompt.get("id") or "").strip()
        prompt_text = str(prompt.get("text") or "")
        if not prompt_id:
            raise SystemExit(f"Prompt {index} in {path} is missing 'id'")
        if prompt_id in seen:
            raise SystemExit(f"Duplicate prompt id in {path}: {prompt_id}")
        if not prompt_text.strip():
            raise SystemExit(f"Prompt {prompt_id} in {path} is missing non-empty 'text'")
        seen.add(prompt_id)
        catalog.append(
            AnswerPromptInfo(
                id=prompt_id,
                description=str(prompt.get("description") or ""),
                text=prompt_text,
            )
        )
    return catalog


def answer_prompt_label(prompt: AnswerPromptInfo) -> str:
    if prompt.description:
        return f"{prompt.id} ({prompt.description})"
    return prompt.id


def select_answer_prompts(args: argparse.Namespace) -> list[str]:
    catalog = load_answer_prompt_catalog()
    by_id = {prompt.id: prompt for prompt in catalog}
    supplied = parse_csv(args.answer_prompts)
    if supplied is not None:
        if not supplied:
            raise SystemExit("--answer-prompts must include at least one prompt id")
        unknown = [prompt_id for prompt_id in supplied if prompt_id not in by_id]
        if unknown:
            available = ", ".join(sorted(by_id))
            raise SystemExit(f"Unknown answer prompt(s): {', '.join(unknown)}. Available prompts: {available}")
        return supplied

    ids = [prompt.id for prompt in catalog]
    default_indexes = [ids.index(prompt_id) + 1 for prompt_id in DEFAULT_ANSWER_PROMPT_IDS if prompt_id in ids] or [1]
    if not sys.stdin.isatty():
        return [ids[index - 1] for index in default_indexes]

    labels = [answer_prompt_label(prompt) for prompt in catalog]
    selected_labels = select_many("Answer prompt variants:", labels, None, default_indexes)
    by_label = dict(zip(labels, catalog, strict=True))
    return [by_label[label].id for label in selected_labels if label in by_label]


def count_config_matrix_items(config_path: Path) -> int:
    config = load_yaml(config_path)
    if not isinstance(config, dict):
        return 0
    case_file = resolve_path(config_path.parent, str(config.get("case_file") or ""))
    models = config.get("models")
    if not isinstance(models, list):
        return 0
    answer_prompts = config.get("answer_prompts")
    if not isinstance(answer_prompts, list):
        return 0
    return count_cases(case_file) * len(models) * len(answer_prompts)


def count_config_matrix_items_per_question(config_path: Path) -> int:
    config = load_yaml(config_path)
    if not isinstance(config, dict):
        return 0
    models = config.get("models")
    answer_prompts = config.get("answer_prompts")
    if not isinstance(models, list) or not isinstance(answer_prompts, list):
        return 0
    return len(models) * len(answer_prompts)


def count_complete_items(latest_path: Path) -> int:
    if not latest_path.exists():
        return 0
    try:
        states = json.loads(latest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    if not isinstance(states, dict):
        return 0
    return sum(
        1
        for state in states.values()
        if isinstance(state, dict) and state.get("state") == "complete" and state.get("status") != "error"
    )


def completed_initial_limited_run(run_dir: Path, complete_items: int, total_items: int) -> bool:
    events_path = run_dir / "run-events.jsonl"
    if not events_path.exists():
        return False
    latest_start: dict[str, Any] | None = None
    with events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "runner_start":
                continue
            latest_start = event
    if not latest_start or latest_start.get("resume"):
        return False
    items_runnable = latest_start.get("items_runnable")
    if not isinstance(items_runnable, int) or items_runnable >= total_items:
        return False
    return complete_items >= items_runnable


def find_interrupted_runs() -> list[InterruptedRun]:
    runs_dir = BENCHMARK_DIR / "runs"
    interrupted: list[InterruptedRun] = []
    for config_path in sorted(runs_dir.glob("*/config.yml")):
        run_dir = config_path.parent
        if not (run_dir / "run-events.jsonl").exists() and not (run_dir / "manifest.latest.json").exists():
            continue
        total_items = count_config_matrix_items(config_path)
        if total_items <= 0:
            continue
        complete_items = count_complete_items(run_dir / "manifest.latest.json")
        if complete_items < total_items and not completed_initial_limited_run(run_dir, complete_items, total_items):
            interrupted.append(
                InterruptedRun(
                    config_path=config_path,
                    run_id=run_dir.name,
                    complete_items=complete_items,
                    total_items=total_items,
                )
            )
    return interrupted


def maybe_resume_interrupted_run(args: argparse.Namespace) -> int | None:
    if not sys.stdin.isatty() or args.run_id or args.no_launch:
        return None

    print("Checking for incomplete benchmark runs...", end=" ")
    interrupted = find_interrupted_runs()
    if not interrupted:
        print("none found")
        return None

    print(f"found {len(interrupted)}")
    print("Incomplete benchmark runs:")
    labels = [
        f"{run.run_id} ({run.complete_items}/{run.total_items} items complete)"
        for run in interrupted
    ]
    for index, label in enumerate(labels, start=1):
        print(f"  {index}. {label}")
    if not prompt_bool("Continue an incomplete run", True):
        return None

    selected_label = select_one("Which run should continue?", labels, None)
    selected_run = interrupted[labels.index(selected_label)]
    command = [sys.executable, str(REPO_ROOT / "runner.py"), str(selected_run.config_path), "--resume"]
    if args.dry_run:
        command.append("--dry-run")
    if args.limit is not None and args.limit > 0:
        command.extend(["--limit", str(args.limit)])
    print(f"Launching: {shlex.join(command)}")
    sys.stdout.flush()
    return subprocess.run(command, cwd=REPO_ROOT).returncode


def load_provider_catalog(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Models file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Could not parse models file {path}: {exc}") from exc

    providers = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(providers, dict) or not providers:
        raise SystemExit(f"Expected {path} to contain a non-empty 'providers' mapping")
    return providers


def filtered_providers(providers: dict[str, Any], selected_names: list[str]) -> dict[str, Any]:
    selected = {}
    for name in selected_names:
        provider = providers.get(name)
        if isinstance(provider, dict):
            selected[name] = provider
    if not selected:
        raise SystemExit("No matching providers selected")
    return selected


def provider_labels(providers: dict[str, Any]) -> list[str]:
    labels = []
    for provider_name, provider in providers.items():
        model_count = len(provider.get("models") or []) if isinstance(provider, dict) else 0
        labels.append(f"{provider_name} ({model_count} configured models)")
    return labels


def select_providers(providers: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.providers:
        return filtered_providers(providers, parse_csv(args.providers) or [])
    if not sys.stdin.isatty():
        return providers

    print("Model providers:")
    labels = provider_labels(providers)
    selected_labels = select_many("Which providers would you like to use?", labels, None, list(range(1, len(labels) + 1)))
    names_by_label = dict(zip(labels, providers.keys(), strict=True))
    return filtered_providers(providers, [names_by_label[label] for label in selected_labels if label in names_by_label])


def should_expand_models(args: argparse.Namespace) -> bool:
    if args.model_source:
        return args.model_source == "expanded"
    if not sys.stdin.isatty():
        return False
    return prompt_bool("Expand selected providers by calling /v1/models", False)


def reasoning_from_metadata(metadata: dict[str, Any]) -> bool | None:
    for key in ("reasoning", "supportsReasoning", "supports_reasoning", "reasoning_supported"):
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    capabilities = metadata.get("capabilities")
    if isinstance(capabilities, dict):
        for key in ("reasoning", "thinking"):
            value = capabilities.get(key)
            if isinstance(value, bool):
                return value
    if isinstance(capabilities, list) and any(str(item).lower() in {"reasoning", "thinking"} for item in capabilities):
        return True
    return None


def is_embedding_model(metadata: dict[str, Any]) -> bool:
    model_type = str(metadata.get("type") or metadata.get("object") or "").lower()
    if model_type in {"embedding", "embeddings"}:
        return True
    raw_id = str(metadata.get("id") or "").lower()
    return "embedding" in raw_id or "embed" in raw_id


def display_name(model: ModelInfo) -> str:
    suffixes = []
    if model.name and model.name != model.raw_id:
        suffixes.append(model.name)
    if model.reasoning is True:
        suffixes.append("reasoning")
    elif model.reasoning is None:
        suffixes.append("reasoning unknown")
    suffixes.append(model.source)
    return f"{model.runner_id} ({', '.join(suffixes)})"


def configured_models(providers: dict[str, Any]) -> list[ModelInfo]:
    models: list[ModelInfo] = []
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        for model in provider.get("models") or []:
            if not isinstance(model, dict) or not model.get("id"):
                continue
            raw_id = str(model["id"])
            models.append(
                ModelInfo(
                    runner_id=f"{provider_name}/{raw_id}",
                    provider=str(provider_name),
                    raw_id=raw_id,
                    name=str(model.get("name") or raw_id),
                    reasoning=reasoning_from_metadata(model),
                    source="configured",
                    metadata=model,
                )
            )
    return models


def fetch_provider_models(provider_name: str, provider: dict[str, Any], timeout: int = 20) -> list[ModelInfo]:
    base_url = str(provider.get("baseUrl") or "").rstrip("/")
    if not base_url:
        return []

    request = urllib.request.Request(f"{base_url}/models", headers={"Accept": "application/json"})
    api_key = provider.get("apiKey")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Warning: could not expand models for provider {provider_name}: {exc}", file=sys.stderr)
        return []

    raw_models = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        print(f"Warning: provider {provider_name} returned an unexpected /models payload", file=sys.stderr)
        return []

    models: list[ModelInfo] = []
    for model in raw_models:
        if not isinstance(model, dict) or not model.get("id"):
            continue
        if is_embedding_model(model):
            continue
        raw_id = str(model["id"])
        models.append(
            ModelInfo(
                runner_id=f"{provider_name}/{raw_id}",
                provider=provider_name,
                raw_id=raw_id,
                name=str(model.get("name") or model.get("publisher") or raw_id),
                reasoning=reasoning_from_metadata(model),
                source="endpoint",
                metadata=model,
            )
        )
    return models


def merge_models(primary: list[ModelInfo], secondary: list[ModelInfo]) -> list[ModelInfo]:
    merged = {model.runner_id: model for model in secondary}
    for model in primary:
        existing = merged.get(model.runner_id)
        if existing and existing.reasoning is None:
            existing.reasoning = model.reasoning
        if existing and (not existing.name or existing.name == existing.raw_id):
            existing.name = model.name
        merged[model.runner_id] = existing or model
    return sorted(merged.values(), key=lambda item: item.runner_id)


def discover_models(providers: dict[str, Any], expand: bool) -> list[ModelInfo]:
    configured = configured_models(providers)
    if not expand:
        return configured

    expanded: list[ModelInfo] = []
    for provider_name, provider in providers.items():
        if isinstance(provider, dict):
            expanded.extend(fetch_provider_models(str(provider_name), provider))
    return merge_models(configured, expanded)


def select_models(prompt: str, models: list[ModelInfo], supplied: list[str] | None, default_indexes: list[int]) -> list[ModelInfo]:
    if not models:
        raise SystemExit("No models are available from the selected source")
    if supplied:
        by_id = {model.runner_id: model for model in models}
        selected = []
        for model_id in supplied:
            selected.append(by_id.get(model_id) or ModelInfo(model_id, model_id.split("/", 1)[0], model_id, model_id, None, "custom", {}))
        return selected

    labels = [display_name(model) for model in models]
    selected_labels = select_many(prompt, labels, None, default_indexes)
    by_label = dict(zip(labels, models, strict=True))
    selected = []
    for label in selected_labels:
        selected.append(by_label.get(label) or ModelInfo(label, label.split("/", 1)[0], label, label, None, "custom", {}))
    return selected


def model_supports_reasoning(model: ModelInfo, args: argparse.Namespace) -> bool:
    if model.reasoning is True:
        return True
    if model.reasoning is False:
        return False
    if args.assume_reasoning:
        return True
    if not sys.stdin.isatty():
        return False
    return prompt_bool(f"Enable reasoning modes for {model.runner_id} with unknown support", False)


def selected_model_entries(selected_models: list[ModelInfo], args: argparse.Namespace) -> list[dict[str, str]]:
    supplied_modes = parse_csv(args.reasoning)
    entries: list[dict[str, str]] = []
    reasoning_capable = [model for model in selected_models if model_supports_reasoning(model, args)]

    if supplied_modes is not None:
        modes_by_model = {
            model.runner_id: supplied_modes if model in reasoning_capable or supplied_modes == ["off"] else ["off"]
            for model in selected_models
        }
    elif reasoning_capable:
        selected_modes = select_many("Reasoning modes for capable models:", REASONING_MODES, None, [1])
        modes_by_model = {
            model.runner_id: selected_modes if model in reasoning_capable else ["off"]
            for model in selected_models
        }
    else:
        modes_by_model = {model.runner_id: ["off"] for model in selected_models}

    for model in selected_models:
        for reasoning in modes_by_model[model.runner_id]:
            entries.append({"id": model.runner_id, "reasoning": reasoning})
    return entries


def select_judge_model(models: list[ModelInfo], args: argparse.Namespace) -> str:
    default_index = next((index for index, model in enumerate(models, start=1) if model.reasoning is True), 1)
    return select_models("Judge model:", models, [args.judge_model] if args.judge_model else None, [default_index])[0].runner_id


def select_judge_reasoning(judge_model: str, models: list[ModelInfo], args: argparse.Namespace) -> str:
    if args.judge_reasoning:
        return args.judge_reasoning
    judge_info = next((model for model in models if model.runner_id == judge_model), None)
    if judge_info is None or not model_supports_reasoning(judge_info, args):
        return "off"
    return select_one("Judge reasoning mode:", REASONING_MODES, None)


def build_config(models: list[ModelInfo], args: argparse.Namespace, run_id: str) -> dict[str, Any]:
    answer_prompts = select_answer_prompts(args)
    selected_answer_models = select_models("Answer models:", models, parse_csv(args.models), [1])
    model_entries = selected_model_entries(selected_answer_models, args)
    judge_model = select_judge_model(models, args)
    judge_reasoning = select_judge_reasoning(judge_model, models, args)

    return {
        "benchmark_name": BENCHMARK_NAME,
        "run_id": run_id,
        "case_file": CASE_FILE,
        "answer_prompt_file": ANSWER_PROMPT_FILE,
        "answer_prompts": answer_prompts,
        "models": model_entries,
        "judge": {
            "model": judge_model,
            "reasoning": judge_reasoning,
            "template_file": JUDGE_TEMPLATE_FILE,
        },
        "runner": {
            "parser_script": PARSER_SCRIPT,
        },
    }


def print_summary(config_path: Path, config: dict[str, Any], launch_args: list[str], question_count: int) -> None:
    models = config["models"]
    unique_models = sorted({model["id"] for model in models})
    unique_reasoning = sorted({model["reasoning"] for model in models})
    answer_prompts = config["answer_prompts"]
    command = [sys.executable, str(REPO_ROOT / "runner.py"), str(config_path), *launch_args]
    print("")
    print("Run summary")
    print(f"  Config: {config_path.relative_to(REPO_ROOT)}")
    print(f"  Run id: {config['run_id']}")
    print(f"  Answer prompts: {', '.join(answer_prompts)}")
    print(f"  Answer models: {', '.join(unique_models)}")
    print(f"  Reasoning modes: {', '.join(unique_reasoning)}")
    print(f"  Questions available: {question_count}")
    print(f"  Answer matrix items per case: {len(models) * len(answer_prompts)}")
    print(f"  Judge: {config['judge']['model']} / reasoning={config['judge']['reasoning']}")
    print(f"  Command: {shlex.join(command)}")


def launch_args_from_inputs(args: argparse.Namespace, question_count: int, matrix_items_per_question: int) -> list[str]:
    dry_run = args.dry_run
    question_limit = args.limit
    runner_limit = None

    if question_limit is not None:
        runner_limit = max(0, min(question_limit, question_count)) * matrix_items_per_question

    if sys.stdin.isatty():
        if dry_run is None:
            dry_run = prompt_bool("Dry run", False)
        if runner_limit is None:
            print(f"Questions available: {question_count}")
            question_limit = prompt_int("How many questions should this run cover; leave blank for all", allow_empty=True)
            if question_limit is not None:
                runner_limit = max(0, min(question_limit, question_count)) * matrix_items_per_question

    launch_args: list[str] = []
    if dry_run:
        launch_args.append("--dry-run")
    if runner_limit is not None and runner_limit > 0:
        launch_args.extend(["--limit", str(runner_limit)])
    return launch_args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-file", type=Path, default=DEFAULT_MODELS_FILE)
    parser.add_argument("--model-source", choices=["configured", "expanded"])
    parser.add_argument("--providers", help="comma-separated provider names from models.json")
    parser.add_argument("--run-id", help="run id to create; blank defaults to a timestamp")
    parser.add_argument("--models", help="comma-separated answer model ids")
    parser.add_argument("--reasoning", help="comma-separated answer reasoning modes")
    parser.add_argument("--answer-prompts", help="comma-separated answer prompt ids from answer-prompts.yml")
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-reasoning")
    parser.add_argument("--assume-reasoning", action="store_true", help="offer reasoning modes for models with unknown support")
    parser.add_argument("--dry-run", action="store_true", default=None)
    parser.add_argument("--limit", type=int, help="number of benchmark questions to run")
    parser.add_argument("--no-launch", action="store_true", help="write the config but do not execute runner.py")
    parser.add_argument("--yes", action="store_true", help="accept defaults and skip confirmation prompts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    resume_result = maybe_resume_interrupted_run(args)
    if resume_result is not None:
        return resume_result

    providers = load_provider_catalog(args.models_file.expanduser().resolve())
    providers = select_providers(providers, args)

    while True:
        run_id = prompt_run_id(args.run_id)
        config_path = BENCHMARK_DIR / "runs" / run_id / "config.yml"
        existing_action = handle_existing_config(config_path, args.yes)
        if existing_action == "new":
            args.run_id = None
            continue
        break

    if existing_action == "use":
        launch_args = launch_args_from_inputs(
            args,
            benchmark_question_count(),
            count_config_matrix_items_per_question(config_path),
        )
        command = [sys.executable, str(REPO_ROOT / "runner.py"), str(config_path), *launch_args]
        print(f"Launching: {shlex.join(command)}")
        if args.no_launch:
            return 0
        sys.stdout.flush()
        return subprocess.run(command, cwd=REPO_ROOT).returncode

    models = discover_models(providers, should_expand_models(args))
    config = build_config(models, args, run_id)
    question_count = benchmark_question_count()
    launch_args = launch_args_from_inputs(args, question_count, len(config["models"]) * len(config["answer_prompts"]))
    print_summary(config_path, config, launch_args, question_count)
    if not args.yes and sys.stdin.isatty() and not prompt_bool("Write config and launch", True):
        raise SystemExit("Aborted.")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text(config), encoding="utf-8")
    print(f"Wrote {config_path.relative_to(REPO_ROOT)}")

    if args.no_launch:
        return 0

    command = [sys.executable, str(REPO_ROOT / "runner.py"), str(config_path), *launch_args]
    sys.stdout.flush()
    return subprocess.run(command, cwd=REPO_ROOT).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        raise SystemExit(130) from None
