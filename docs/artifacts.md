# Artifacts

Run artifacts are written under:

```text
benchmarks/<name>/runs/<run-id>/
```

Generated artifacts should not be hand-edited. They are evidence for a concrete benchmark execution.

## Run-Level Files

- `config.yml`: run configuration. Relative paths are resolved from this file's directory.
- `run.log`: human-readable progress log mirrored from console output.
- `run-events.jsonl`: coarse runner lifecycle events such as start, interrupt, and completion.
- `manifest.jsonl`: append-only per-item state log.
- `manifest.latest.json`: latest state per item id, derived from `manifest.jsonl`.
- `results.jsonl`: structured result records, one per completed or errored item.
- `results.collated.jsonl`: generated report/collation output.
- `results.csv`: CSV report output.
- `report.md`: Markdown summary report.
- `student_output.md`: review-oriented Markdown with answer prompts, thoughts, and final answers.
- `plots/`: generated plots.
- `artifacts/`: per-item raw artifacts.

## Per-Item Layout

Each matrix item writes:

```text
artifacts/<item_id>/
  metadata.json
  parsed.json
  answer/
    args.json
    system-prompt.md
    output.json
    stderr.txt
    answer.txt
    thoughts.txt          # optional
    sessions/
  judge/
    args.json
    system-prompt.md
    prompt.txt
    output.json
    stderr.txt
    judge.txt
    sessions/
```

## `metadata.json`

`metadata.json` records the item context used by parser and reports:

- benchmark name
- run id
- item id
- case id
- question
- benchmark-specific expected fields, such as `judge_hint` or `expected_answer`
- tags
- answer model
- answer reasoning mode
- answer prompt id, description, and SHA-256
- judge model
- judge reasoning mode

## Answer Artifacts

- `answer/args.json`: exact `pi` argv list used for response generation.
- `answer/system-prompt.md`: answer system prompt text selected from `generation-system-prompt.yml`.
- `answer/output.json`: compact parsed final assistant text, thoughts, final message metadata, event count, and elapsed seconds.
- `answer/stderr.txt`: process stderr or dry-run marker.
- `answer/answer.txt`: human-readable final answer text.
- `answer/thoughts.txt`: captured thinking content when available.
- `answer/sessions/`: run-local `pi` session storage.

The answer user prompt is the case question. It appears in `answer/args.json`, `metadata.json`, and reports.

## Judge Artifacts

- `judge/args.json`: exact `pi` argv list used for rubric grading.
- `judge/system-prompt.md`: stable benchmark judge system prompt.
- `judge/prompt.txt`: rendered judge template used as the judge user prompt.
- `judge/output.json`: compact parsed final judge text, thoughts, final message metadata, event count, and elapsed seconds.
- `judge/stderr.txt`: process stderr or dry-run marker.
- `judge/judge.txt`: human-readable judge output.
- `judge/sessions/`: run-local `pi` session storage.

`judge/prompt.txt` is rendered from `evaluation-prompt-template.md` and includes
the case question, answer text, benchmark-specific expected fields, rubric, and
required output format.

## `output.json`

`runner.py` intentionally does not persist raw `pi --mode json` event streams. Instead it parses the final `message_end` event and stores compact output:

- `text`: final assistant text
- `thoughts`: captured thinking text, if present
- `metadata`: selected final message metadata such as provider, model, usage, stop reason, timestamp, response id, and error message
- `event_count`: number of JSON event lines seen
- `elapsed_seconds`: call duration
- `aborted`: present when interrupted

This keeps artifacts auditable without storing token deltas or streaming updates.

## Manifest States

`manifest.jsonl` is append-only. Common states include:

- `answer_running`
- `answer_complete`
- `judge_running`
- `judge_complete`
- `parsed`
- `complete`
- `interrupted`

Error result records use `status: error` and include the failing `phase`. A successful item is one whose latest manifest state is `complete` and whose state does not include `status: error`.

## Audit Checklist

When validating a run's input control:

- inspect `answer/args.json` and `judge/args.json`
- confirm both contain `--system-prompt`
- confirm neither contains `--append-system-prompt`
- confirm `answer/system-prompt.md` matches the selected answer prompt
- confirm `judge/system-prompt.md` contains the benchmark judge role
- confirm `judge/prompt.txt` contains the rendered benchmark rubric
- confirm `metadata.json` records the expected case fields and judge model ids
