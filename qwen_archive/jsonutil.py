"""Robust extraction of structured model output and embedded Subject JSON."""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .errors import IncompleteJsonError, InvalidModelResponseError


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).replace("\x00", "").strip()


def iter_text_values(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from iter_text_values(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_text_values(item)
        return
    text = normalize_text(value)
    if text:
        yield text


def strip_thinking(text: str) -> str:
    text = normalize_text(text)
    # Structured archive metadata never needs hidden reasoning. Remove complete
    # thinking blocks; an unterminated block is left for normal validation.
    return re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()


def _matching_json_end(text: str, start: int) -> int | None:
    opener = text[start]
    if opener not in "{[":
        return None
    stack = [opener]
    pairs = {"}": "{", "]": "["}
    in_string = False
    escape = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack[-1] != pairs[char]:
                raise InvalidModelResponseError("LLM response contained mismatched JSON delimiters.")
            stack.pop()
            if not stack:
                return index
    return None


def extract_json_text(text: str) -> str:
    cleaned = strip_thinking(text)
    cleaned = re.sub(r"^\s*```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()
    if not cleaned:
        raise InvalidModelResponseError("LLM response is empty.")

    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    starts = [index for index in (cleaned.find("{"), cleaned.find("[")) if index >= 0]
    if not starts:
        raise InvalidModelResponseError("LLM response did not contain a JSON object or array.")
    start = min(starts)
    end = _matching_json_end(cleaned, start)
    if end is None:
        raise IncompleteJsonError(
            "LLM response contained incomplete JSON. The output may have reached maxNewTokens."
        )
    candidate = cleaned[start : end + 1]
    try:
        json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise InvalidModelResponseError(f"LLM JSON could not be parsed: {exc}") from exc
    return candidate


def parse_json_response(text: str) -> Any:
    payload = extract_json_text(text)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:  # defensive; extract_json_text already validates.
        raise InvalidModelResponseError(f"LLM JSON could not be parsed: {exc}") from exc


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False)


def build_json_candidates(value: Any) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: Any) -> None:
        text = normalize_text(candidate)
        if not text or text in seen:
            return
        seen.add(text)
        candidates.append(text)
        try:
            extracted = extract_json_text(text)
        except Exception:
            return
        if extracted not in seen:
            seen.add(extracted)
            candidates.append(extracted)

    if isinstance(value, list):
        parts = [normalize_text(item) for item in iter_text_values(value)]
        for part in parts:
            add(part)
        if len(parts) > 1:
            for separator in (",", ",\n", "\n", " "):
                add(separator.join(parts))
    elif isinstance(value, dict):
        add(json.dumps(value, ensure_ascii=False))
        parts = [normalize_text(item) for item in iter_text_values(value)]
        for part in parts:
            add(part)
        if len(parts) > 1:
            add(",".join(parts))
    else:
        add(value)
    return candidates
