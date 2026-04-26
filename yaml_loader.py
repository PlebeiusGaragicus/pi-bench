"""Small YAML subset loader for benchmark config and case files.

This intentionally supports only the structures used by the benchmark files:
mappings, lists, scalar values, and literal block scalars (`|`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class YamlSubsetError(ValueError):
    pass


def load_yaml(path: Path) -> Any:
    return loads(path.read_text(encoding="utf-8"))


def loads(text: str) -> Any:
    parser = _Parser(text.splitlines())
    value, index = parser.parse_block(0, 0)
    parser.skip_empty(index)
    return value


class _Parser:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def skip_empty(self, index: int) -> int:
        while index < len(self.lines):
            stripped = self.lines[index].strip()
            if stripped and not stripped.startswith("#"):
                break
            index += 1
        return index

    def indent_of(self, index: int) -> int:
        line = self.lines[index]
        return len(line) - len(line.lstrip(" "))

    def parse_block(self, index: int, indent: int) -> tuple[Any, int]:
        index = self.skip_empty(index)
        if index >= len(self.lines):
            return {}, index
        if self.indent_of(index) < indent:
            return {}, index
        if self.lines[index].lstrip().startswith("- "):
            return self.parse_list(index, indent)
        return self.parse_mapping(index, indent)

    def parse_mapping(self, index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while index < len(self.lines):
            index = self.skip_empty(index)
            if index >= len(self.lines) or self.indent_of(index) < indent:
                break
            current_indent = self.indent_of(index)
            if current_indent > indent:
                raise YamlSubsetError(f"Unexpected indentation on line {index + 1}")
            stripped = self.lines[index].strip()
            if stripped.startswith("- "):
                break
            key, raw_value = self.split_key_value(stripped, index)
            index += 1
            result[key] = self.parse_value(raw_value, index, current_indent)
            if isinstance(result[key], _PendingNested):
                result[key], index = self.parse_block(index, current_indent + 2)
            elif isinstance(result[key], _PendingLiteral):
                result[key], index = self.parse_literal(index, current_indent + 2)
        return result, index

    def parse_list(self, index: int, indent: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while index < len(self.lines):
            index = self.skip_empty(index)
            if index >= len(self.lines) or self.indent_of(index) < indent:
                break
            if self.indent_of(index) != indent:
                raise YamlSubsetError(f"Unexpected list indentation on line {index + 1}")
            stripped = self.lines[index].strip()
            if not stripped.startswith("- "):
                break
            item_text = stripped[2:].strip()
            index += 1

            if not item_text:
                item, index = self.parse_block(index, indent + 2)
                result.append(item)
                continue

            if ":" in item_text and not item_text.startswith(("'", '"')):
                key, raw_value = self.split_key_value(item_text, index - 1)
                item: dict[str, Any] = {}
                value = self.parse_value(raw_value, index, indent)
                if isinstance(value, _PendingNested):
                    value, index = self.parse_block(index, indent + 2)
                elif isinstance(value, _PendingLiteral):
                    value, index = self.parse_literal(index, indent + 2)
                item[key] = value
                extra, index = self.parse_mapping(index, indent + 2)
                item.update(extra)
                result.append(item)
            else:
                result.append(parse_scalar(item_text))
        return result, index

    def parse_value(self, raw_value: str, index: int, indent: int) -> Any:
        raw_value = raw_value.strip()
        if raw_value == "":
            return _PendingNested()
        if raw_value == "|":
            return _PendingLiteral()
        return parse_scalar(raw_value)

    def parse_literal(self, index: int, indent: int) -> tuple[str, int]:
        chunks: list[str] = []
        while index < len(self.lines):
            line = self.lines[index]
            if line.strip() == "":
                chunks.append("")
                index += 1
                continue
            current_indent = self.indent_of(index)
            if current_indent < indent:
                break
            chunks.append(line[indent:])
            index += 1
        return "\n".join(chunks).rstrip() + "\n", index

    def split_key_value(self, stripped: str, index: int) -> tuple[str, str]:
        if ":" not in stripped:
            raise YamlSubsetError(f"Expected key/value pair on line {index + 1}")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise YamlSubsetError(f"Missing key on line {index + 1}")
        return key, raw_value


class _PendingNested:
    pass


class _PendingLiteral:
    pass


def parse_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value
