"""Extract clean text from HTML using trafilatura with BeautifulSoup fallback."""

import re
from typing import Optional, Tuple

from bs4 import BeautifulSoup

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

from .config import MIN_TEXT_LENGTH

_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def extract_text(html: str) -> Optional[str]:
    text = None

    if HAS_TRAFILATURA:
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )

    if not text:
        text = _bs4_fallback(html)

    if not text:
        return None

    text = _normalize_whitespace(text)

    if len(text) < MIN_TEXT_LENGTH:
        return None

    return text


def _bs4_fallback(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.find("div", {"role": "main"})
    target = main if main else soup.body

    if not target:
        return None

    return target.get_text(separator="\n", strip=True)


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return "Untitled"


def detect_language(text: str) -> str:
    arabic_chars = len(_ARABIC_RE.findall(text))
    total_alpha = sum(1 for c in text if c.isalpha())
    if total_alpha == 0:
        return "en"
    ratio = arabic_chars / total_alpha
    return "ar" if ratio > 0.3 else "en"
