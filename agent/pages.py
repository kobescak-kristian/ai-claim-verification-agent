"""Local-HTML-only page access, scoped to evals/dataset/."""
from html.parser import HTMLParser
from pathlib import Path

from .config import DATASET_ROOT


class PathOutsideDatasetError(ValueError):
    pass


class _ParagraphExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.paragraphs = []
        self._in_title = False
        self._in_p = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "p":
            self._in_p = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "p":
            if self._buf:
                self.paragraphs.append("".join(self._buf).strip())
            self._in_p = False
            self._buf = []

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        if self._in_p:
            self._buf.append(data)


def resolve_dataset_path(rel_path: str) -> Path:
    """Resolve a path relative to evals/dataset/, rejecting any escape attempt."""
    if "\\" in rel_path:
        # Backslash is a path separator on Windows but a literal filename
        # character on POSIX — the same string escapes on one OS and not the
        # other. The cage treats it as a separator everywhere, so the bound
        # is OS-independent (design decision 2026-07-24: reject on all OS).
        raise PathOutsideDatasetError(
            f"Path '{rel_path}' resolves outside evals/dataset/ — rejected."
        )
    candidate = (DATASET_ROOT / rel_path).resolve()
    if not candidate.is_relative_to(DATASET_ROOT):
        raise PathOutsideDatasetError(
            f"Path '{rel_path}' resolves outside evals/dataset/ — rejected."
        )
    if not candidate.exists():
        raise FileNotFoundError(f"No such file under evals/dataset/: {rel_path}")
    return candidate


def read_page(rel_path: str) -> tuple[str, list[str]]:
    """Return (title, paragraphs) for an HTML file under evals/dataset/."""
    path = resolve_dataset_path(rel_path)
    parser = _ParagraphExtractor()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.title, parser.paragraphs
