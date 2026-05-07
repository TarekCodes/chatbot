import io
import re
import requests
from bs4 import BeautifulSoup


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


def ingest_url(url: str) -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ChatbotBot/1.0)"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return split_text(text)
