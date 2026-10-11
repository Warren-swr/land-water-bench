"""Versioned final-answer extraction, independent of model identity and ground truth."""

from __future__ import annotations

import re

from .core import extract_answer, parse_label, response_is_filtered

LEGACY_VERSION = "land-water-answer/2.1-boundary-lines"
VERSION = "land-water-answer/2.2-explicit-labels"
SUPPORTED_VERSIONS = {LEGACY_VERSION, VERSION}
PREFIX = re.compile(
    r"(?:(?:final\s+answer|answer|label|classification|conclusion)\s*[:：=]\s*"
    r"|(?:the\s+)?(?:final\s+)?answer\s+is\s+"
    r"|(?:最终答案|答案|结论)\s*[:：]\s*)", re.I)
WRAPPERS = ("***", "___", "**", "__", "*", "_", "`")
FENCE = re.compile(r"(```|~~~)(?:text|plaintext)?", re.I)
UNCERTAIN = re.compile(r"\b(?:not\s+(?:sure|certain)|uncertain|maybe|perhaps|probably|possibly|"
                       r"cannot\s+(?:tell|determine)|(?:could|might|may)\s+be)\b|不确定|可能|无法判断", re.I)
UNCERTAIN_LABEL = re.compile(
    r"\b(?:not\s+(?:sure|certain)|uncertain|cannot\s+(?:tell|determine))\b|不确定|无法判断"
    r"|\b(?:maybe|perhaps|probably|possibly|(?:could|might|may)\s+be)\s+"
    r"(?:(?:on|over|in|actually|mostly|a|the)\s+){0,3}(?:land|water)\b", re.I)


def unwrap(value: str) -> str:
    for _ in range(4):
        for wrapper in WRAPPERS:
            if len(value) > 2 * len(wrapper) and value.startswith(wrapper) and value.endswith(wrapper):
                value = value[len(wrapper):-len(wrapper)].strip()
                break
        else:
            break
    return value


def line_label(line: str) -> int | None:
    value = unwrap(line.strip())
    match = PREFIX.match(value)
    if match:
        value = value[match.end():].strip()
    outer_punctuation = bool(re.search(r"[.!。！]$", value))
    value = re.sub(r"[.!。！]$", "", value).strip()
    value = unwrap(value)
    if outer_punctuation and re.search(r"[.!。！]$", value):
        return None
    value = re.sub(r"[.!。！]$", "", value).strip()
    return parse_label(value)


def parse_final_answer(text: str, *, version: str = VERSION) -> dict:
    """Prefer a terminal answer; accept a leading answer only without a contrary label."""
    if version not in SUPPORTED_VERSIONS:
        raise ValueError("Unsupported classification answer parser")
    result = {"version": version, "label": None, "rule": None, "reason": None,
              "span": None, "matched_text": None}
    lines = []
    offset = 0
    for raw in text.splitlines(keepends=True):
        stripped = raw.strip()
        if stripped:
            start = offset + len(raw) - len(raw.lstrip())
            lines.append((stripped, start, start + len(stripped)))
        offset += len(raw)
    if not lines:
        return {**result, "reason": "empty_text"}
    open_fence = None
    for line, _, _ in lines:
        if line.startswith(("```", "~~~")):
            if open_fence is None:
                open_fence = line[:3]
            elif line == open_fence:
                open_fence = None
    if open_fence:
        return {**result, "reason": "unclosed_code_fence"}
    index = len(lines) - 1
    rule = "final_line"
    previous = index - 1
    if lines[index][0] in {"```", "~~~"}:
        opening = FENCE.fullmatch(lines[index - 2][0]) if index >= 2 else None
        if not opening or opening.group(1) != lines[index][0]:
            return {**result, "reason": "no_explicit_final_answer"}
        index -= 1
        previous = index - 2
        rule = "fenced_final_line"
    candidate, start, end = lines[index]
    label = line_label(candidate)
    if label is None:
        first, first_start, first_end = lines[0]
        leading = line_label(first) if len(lines) > 1 else None
        if leading is None:
            return {**result, "reason": "no_explicit_final_answer"}
        explanation = text[first_end:]
        opposite = "water" if leading else "land"
        if version == LEGACY_VERSION:
            conflict = opposite in {word.lower() for word in re.findall(r"\b(?:land|water)\b", explanation, re.I)}
        else:
            # Geography prose can mention both categories without changing the answer.
            # Reject contrary answer lines or explicit assertions, not incidental words.
            conflict = any(line_label(line) == 1 - leading for line, _, _ in lines[1:])
            assertions = (
                r"\b(?:answer|classification|label)\s*(?:is|:|=)\s*",
                r"\b(?:the\s+(?:point|location|coordinate)|it|this)\s+is\s+(?:on\s+|over\s+)?",
            )
            plain = re.sub(r"[*_`]", "", explanation)
            conflict = conflict or any(re.search(prefix + opposite + r"\b", plain, re.I)
                                       for prefix in assertions)
        if conflict:
            return {**result, "reason": "conflicting_answer_labels"}
        if (UNCERTAIN if version == LEGACY_VERSION else UNCERTAIN_LABEL).search(explanation):
            return {**result, "reason": "ambiguous_explanation"}
        return {**result, "label": leading, "rule": "leading_answer", "span": [first_start, first_end],
                "matched_text": text[first_start:first_end]}
    # Adjacent explicit answers with different labels are ambiguous, even across blank lines.
    while previous >= 0:
        other = line_label(lines[previous][0])
        if other is None:
            break
        if other != label:
            return {**result, "reason": "conflicting_final_answers"}
        previous -= 1
    if parse_label(text) is not None:
        rule = "exact_word"
    return {**result, "label": label, "rule": rule, "span": [start, end],
            "matched_text": text[start:end]}


def reextract_record(record: dict, wire: str) -> dict:
    """Project a raw record for a new scoring version without modifying its source."""
    row = {k: v for k, v in record.items() if k not in {"request", "response"}}
    row.update(original_status=record["status"], original_label=record.get("label"))
    if record["status"] == "infra_error":
        row["extraction"] = {"version": VERSION, "label": None, "rule": None,
                             "reason": "infra_error", "span": None, "matched_text": None}
        return row
    text, complete = extract_answer(wire, record["response"])
    if response_is_filtered(record["response"]):
        row.update(text=text, complete_response=False, status="infra_error", label=None,
                   error={"type": "content_filter"},
                   extraction={"version": VERSION, "label": None, "rule": None,
                               "reason": "content_filter", "span": None, "matched_text": None})
        return row
    if (record["response"].get("incomplete_details") or {}).get("reason") == "max_output_tokens":
        complete = False
    if text != record["text"]:
        raise ValueError("Stored final text differs from the raw response")
    answer = parse_final_answer(text) if complete else {
        "version": VERSION, "label": None, "rule": None, "reason": "incomplete_response",
        "span": None, "matched_text": None}
    row.update(label=answer["label"], status="ok" if answer["label"] is not None else "invalid",
               extraction=answer)
    if record["status"] == "ok" and (row["status"] != "ok" or row["label"] != record["label"]):
        raise ValueError("Compatible parsing changed a previously valid answer")
    return row
