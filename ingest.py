import io
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse


def split_text(text: str, chunk_size: int = 400, overlap: int = 40) -> list[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        if len(chunk.strip()) > 60:
            chunks.append(chunk.strip())
        i += chunk_size - overlap
    return chunks


def ingest_pdf(content: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return split_text(text)


def ingest_docx(content: bytes) -> list[str]:
    from docx import Document

    doc = Document(io.BytesIO(content))
    text = "\n".join(para.text for para in doc.paragraphs)
    return split_text(text)


def _extract_text(soup: BeautifulSoup) -> list[str]:
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return split_text(text)


def ingest_url(url: str) -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ChatbotBot/1.0)"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return _extract_text(soup)


def crawl_site(start_url: str, max_pages: int = 50) -> list[dict]:
    """
    Crawl all pages on the same domain as start_url.
    Returns a list of {url, chunks} dicts for successfully ingested pages.
    """
    parsed_start = urlparse(start_url)
    domain = parsed_start.netloc
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ChatbotBot/1.0)"}

    visited: set[str] = set()
    queue: list[str] = [start_url]
    results: list[dict] = []

    while queue and len(visited) < max_pages:
        url = queue.pop(0)

        # Normalise: strip fragment and trailing slash
        p = urlparse(url)
        clean_url = p._replace(fragment="", query="").geturl().rstrip("/") or url
        if clean_url in visited:
            continue
        visited.add(clean_url)

        try:
            resp = requests.get(clean_url, headers=headers, timeout=15)
            if "text/html" not in resp.headers.get("content-type", ""):
                continue
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Collect same-domain links before stripping tags
            for a in soup.find_all("a", href=True):
                href = urljoin(clean_url, a["href"])
                ph = urlparse(href)
                if ph.netloc == domain and ph.scheme in ("http", "https"):
                    normalised = ph._replace(fragment="", query="").geturl().rstrip("/")
                    if normalised not in visited:
                        queue.append(normalised)

            chunks = _extract_text(soup)
            results.append({"url": clean_url, "chunks": chunks, "error": None})

        except Exception as e:
            results.append({"url": clean_url, "chunks": [], "error": str(e)})

    return results
