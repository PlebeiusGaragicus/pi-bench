# Runner

`runner.py` is the execution engine. It consumes one run config, expands the benchmark matrix, runs the real `pi` CLI, records artifacts, and writes structured results.

Use it directly when a config already exists:

```bash
./bench runner benchmarks/bullshit-detector/runs/<run-id>/config.yml
```

Resume an interrupted config:

```bash
./bench runner benchmarks/bullshit-detector/runs/<run-id>/config.yml --resume
```

## Startup

On startup, `runner.py`:

1. Loads the run config.
2. Resolves paths relative to the config directory.
3. Loads cases from `case_file`.
4. Loads selected answer prompts from `answer_prompt_file`.
5. Expands matrix items.
6. Replays `manifest.jsonl` to determine latest known item states.
7. Applies `--resume` and `--limit`.
8. Appends a `runner_start` record to `run-events.jsonl`.
9. Mirrors progress to `run.log`.

## Item Identity

Each item id is stable over:

```text
case_id, answer_model_id, answer_reasoning, answer_prompt_id
```

The item id intentionally includes answer prompt id and answer reasoning because those are experimental variables for the answer model.

The item id does not include:

- judge model
- judge reasoning
- judge template
- parser script

Those values are recorded in metadata/results, but they do not affect the artifact directory name. If a run's judge configuration changes after artifacts exist, resume can skip old judge artifacts because the item id is unchanged. Use a new run id or clear affected artifacts when changing judge configuration.

## Phase 1: Answer

The answer phase calls the answer model for each runnable item.

Inputs:

- system prompt: selected answer prompt text
- user prompt: case question
- model: item model id
- reasoning: item reasoning mode

Outputs:

- `answer/args.json`
- `answer/system-prompt.md`
- `answer/output.json`
- `answer/stderr.txt`
- `answer/answer.txt`
- optional `answer/thoughts.txt`

If the answer call fails, the runner writes an error result record with `phase: answer`.

## Phase 2: Judge

The judge phase calls the judge model for items that have answer text and are not already successfully complete.

Inputs:

- system prompt: `JUDGE_SYSTEM_PROMPT` from `runner.py`
- user prompt: rendered judge template
- model: `judge.model` from config
- reasoning: `judge.reasoning` from config

The rendered judge template receives `response` plus all case fields. For
`bullshit-detector`, this includes:

- `question`
- `response`
- `judge_hint`
- all case fields

Outputs:

- `judge/args.json`
- `judge/system-prompt.md`
- `judge/prompt.txt`
- `judge/output.json`
- `judge/stderr.txt`
- `judge/judge.txt`

If the judge call fails, the runner writes an error result record with `phase: judge`.

## Phase 3: Parse

The parse phase calls the benchmark-specific parser script.

Inputs:

- `metadata.json`
- `judge/judge.txt`
- target path for `parsed.json`

The parser must write structured JSON. A benchmark may choose its own scoring
range. For example, `bullshit-detector` parses:

```text
Score: <0, 1, or 2>
Description: <sentence>
```

`skibidi` uses the same two-line shape with binary scores:

```text
Score: <0 or 1>
Description: <sentence>
```

Successful parse writes:

- `parsed.json`
- one line in `results.jsonl`
- `parsed` and `complete` states in `manifest.jsonl`

Parser failures are recorded as result records with `status: error` and `phase: parse`.

## Resume Semantics

`--resume` has two layers:

1. Matrix filtering: items with latest manifest state `complete` and no error status are skipped entirely.
2. Phase artifact skipping: answer/judge phases skip existing usable text artifacts even if the item is not marked complete.

Usable text artifacts require:

- the text file exists and is non-empty
- the corresponding `output.json` exists
- `output.json` does not contain a model error stop reason

Resume does not validate that the config still matches existing artifacts. It assumes a run directory is a coherent experiment. If prompt, judge, parser, model, or reasoning configuration changes, start a new run or intentionally clear affected generated artifacts.

## Interruptions

On keyboard interrupt, the runner:

- terminates the active `pi` process
- writes whatever compact output can be parsed
- records `interrupted` in `manifest.jsonl`
- writes `manifest.latest.json`
- appends `runner_interrupted` to `run-events.jsonl`
- attempts report generation
- exits with `130`

## Reports

At successful completion or interruption, the runner calls `report.py`. Report failures do not invalidate benchmark artifacts; they are logged as report errors.
