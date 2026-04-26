# Pi Benchmarks

Benchmark harnesses for running the real `pi` CLI and preserving artifacts for
inspection, reporting, and re-scoring.

## Quick Start

Launch the interactive benchmark helper:

```bash
./bench run bullshit-detector
```

Run a one-question dry-run from configured models:

```bash
./bench run bullshit-detector --providers plebchat --model-source configured --models plebchat/qwen/qwen3-coder-next --reasoning off --limit 1 --dry-run --yes
```

Generate a report from parsed results:

```bash
./bench report benchmarks/bullshit-detector/runs/<run-id>/config.yml
```
