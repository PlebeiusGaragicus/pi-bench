"""Shared parser for line-oriented benchmark judge scores."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DESCRIPTION_RE = re.compile(r"^\s*Description\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def score_pattern(min_score: int, max_score: int) -> re.Pattern[str]:
    allowed = "".join(str(score) for score in range(min_score, max_score + 1))
    return re.compile(rf"^\s*Score\s*:\s*([{allowed}])\s*$", re.IGNORECASE | re.MULTILINE)


def score_label(min_score: int, max_score: int) -> str:
    if min_score == 0 and max_score == 1:
        return "0 or 1"
    return ", ".join(str(score) for score in range(min_score, max_score + 1))


def parse_judge_output(text: str, min_score: int, max_score: int) -> dict[str, Any]:
    score_match = score_pattern(min_score, max_score).search(text)
    description_match = DESCRIPTION_RE.search(text)
    if not score_match:
        raise ValueError(f"judge output is missing 'Score: <{score_label(min_score, max_score)}>'")
    if not description_match:
        raise ValueError("judge output is missing 'Description: ...'")

    return {
        "score": int(score_match.group(1)),
        "description": description_match.group(1).strip(),
        "raw_judge_output": text,
    }


def parse_and_write(metadata_path: Path, judge_output_path: Path, output_path: Path, min_score: int, max_score: int) -> dict[str, Any]:
    metadata = read_json(metadata_path)
    judge_text = judge_output_path.read_text(encoding="utf-8")
    parsed = parse_judge_output(judge_text, min_score, max_score)
    record = {**metadata, **parsed}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def main(min_score: int, max_score: int) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--judge-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    record = parse_and_write(args.metadata, args.judge_output, args.output, min_score, max_score)
    print(json.dumps(record, sort_keys=True))
    return 0
