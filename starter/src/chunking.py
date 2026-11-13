"""Chunk source units while retaining exact character offsets and provenance."""

import hashlib
import re

from .models import Chunk, DocumentPage


def _make_chunk(page: DocumentPage, strategy: str, start: int, end: int) -> Chunk:
    text = page.text[start:end]
    digest = hashlib.sha256(
        f"{strategy}\0{page.document_id}\0{start}\0{end}\0{text}".encode("utf-8")
    ).hexdigest()[:24]
    return Chunk(
        chunk_id=f"{strategy}-{digest}",
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=text,
        metadata={
            **page.metadata,
            "unit_id": str(page.metadata["unit_id"]),
            "start_char": start,
            "end_char": end,
        },
    )


def fixed_size(page: DocumentPage, size: int = 1000, overlap: int = 150) -> list[Chunk]:
    """Split at bounded character windows, preferring nearby paragraph boundaries."""
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("size must be positive and overlap must be in [0, size)")
    chunks = []
    start = 0
    while start < len(page.text):
        end = min(start + size, len(page.text))
        if end < len(page.text):
            boundary = page.text.rfind("\n\n", start + size // 2, end)
            if boundary > start:
                end = boundary
        if end <= start:
            end = min(start + size, len(page.text))
        chunks.append(_make_chunk(page, "fixed", start, end))
        if end == len(page.text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _split_long_paragraph(
    page: DocumentPage, start: int, end: int, max_size: int, overlap: int
) -> list[Chunk]:
    result = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + max_size, end)
        if window_end < end:
            space = page.text.rfind(" ", cursor + max_size // 2, window_end)
            if space > cursor:
                window_end = space
        result.append(_make_chunk(page, "paragraph", cursor, window_end))
        if window_end == end:
            break
        cursor = max(cursor + 1, window_end - overlap)
    return result


def by_paragraph(
    page: DocumentPage, max_size: int = 1200, overlap: int = 100
) -> list[Chunk]:
    """Group adjacent paragraphs up to a size limit; split unusually long paragraphs."""
    if max_size <= 0 or overlap < 0 or overlap >= max_size:
        raise ValueError("max_size must be positive and overlap must be in [0, max_size)")
    spans = [
        (match.start(), match.end())
        for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", page.text, re.DOTALL)
    ]
    chunks = []
    group_start: int | None = None
    group_end: int | None = None

    def flush_group() -> None:
        nonlocal group_start, group_end
        if group_start is not None and group_end is not None:
            chunks.append(_make_chunk(page, "paragraph", group_start, group_end))
        group_start = None
        group_end = None

    for start, end in spans:
        paragraph_size = end - start
        if paragraph_size > max_size:
            flush_group()
            chunks.extend(_split_long_paragraph(page, start, end, max_size, overlap))
            continue
        if group_start is None:
            group_start, group_end = start, end
        elif end - group_start <= max_size:
            group_end = end
        else:
            flush_group()
            group_start, group_end = start, end
    flush_group()
    return chunks
