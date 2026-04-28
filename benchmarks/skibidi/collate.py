#!/usr/bin/env python3
"""Parse SkibidiBench judge output into a normalized JSON record."""

from __future__ import annotations

import sys
from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parents[1]
sys.path.insert(0, str(REPO_ROOT))

from score_description_parser import main as parse_score_description  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(parse_score_description(min_score=0, max_score=1))
