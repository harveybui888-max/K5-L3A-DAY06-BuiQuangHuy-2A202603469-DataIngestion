"""Load crawled documentation units and normalize their text."""

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from .models import DocumentPage


def normalize_text(text: str) -> str:
    """Normalize Unicode and whitespace while preserving paragraph boundaries."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    lines = []
    for line in text.splitlines():
        indentation = len(line) - len(line.lstrip(" \t"))
        content = re.sub(r"[ \t]+", " ", line[indentation:].strip())
        lines.append((" " * min(indentation, 8)) + content if content else "")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def load_corpus(path: Path) -> list[DocumentPage]:
    """Load one normalized source unit per JSONL record."""
    pages = []
    seen_ids: set[str] = set()
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            try:
                record: dict[str, Any] = json.loads(line)
                unit_id = str(record["unit_id"])
                document_id = str(record.get("document_id", unit_id))
                text = normalize_text(str(record["text"]))
                if not unit_id or not text:
                    raise ValueError("unit_id and text must be non-empty")
                if unit_id in seen_ids:
                    raise ValueError(f"Duplicate unit_id: {unit_id}")
                seen_ids.add(unit_id)
                pages.append(
                    DocumentPage(
                        document_id=document_id,
                        source=str(record["url"]),
                        page=unit_id,
                        text=text,
                        metadata={
                            "unit_id": unit_id,
                            "title": str(record["title"]),
                            "category": str(record["category"]),
                            "accessed_at": str(record["accessed_at"]),
                            "license": str(record["license"]),
                            "documentation_version": str(record["documentation_version"]),
                        },
                    )
                )
            except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"Invalid corpus record at line {line_number}: {exc}") from exc
    return pages
