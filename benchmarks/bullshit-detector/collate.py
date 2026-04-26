#!/usr/bin/env python3
"""Parse BullshitBench judge output into a normalized JSON record."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SCORE_RE = re.compile(r"^\s*Score\s*:\s*([0-2])\s*$", re.IGNORECASE | re.MULTILINE)
DESCRIPTION_RE = re.compile(r"^\s*Description\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_judge_output(text: str) -> dict[str, Any]:
    score_match = SCORE_RE.search(text)
    description_match = DESCRIPTION_RE.search(text)
    if not score_match:
        raise ValueError("judge output is missing 'Score: <0, 1, or 2>'")
    if not description_match:
        raise ValueError("judge output is missing 'Description: ...'")

    return {
        "score": int(score_match.group(1)),
        "description": description_match.group(1).strip(),
        "raw_judge_output": text,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--judge-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    metadata = read_json(args.metadata)
    judge_text = args.judge_output.read_text(encoding="utf-8")
    parsed = parse_judge_output(judge_text)
    record = {**metadata, **parsed}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
