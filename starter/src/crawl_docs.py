"""Crawl a small, explicit set of pages from the official Python documentation."""

import csv
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from .ingestion import normalize_text

BASE_URL = "https://docs.python.org/3/"
LICENSE_URL = urljoin(BASE_URL, "license.html")
MIN_UNIT_CHARACTERS = 1500
REQUEST_DELAY_SECONDS = 0.25
USER_AGENT = "Day06DataIngestionStudentLab/1.0"
REPO_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_DIR / "data"

DOCS_PAGES = [
    ("library_argparse", "Command-line parsing", "application-development", "library/argparse.html"),
    ("library_asyncio", "Asynchronous I/O", "concurrency", "library/asyncio.html"),
    ("library_collections", "Container datatypes", "data-structures", "library/collections.html"),
    ("library_concurrent_futures", "Concurrent execution", "concurrency", "library/concurrent.futures.html"),
    ("library_csv", "CSV file reading and writing", "data-formats", "library/csv.html"),
    ("library_dataclasses", "Data classes", "data-modeling", "library/dataclasses.html"),
    ("library_datetime", "Date and time types", "date-and-time", "library/datetime.html"),
    ("library_decimal", "Decimal fixed-point arithmetic", "numeric-computing", "library/decimal.html"),
    ("library_functools", "Higher-order functions", "data-structures", "library/functools.html"),
    ("library_hashlib", "Secure hashes and message digests", "security-and-cryptography", "library/hashlib.html"),
    ("library_heapq", "Heap queue algorithm", "data-structures", "library/heapq.html"),
    ("library_itertools", "Functions creating iterators", "data-structures", "library/itertools.html"),
    ("library_json", "JSON encoder and decoder", "data-formats", "library/json.html"),
    ("library_logging", "Logging facility", "application-development", "library/logging.html"),
    ("library_math", "Mathematical functions", "numeric-computing", "library/math.html"),
    ("library_os", "Operating system interfaces", "files-and-operating-system", "library/os.html"),
    ("library_pathlib", "Object-oriented filesystem paths", "files-and-operating-system", "library/pathlib.html"),
    ("library_random", "Generate pseudo-random numbers", "numeric-computing", "library/random.html"),
    ("library_re", "Regular expression operations", "text-processing", "library/re.html"),
    ("library_shutil", "High-level file operations", "files-and-operating-system", "library/shutil.html"),
    ("library_socket", "Low-level networking interface", "networking", "library/socket.html"),
    ("library_sqlite3", "SQLite database interface", "data-storage", "library/sqlite3.html"),
    ("library_statistics", "Mathematical statistics functions", "numeric-computing", "library/statistics.html"),
    ("library_string", "Common string operations", "text-processing", "library/string.html"),
    ("library_subprocess", "Subprocess management", "process-management", "library/subprocess.html"),
    ("library_sys", "System-specific parameters and functions", "runtime-and-imports", "library/sys.html"),
    ("library_tempfile", "Generate temporary files and directories", "files-and-operating-system", "library/tempfile.html"),
    ("library_textwrap", "Text wrapping and filling", "text-processing", "library/textwrap.html"),
    ("library_threading", "Thread-based parallelism", "concurrency", "library/threading.html"),
    ("library_typing", "Support for type hints", "data-modeling", "library/typing.html"),
    ("library_unittest", "Unit testing framework", "testing", "library/unittest.html"),
    ("library_urllib_request", "URL request handling", "networking", "library/urllib.request.html"),
    ("library_uuid", "UUID objects", "data-modeling", "library/uuid.html"),
]

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_BLOCK_TAGS = {
    "address", "blockquote", "br", "dd", "div", "dl", "dt", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "pre", "section",
    "table", "td", "th", "tr", "ul",
}
_IGNORED_TAGS = {"aside", "footer", "header", "nav", "script", "style", "svg"}
_IGNORED_CLASSES = {
    "breadcrumb",
    "headerlink",
    "related",
    "sphinxsidebar",
    "sphinxsidebarwrapper",
    "toctree-wrapper",
}


