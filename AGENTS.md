# Agent Guide

This repo contains benchmark harnesses that run the real `pi` CLI, preserve raw
artifacts, and make runs reproducible from `config.yml` files.

## Repository Map

```text
bench                                  # venv bootstrap and command dispatcher
requirements.txt                       # Python dependencies for the venv
runner.py                             # run answer + judge matrix
report.py                             # collate parsed scores into reports
yaml_loader.py                        # small YAML subset loader
docs/                                 # architecture, runner, artifact, and model-input contracts
benchmarks/
  bullshit-detector/
    run.py                            # interactive benchmark launcher
    questions.yml                     # benchmark case data
    answer-prompts.yml                # named answer system prompt variants
    judge-template.md                 # benchmark-specific judge prompt
    collate.py                        # benchmark-specific judge parser
    runs/<run-id>/config.yml          # one concrete run configuration
```

Generated run artifacts live under `benchmarks/<name>/runs/<run-id>/` and should
not be hand-edited. `.gitignore` is intended to keep generated artifacts,
manifests, reports, and plots out of git while allowing run configs to be
tracked when desired.

Detailed internal contracts live under `docs/`. In particular,
`docs/model-inputs.md` defines the controlled prompt contract for every model
call.

## Core Flow

Prefer the root `bench` wrapper for all researcher-facing commands. It creates
or reuses `.venv`, installs `requirements.txt` only when dependencies change,
and dispatches through the venv Python.

Launch the benchmark helper:

```bash
./bench run bullshit-detector
```

The launcher:

- checks for interrupted runs first and offers to resume one
- reads providers from `~/.pi/agent/models.json`
- asks which providers to use
- asks for a run id; blank means timestamp
- asks whether to expand selected providers through `/v1/models`
- asks for answer prompt variants, answer models, reasoning modes, judge model,
  judge reasoning, question count, and launch flags
- writes `benchmarks/bullshit-detector/runs/<run-id>/config.yml`
- launches the root `runner.py`

For non-interactive validation, use:

```bash
./bench run bullshit-detector --providers plebchat --model-source configured --models plebchat/qwen/qwen3-coder-next --reasoning off --answer-prompts baseline-helpful,premise-skeptic --limit 1 --dry-run --yes
```

For endpoint-backed model discovery:

```bash
./bench run bullshit-detector --model-source expanded
```

The launcher hides embedding models returned by provider endpoints. Reasoning
support comes from model metadata when present; unknown support defaults to
`off` unless the user enables reasoning interactively or passes
`--assume-reasoning`.

## Run Config Contract

Each run is configured by `benchmarks/<name>/runs/<run-id>/config.yml`. Relative
paths are resolved from the config file directory.

```yaml
benchmark_name: bullshit-detector
run_id: smoke
case_file: ../../questions.yml
answer_prompt_file: ../../answer-prompts.yml

answer_prompts:
  - baseline-helpful

models:
  - id: plebchat/qwen/qwen3-coder-next
    reasoning: off

judge:
  model: plebchat/google/gemma-4-31b
  reasoning: off
  template_file: ../../judge-template.md

runner:
  parser_script: ../../collate.py
```

`runner.py` invokes `pi` with `--mode json`, `--no-tools`, `--no-skills`,
`--no-prompt-templates`, and `--no-context-files`. It adds `--model`,
`--thinking`, `--session-dir`, `--system-prompt`, and `-p` from the run config,
selected answer prompt, judge prompt contract, and benchmark case data. The
runner must not use `--append-system-prompt` for controlled benchmark calls
because that keeps the default `pi` coding-agent system prompt in effect. There
is intentionally no per-call timeout; agentic runs may take several minutes.

Answer prompts are first-class matrix entries selected by id from
`answer-prompts.yml`. The runner expands
`case x model x reasoning x answer_prompt`, records `answer_prompt_id` and
prompt hash in metadata and reports, and writes the concrete prompt text to each
answer artifact.

Answer, judge, and parser errors are recorded as `status: error` result records
so reports include failed items. Resume skips completed result records,
including errors, and continues incomplete work.

Runner progress is intentionally verbose. Console output is mirrored to
`benchmarks/<name>/runs/<run-id>/run.log`, including answer, judge, parse, error,
and completion events for each matrix item.

Runner execution is phased to reduce model swaps on self-hosted inference: run
all answer calls for the runnable matrix, then all judge calls for completed
answers, then all parser calls.

Do not persist raw `pi --mode json` streaming output. New answer and judge
artifacts should keep compact `output.json` files with final text and final
message metadata, plus human-readable `answer.txt` / `judge.txt`. Avoid storing
`message_update`, token deltas, or thinking deltas.

## Direct Commands

Run an existing config directly:

```bash
./bench runner benchmarks/bullshit-detector/runs/<run-id>/config.yml
```

Resume a config directly:

```bash
./bench runner benchmarks/bullshit-detector/runs/<run-id>/config.yml --resume
```

Generate a report:

```bash
./bench report benchmarks/bullshit-detector/runs/<run-id>/config.yml
```

## Case Data

Question data uses YAML because each case needs more than prompt text. Markdown
with `---` separators is pleasant for prompt-only files, but it becomes brittle
once cases need ids, hints, tags, expected fields, fixtures, or benchmark-level
prompt metadata.

For `bullshit-detector`, each case has:

- `id`
- `question`
- `judge_hint`

The answer model receives only `question`. The judge template receives
`question`, `response`, and `judge_hint`.

## Agent Notes

- Do not hard-code generated run ids in docs or tests unless the user explicitly
  wants a committed example config.
- Do not print or commit provider API keys from `~/.pi/agent/models.json`.
- Prefer `./bench run bullshit-detector --answer-prompts baseline-helpful,premise-skeptic --dry-run --limit 1 --yes`
  when changing launcher or runner behavior.
- Clean up generated validation runs after testing unless the user asked to keep
  them.
- Keep answer prompt variants in benchmark prompt files such as
  `answer-prompts.yml`; do not put system prompts in case data files.
