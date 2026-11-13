import unittest

from src.chunking import by_paragraph, fixed_size
from src.evaluation import evaluate_query
from src.ingestion import normalize_text
from src.models import DocumentPage


def make_page(text: str = "first paragraph.\n\nsecond paragraph.") -> DocumentPage:
    return DocumentPage(
        document_id="unit-1",
        source="https://docs.python.org/3/library/example.html",
        page="unit-1",
        text=text,
        metadata={"unit_id": "unit-1", "category": "example"},
    )


class NormalizeTextTests(unittest.TestCase):
    def test_normalizes_unicode_and_preserves_paragraph_breaks(self) -> None:
        normalized = normalize_text("Cafe\u0301\u00a0text\n\n\n  next   line")
        self.assertEqual(normalized, "Café text\n\n  next line")


class ChunkingTests(unittest.TestCase):
    def test_fixed_size_chunks_retain_offsets_and_source_text(self) -> None:
        page = make_page("abcdefghij" * 5)
        chunks = fixed_size(page, size=20, overlap=5)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            start = chunk.metadata["start_char"]
            end = chunk.metadata["end_char"]
            self.assertEqual(chunk.text, page.text[start:end])
            self.assertEqual(chunk.page, page.page)
        self.assertLessEqual(chunks[0].metadata["end_char"] - chunks[1].metadata["start_char"], 20)

    def test_paragraph_chunking_keeps_paragraphs_together(self) -> None:
        page = make_page("one paragraph.\n\nsecond paragraph.\n\nthird paragraph.")
        chunks = by_paragraph(page, max_size=40, overlap=5)

        self.assertEqual(len(chunks), 2)
        self.assertIn("one paragraph.", chunks[0].text)
        self.assertIn("second paragraph.", chunks[0].text)
        self.assertIn("third paragraph.", chunks[1].text)


class EvidenceMetricTests(unittest.TestCase):
    def test_hit_requires_both_gold_unit_and_quote(self) -> None:
        question = {
            "id": "q1",
            "split": "dev",
            "gold_source_id": "unit-1",
            "gold_page": "unit-1",
            "evidence_quote": "second paragraph",
        }
        unrelated = {
            "chunk_id": "other",
            "text": "second paragraph",
            "metadata": {"document_id": "unit-2", "page": "unit-2"},
        }
        correct = {
            "chunk_id": "gold",
            "text": "some second\nparagraph with evidence",
            "metadata": {"document_id": "unit-1", "page": "unit-1"},
        }

        self.assertFalse(evaluate_query(question, [unrelated])["evidence_hit_at_3"])
        self.assertTrue(evaluate_query(question, [correct])["hit_at_1"])
        self.assertTrue(evaluate_query(question, [unrelated, correct])["evidence_hit_at_3"])


if __name__ == "__main__":
    unittest.main()
