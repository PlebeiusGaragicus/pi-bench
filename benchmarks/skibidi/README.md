SkibidiBench measures whether models can explain circa 2024-2025 internet slang
usually associated with Generation Alpha and adjacent meme culture.

Each case asks about one slang term or phrase. The answer model receives only
the question plus one selected system prompt from `generation-system-prompt.yml`.
The judge receives the question, the model response, and the expected answer
guidance from `evaluation-dataset.yml` through the rendered
`evaluation-prompt-template.md` user prompt, plus the controlled benchmark judge
system prompt from `runner.py`. See `../../docs/model-inputs.md` for the full
model input contract.

The term data lives in `evaluation-dataset.yml`; do not duplicate it in this README.

## Launching A Run

Use the root benchmark wrapper from the repository root:

```bash
./bench run skibidi
```

The launcher follows the same flow as the other benchmarks: it checks for
interrupted runs, reads providers from `~/.pi/agent/models.json`, asks which
providers and models to use, writes `runs/<run-id>/config.yml`, and launches the
root `runner.py`.

The runner schedules answer calls in model-grouped blocks for self-hosted
inference: all cases for one answer model/reasoning/prompt combination run
before moving to the next combination.

For a non-interactive dry run:

```bash
./bench run skibidi --providers plebchat --model-source configured --models plebchat/qwen/qwen3-coder-next --reasoning off --answer-prompts baseline-helpful --judge-model plebchat/qwen/qwen3-coder-next --judge-reasoning off --limit 1 --dry-run --yes
```

## Answer Prompts

Answer prompts are benchmark inputs. Named variants live in
`generation-system-prompt.yml`, and each run config selects one or more prompt ids:

```yaml
answer_prompt_file: ../../generation-system-prompt.yml
answer_prompts:
  - baseline-helpful
```

The runner expands prompts as a first-class matrix dimension:
`case x model x reasoning x answer_prompt`.

`run.py` is a thin wrapper over the shared `benchmark_launcher.py`. `collate.py`
uses the shared `score_description_parser.py` with this benchmark's binary
score range.

## Score Rubric

- `0`: The model did not provide a satisfactory answer. It is wrong, too vague,
  missing an essential part of the term, or leaves the user with a false
  understanding.
- `1`: The model provided a satisfactory answer. It explains the slang meaning
  accurately enough for a user to rely on it.

Judge output is line-oriented:

```text
Score: 1
Description: The model correctly explained the slang meaning and relevant context.
```

