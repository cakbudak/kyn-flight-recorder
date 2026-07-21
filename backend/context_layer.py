"""Bounded, citation-first helpers for the standalone public context layer."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .contracts import ContractViolation, canonical_json, fingerprint


MAX_SOURCE_BYTES = 256 * 1024
MAX_SOURCE_LINES = 10_000
MAX_SOURCE_COUNT = 200
MAX_PASSAGE_RESULTS = 100
PASSAGE_LINES = 24
PASSAGE_OVERLAP = 4
SMART_READ_MODES = frozenset({"glance", "outline", "focus", "grep", "full"})
TOKEN_RE = re.compile(r"[\w-]{2,}", re.UNICODE)
HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+.+|(?:class|def|async\s+def|function|interface|type|enum)\s+[\w$.-]+|.+:\s*)$"
)


def normalize_source_text(value: Any) -> tuple[str, list[str]]:
    if not isinstance(value, str):
        raise ContractViolation("Knowledge content must be UTF-8 text")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in normalized:
        raise ContractViolation("Knowledge content may not contain NUL bytes")
    size = len(normalized.encode("utf-8"))
    if size == 0:
        raise ContractViolation("Knowledge content may not be empty")
    if size > MAX_SOURCE_BYTES:
        raise ContractViolation("Knowledge content exceeds 256 KiB")
    lines = normalized.split("\n")
    if len(lines) > MAX_SOURCE_LINES:
        raise ContractViolation("Knowledge content exceeds 10,000 lines")
    return normalized, lines


def build_passages(lines: Sequence[str]) -> list[dict[str, Any]]:
    passages: list[dict[str, Any]] = []
    start = 0
    ordinal = 1
    while start < len(lines):
        end = min(start + PASSAGE_LINES, len(lines))
        text = "\n".join(lines[start:end])
        if text.strip():
            material = {
                "ordinal": ordinal,
                "line_start": start + 1,
                "line_end": end,
                "text": text,
            }
            passages.append({**material, "fingerprint": fingerprint(material)})
            ordinal += 1
        if end == len(lines):
            break
        start = end - PASSAGE_OVERLAP
    return passages


def citation(version: Mapping[str, Any], line_start: int, line_end: int) -> dict[str, Any]:
    return {
        "source_id": version["source_id"],
        "source_version_id": version["id"],
        "source_version": int(version["version"]),
        "source_name": version["source_name"],
        "filename": version["filename"],
        "fingerprint": version["fingerprint"],
        "line_start": line_start,
        "line_end": line_end,
        "label": f"{version['filename']}:L{line_start}-L{line_end}",
    }


def _window(version: Mapping[str, Any], lines: Sequence[str], start: int, end: int) -> dict[str, Any]:
    return {
        "text": "\n".join(lines[start - 1 : end]),
        "citation": citation(version, start, end),
    }


def smart_read(
    version: Mapping[str, Any],
    *,
    mode: str,
    query: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    max_results: int = 12,
    max_full_bytes: int = 96 * 1024,
) -> dict[str, Any]:
    if mode not in SMART_READ_MODES:
        raise ContractViolation("SmartRead mode is invalid")
    content = str(version["content"])
    lines = content.split("\n")
    result: dict[str, Any] = {
        "mode": mode,
        "source": {
            "id": version["source_id"],
            "version_id": version["id"],
            "version": int(version["version"]),
            "name": version["source_name"],
            "filename": version["filename"],
            "media_type": version["media_type"],
            "fingerprint": version["fingerprint"],
            "line_count": int(version["line_count"]),
            "byte_count": int(version["byte_count"]),
        },
        "passages": [],
    }
    if mode == "full":
        if int(version["byte_count"]) > max_full_bytes:
            raise ContractViolation(
                "SmartRead full mode exceeds this Action's bound; use outline, grep, or focus"
            )
        result["passages"] = [_window(version, lines, 1, len(lines))]
    elif mode == "focus":
        if not isinstance(line_start, int) or isinstance(line_start, bool):
            raise ContractViolation("SmartRead focus requires an integer line_start")
        if not isinstance(line_end, int) or isinstance(line_end, bool):
            raise ContractViolation("SmartRead focus requires an integer line_end")
        if not 1 <= line_start <= line_end <= len(lines):
            raise ContractViolation("SmartRead focus line range is outside the source")
        if line_end - line_start + 1 > 160:
            raise ContractViolation("SmartRead focus may return at most 160 lines")
        result["passages"] = [_window(version, lines, line_start, line_end)]
    elif mode == "grep":
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ContractViolation("SmartRead grep requires a bounded literal query")
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= 30:
            raise ContractViolation("SmartRead max_results must be between one and thirty")
        needle = query.strip().casefold()
        matches: list[dict[str, Any]] = []
        occupied: list[tuple[int, int]] = []
        for index, line in enumerate(lines, start=1):
            if needle not in line.casefold():
                continue
            start = max(1, index - 2)
            end = min(len(lines), index + 2)
            if any(not (end < previous_start or start > previous_end) for previous_start, previous_end in occupied):
                continue
            occupied.append((start, end))
            matches.append({**_window(version, lines, start, end), "match_line": index})
            if len(matches) >= max_results:
                break
        result["query"] = query.strip()
        result["passages"] = matches
    elif mode == "outline":
        outline: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped and HEADING_RE.match(stripped):
                outline.append(
                    {
                        "text": stripped[:500],
                        "citation": citation(version, index, index),
                    }
                )
            if len(outline) >= 100:
                break
        if not outline:
            nonempty = [index for index, line in enumerate(lines, start=1) if line.strip()]
            for index in nonempty[:12]:
                outline.append(_window(version, lines, index, index))
        result["passages"] = outline
    else:
        informative = [index for index, line in enumerate(lines, start=1) if line.strip()]
        if informative:
            start = informative[0]
            end = min(len(lines), start + 39)
            result["passages"] = [_window(version, lines, start, end)]
        result["headings"] = [
            {
                "text": line.strip()[:300],
                "citation": citation(version, index, index),
            }
            for index, line in enumerate(lines, start=1)
            if line.strip() and HEADING_RE.match(line.strip())
        ][:12]
    result["result_fingerprint"] = fingerprint(result)
    return result


def query_terms(query: Any) -> list[str]:
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
        raise ContractViolation("search query must contain between one and 300 characters")
    terms = list(dict.fromkeys(token.casefold() for token in TOKEN_RE.findall(query)))
    if not terms:
        raise ContractViolation("search query contains no searchable terms")
    return terms[:16]


def score_text(text: str, terms: Sequence[str]) -> tuple[int, list[str]]:
    haystack = text.casefold()
    matched = [term for term in terms if term in haystack]
    if not matched:
        return 0, []
    occurrences = sum(min(haystack.count(term), 8) for term in matched)
    coverage = len(matched) * 100
    phrase_bonus = 40 if " ".join(terms) in haystack else 0
    return coverage + occurrences + phrase_bonus, matched


def source_snapshot_hash(run: Mapping[str, Any], evidence_event_ids: Sequence[str]) -> str:
    events = {event["id"]: event for event in run.get("events", [])}
    material = {
        "run_id": run["id"],
        "flow_version_id": run["flow_version_id"],
        "ledger_verified": bool(run.get("ledger_verified")),
        "events": [events[event_id] for event_id in sorted(evidence_event_ids) if event_id in events],
    }
    return fingerprint(material)


def memory_candidate_fingerprint(material: Mapping[str, Any]) -> str:
    return fingerprint(
        {
            "source_run_id": material["source_run_id"],
            "author_kind": material["author_kind"],
            "title": material["title"],
            "content": material["content"],
            "rationale": material["rationale"],
            "tags": sorted(material["tags"]),
            "evidence_event_ids": sorted(material["evidence_event_ids"]),
            "source_snapshot_hash": material["source_snapshot_hash"],
        }
    )


def json_size(value: Any) -> int:
    return len(canonical_json(value).encode("utf-8"))
