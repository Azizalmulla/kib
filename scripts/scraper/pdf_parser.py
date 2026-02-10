"""PDF download and text extraction using PyMuPDF with OCR fallback."""

import os
import shutil
import tempfile
from typing import List, Optional

import requests

from .config import USER_AGENT

HAS_TESSERACT = shutil.which("tesseract") is not None


def download_pdf(url: str, timeout: int = 60) -> Optional[bytes]:
    """Download a PDF from a URL using requests. Returns raw bytes or None."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
        if b"%PDF" not in resp.content[:1024]:
            return None
        return resp.content
    except Exception as e:
        print(f"    [PDF-DL] requests failed: {e}")
        return None


def download_pdf_playwright(url: str, page) -> Optional[bytes]:
    """Download a PDF using Playwright's browser HTTP stack (bypasses SSL blocks)."""
    try:
        resp = page.request.get(url, timeout=60000)
        if resp.status >= 400:
            print(f"    [PDF-DL] HTTP {resp.status}")
            return None
        body = resp.body()
        if b"%PDF" not in body[:1024]:
            return None
        return body
    except Exception as e:
        print(f"    [PDF-DL] Playwright failed: {e}")
        return None


def extract_text_from_pdf(pdf_bytes: bytes) -> List[dict]:
    """Extract text page-by-page from PDF bytes.

    First tries native text extraction. If a page yields no text and
    tesseract is available, falls back to OCR.

    Returns a list of dicts: [{"page": 1, "text": "..."}]
    """
    import pymupdf

    pages = []
    ocr_pages = 0
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        doc = pymupdf.open(tmp_path)
        for i, pg in enumerate(doc):
            text = pg.get_text("text").strip()

            # OCR fallback for scanned pages
            if not text and HAS_TESSERACT:
                try:
                    tp = pg.get_textpage_ocr(flags=0, language="eng+ara", dpi=300)
                    text = pg.get_text("text", textpage=tp).strip()
                    if text:
                        ocr_pages += 1
                except Exception:
                    pass

            if text:
                pages.append({"page": i + 1, "text": text})
        doc.close()
    finally:
        os.unlink(tmp_path)

    if ocr_pages:
        print(f"    [OCR] {ocr_pages} page(s) required OCR")

    return pages


def detect_pdf_language(pages: List[dict]) -> str:
    """Detect language from extracted PDF pages."""
    sample = " ".join(p["text"][:200] for p in pages[:3])
    arabic_chars = sum(1 for c in sample if "\u0600" <= c <= "\u06FF")
    return "ar" if arabic_chars > len(sample) * 0.3 else "en"
