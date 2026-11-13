"""Gold-evidence retrieval metrics and JSONL trace helpers."""

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _gold_items(question: dict[str, Any]) -> list[dict[str, str]]:
    alternatives = question.get("gold_evidence")
    if alternatives:
        return alternatives
    return [
        {
            "source_id": str(question["gold_source_id"]),
            "page": str(question["gold_page"]),
            "evidence_quote": str(question["evidence_quote"]),
        }
    ]


def evaluate_query(
    question: dict[str, Any], hits: list[dict[str, Any]]
) -> dict[str, Any]:
    """Mark a hit only when both the gold unit and its evidence occur in a chunk."""
    gold_items = _gold_items(question)
    evidence_hits = []
    for hit in hits:
        metadata = hit["metadata"]
        for gold in gold_items:
            same_unit = (
                str(metadata.get("document_id")) == gold["source_id"]
                and str(metadata.get("page")) == gold["page"]
            )
            quote = _normalized(gold["evidence_quote"])
            if same_unit and quote and quote in _normalized(hit["text"]):
                evidence_hits.append(hit["chunk_id"])
                break
    return {
        "question_id": question["id"],
        "split": question["split"],
        "hit_at_1": bool(evidence_hits and hits and evidence_hits[0] == hits[0]["chunk_id"]),
        "evidence_hit_at_3": bool(evidence_hits),
        "matching_chunk_ids": evidence_hits,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        grouped[(trace["strategy"], trace["split"])].append(trace)
    summary = {}
    for (strategy, split), rows in sorted(grouped.items()):
        total = len(rows)
        hit1 = sum(bool(row["metrics"]["hit_at_1"]) for row in rows)
        hit3 = sum(bool(row["metrics"]["evidence_hit_at_3"]) for row in rows)
        summary.setdefault(strategy, {})[split] = {
            "hit_at_1": {"hits": hit1, "total": total, "rate": hit1 / total if total else 0.0},
            "evidence_hit_at_3": {
                "hits": hit3,
                "total": total,
                "rate": hit3 / total if total else 0.0,
            },
        }
    return summary


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
