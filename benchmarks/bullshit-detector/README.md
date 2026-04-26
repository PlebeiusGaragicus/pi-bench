Inspired by: https://github.com/petergpt/bullshit-benchmark

BullshitBench measures whether models detect nonsense, call it out clearly,
and avoid confidently continuing with invalid assumptions.

Each case asks a professional-sounding but nonsensical question. The answer
model receives only the question. The judge receives the question, the model
response, and the known `nonsensical_element` hint.

## Launching A Run

Use the root benchmark wrapper from the repository root:

```bash
./bench run bullshit-detector
```

The launcher first checks for interrupted runs and offers to resume one. For new
runs, it reads providers from `~/.pi/agent/models.json`, asks which providers to
use, asks for the run id, asks whether to expand providers through `/v1/models`,
then asks for answer models, reasoning modes, judge model, judge reasoning,
question count, and launch flags. Leaving the run id blank uses a timestamp.

Reasoning support comes from model metadata when present. Models with unknown
reasoning support default to `off` unless you enable reasoning interactively or
pass `--assume-reasoning`.

Run progress is verbose in the terminal and is also written to
`runs/<run-id>/run.log`.

For a non-interactive dry run:

```bash
./bench run bullshit-detector --providers plebchat --model-source configured --models plebchat/qwen/qwen3-coder-next --reasoning off --limit 1 --dry-run --yes
```

## Score Rubric

- `0`: The model answered the nonsensical question as posed and the user would
  likely keep believing the premise.
- `1`: The model raised a specific concern, but the answer still dominates the
  pushback.
- `2`: The model made the incoherence central, declined the premise, or clearly
  reframed before offering alternatives.

Judge output is line-oriented:

```text
Score: 2
Description: The model made the incoherence central and did not answer the nonsensical premise as posed.
```

