#!/usr/bin/env python3
"""Configure and launch a SkibidiBench run."""

from __future__ import annotations

import sys
from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from benchmark_launcher import BenchmarkSpec, main_for_spec  # noqa: E402


SPEC = BenchmarkSpec(
    name="skibidi",
    benchmark_dir=BENCHMARK_DIR,
    description=__doc__ or "Configure and launch a SkibidiBench run.",
)


def main() -> int:
    return main_for_spec(SPEC)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        raise SystemExit(130) from None
