# Model Input Control

`pi-bench` is a controlled benchmarking harness. Every model call must have explicit, auditable inputs. The harness must not depend on the default `pi` coding-agent prompt, ambient context files, skills, tools, prompt templates, or an implicit session.

This document defines the model input contract used by `runner.py`.

## Required Invariant

Every real `pi` call made by `runner.py` must specify:

- the target model via `--model`
- the reasoning mode via `--thinking`
- a run-local session directory via `--session-dir`
- a controlled replacement system prompt via `--system-prompt`
- a controlled user prompt via `-p`
- `--mode json`
- `--no-tools`
- `--no-skills`
- `--no-prompt-templates`
- `--no-context-files`

The runner must not use `--append-system-prompt` for benchmark model calls. Appending keeps the default `pi` coding-agent system prompt in effect, which makes the benchmark input uncontrolled.

## Empty System Prompts

`pi_args()` only adds `--system-prompt` when `system_prompt.strip()` is non-empty. If a caller passes an empty system prompt, `pi` falls back to its own default system prompt.

That behavior is not acceptable for controlled benchmark calls. Current answer and judge calls both pass non-empty system prompts:

- answer calls pass the selected answer prompt text
- judge calls pass `JUDGE_SYSTEM_PROMPT`

Any future runner path that calls `run_pi()` must also pass an explicit non-empty system prompt unless the benchmark intentionally adds a separately documented opt-in for default `pi` behavior.

## Answer Model Input

The answer model is the model being evaluated.

System prompt:

- source: selected entry from `benchmarks/<name>/answer-prompts.yml`
- artifact: `artifacts/<item_id>/answer/system-prompt.md`
- CLI flag: `--system-prompt <answer prompt text>`

User prompt:

- source: the case `question`
- artifact: captured in `artifacts/<item_id>/answer/args.json` and `metadata.json`
- CLI flag: `-p <question>`

For `bullshit-detector`, the answer model receives only the case question as the user prompt. It does not receive `judge_hint`.

## Judge Model Input

The judge model evaluates the answer model's response.

System prompt:

- source: `JUDGE_SYSTEM_PROMPT` in `runner.py`
- purpose: stable one-line benchmark judge role
- artifact: `artifacts/<item_id>/judge/system-prompt.md`
- CLI flag: `--system-prompt <judge system prompt>`

User prompt:

- source: rendered `judge.template_file` from the run config
- for `bullshit-detector`: `benchmarks/bullshit-detector/judge-template.md`
- template values: `question`, `response`, `judge_hint`, plus the case fields
- artifact: `artifacts/<item_id>/judge/prompt.txt`
- CLI flag: `-p <rendered judge prompt>`

This split keeps the judge role stable while the benchmark-specific rubric remains in the template file.

## What Is Preserved

Each model call writes enough information to audit the exact prompt shape:

- `args.json`: the exact `pi` argv list
- `system-prompt.md`: the replacement system prompt passed to `pi`
- `prompt.txt`: for judge calls, the rendered user prompt/rubric
- `output.json`: compact final output and final message metadata
- `answer.txt` or `judge.txt`: human-readable final text
- `stderr.txt`: process stderr or dry-run marker

Raw `pi --mode json` event streams are intentionally not persisted. `output.json` should remain compact and should not include streaming deltas.

## Recommended Validation

After changing runner prompt behavior, run a one-case dry-run and inspect generated artifacts:

```bash
./bench run bullshit-detector --providers plebchat --model-source configured --models plebchat/qwen/qwen3-coder-next --reasoning off --answer-prompts baseline-helpful --judge-model plebchat/qwen/qwen3-coder-next --judge-reasoning off --run-id prompt-contract-dry-run --limit 1 --dry-run --yes
```

Expected:

- `answer/args.json` contains `--system-prompt`
- `judge/args.json` contains `--system-prompt`
- neither args file contains `--append-system-prompt`
- `answer/system-prompt.md` contains the selected answer prompt
- `judge/system-prompt.md` contains `JUDGE_SYSTEM_PROMPT`
- `judge/prompt.txt` contains the rendered judge template

Clean up validation runs unless they are intentionally being kept for inspection.