class _PythonDocsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.stack: list[tuple[str, bool, bool]] = []
        self.main_depth = 0
        self.blocked_depth = 0
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        is_void = tag in _VOID_TAGS
        is_main = self.main_depth == 0 and (
            tag == "main" or (tag == "div" and "body" in classes)
        )
        is_blocked = self.blocked_depth > 0 or tag in _IGNORED_TAGS or bool(
            classes & _IGNORED_CLASSES
        )
        if tag == "title":
            self.in_title = True
        if is_main:
            self.main_depth += 1
        elif self.main_depth > 0 and not is_void:
            self.main_depth += 1
        if is_blocked and not is_void:
            self.blocked_depth += 1
        if self.main_depth > 0 and self.blocked_depth == 0 and tag in _BLOCK_TAGS:
            self.parts.append("\n\n")
        if not is_void:
            self.stack.append((tag, is_main, is_blocked))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        matching_index = next(
            (index for index in range(len(self.stack) - 1, -1, -1)
             if self.stack[index][0] == tag),
            None,
        )
        if matching_index is None:
            return
        removed = self.stack[matching_index:]
        del self.stack[matching_index:]
        for _, is_main, is_blocked in reversed(removed):
            if is_blocked:
                self.blocked_depth -= 1
            if is_main or self.main_depth > 0:
                self.main_depth -= 1
        if self.main_depth > 0 and self.blocked_depth == 0 and tag in _BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.main_depth > 0 and self.blocked_depth == 0:
            self.parts.append(data)


def _read_page(url: str) -> tuple[str, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        content_type = response.headers.get_content_type()
        if content_type != "text/html":
            raise ValueError(f"Expected HTML, received {content_type}")
        final_url = response.geturl()
        if urlparse(final_url).hostname != "docs.python.org":
            raise ValueError(f"Unexpected redirect outside docs.python.org: {final_url}")
        html = response.read().decode(response.headers.get_content_charset() or "utf-8")
    parser = _PythonDocsParser()
    parser.feed(html)
    if not parser.parts:
        raise ValueError("Could not locate the main documentation body")
    title = normalize_text(" ".join(parser.title_parts))
    text = normalize_text("".join(parser.parts))
    if not title or len(text) < MIN_UNIT_CHARACTERS:
        raise ValueError(f"Extracted only {len(text)} content characters")
    return title, text


def _documentation_version(title: str) -> str:
    match = re.search(r"Python (\d+\.\d+(?:\.\d+)?) documentation", title)
    return match.group(1) if match else "Python 3 documentation; current stable alias"


def _robots_parser() -> RobotFileParser:
    parser = RobotFileParser("https://docs.python.org/robots.txt")
    request = Request(parser.url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        parser.parse(response.read().decode("utf-8").splitlines())
    return parser


def crawl(output_dir: Path = OUTPUT_DIR) -> dict[str, int | str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    robots = _robots_parser()
    manifest_rows: list[dict[str, str | int]] = []
    documents: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    accessed_at = datetime.now(timezone.utc).isoformat()

    for unit_id, label, category, path in DOCS_PAGES:
        url = urljoin(BASE_URL, path)
        if not robots.can_fetch(USER_AGENT, url):
            errors.append({"unit_id": unit_id, "url": url, "error": "Disallowed by robots.txt"})
            continue
        try:
            title, text = _read_page(url)
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, ValueError, OSError) as exc:
            errors.append({"unit_id": unit_id, "url": url, "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        documentation_version = _documentation_version(title)
        documents.append(
            {
                "unit_id": unit_id,
                "document_id": unit_id,
                "title": title or label,
                "url": url,
                "category": category,
                "text": text,
                "content_sha256": content_hash,
                "accessed_at": accessed_at,
                "license": "Python Software Foundation License Version 2",
                "license_url": LICENSE_URL,
                "documentation_version": documentation_version,
            }
        )
        manifest_rows.append(
            {
                "source_id": unit_id,
                "unit_id": unit_id,
                "title": title or label,
                "source_path_or_url": url,
                "license_or_permission": "Python Software Foundation License Version 2",
                "license_url": LICENSE_URL,
                "accessed_at": accessed_at,
                "documentation_version": documentation_version,
                "category": category,
                "content_characters": len(text),
                "content_sha256": content_hash,
            }
        )
        time.sleep(REQUEST_DELAY_SECONDS)

    documents_path = output_dir / "python_docs.jsonl"
    manifest_path = output_dir / "manifest.csv"
    errors_path = output_dir / "crawl_errors.csv"
    documents_path.write_text(
        "".join(json.dumps(document, ensure_ascii=False) + "\n" for document in documents),
        encoding="utf-8",
    )
    with manifest_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(manifest_rows[0]) if manifest_rows else [
            "source_id", "unit_id", "title", "source_path_or_url",
            "license_or_permission", "license_url", "accessed_at",
            "documentation_version", "category", "content_characters", "content_sha256",
        ])
        writer.writeheader()
        writer.writerows(manifest_rows)
    with errors_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["unit_id", "url", "error"])
        writer.writeheader()
        writer.writerows(errors)

    if len(documents) < 30:
        raise RuntimeError(
            f"Crawl produced {len(documents)} valid source units; at least 30 are required. "
            f"See {errors_path}."
        )
    return {
        "valid_units": len(documents),
        "failed_units": len(errors),
        "manifest": str(manifest_path),
        "corpus": str(documents_path),
    }


if __name__ == "__main__":
    print(json.dumps(crawl(), indent=2, ensure_ascii=False))
