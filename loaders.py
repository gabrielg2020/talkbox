"""Read source documents into plain text for synthesis.

A small registry maps a file extension to how its text is extracted and, where
extraction can be imperfect, a caveat shown to the user before they commit. Libs
are imported lazily so a missing optional dependency only bites the format that
needs it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


@dataclass
class Loader:
    extract: Callable[[Path], str]
    caveat: Optional[str] = None  # warn + confirm before synth when extraction may be rough


def _load_txt(path):
    return path.read_text()


def _load_md(path):
    import markdown
    from bs4 import BeautifulSoup

    html = markdown.markdown(path.read_text())
    return BeautifulSoup(html, "html.parser").get_text("\n")


def _load_docx(path):
    from docx import Document

    return "\n".join(p.text for p in Document(str(path)).paragraphs)


# Reliable formats carry no caveat. A future best-effort format (e.g. PDF) would
# set one, and do_generate warns automatically — no other wiring needed:
#   ".pdf": Loader(_load_pdf, "PDFs store layout, not clean text, so word order "
#                             "and headers/footers may come out imperfect."),
LOADERS = {
    ".txt": Loader(_load_txt),
    ".md": Loader(_load_md),
    ".docx": Loader(_load_docx),
}

SUPPORTED_GLOBS = [f"*{ext}" for ext in LOADERS]


def load_text(path):
    return LOADERS[path.suffix.lower()].extract(path)


def caveat_for(path):
    loader = LOADERS.get(path.suffix.lower())
    return loader.caveat if loader else None
