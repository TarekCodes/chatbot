import os
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ingest import crawl_site, ingest_docx, ingest_pdf, ingest_url
from rag import RAGEngine

app = FastAPI(title="Chatbot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

rag = RAGEngine()

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    print(f"[422] path={request.url.path} body={body.decode()} errors={exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/")
async def admin():
    return FileResponse("static/admin.html")


# ── Chat ──────────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    history: list = []


@app.post("/api/chat")
async def chat(body: ChatRequest):
    if not body.message.strip():
        raise HTTPException(400, "Message cannot be empty")
    reply = rag.chat(body.message, body.history)
    return {"reply": reply}


# ── Ingest ────────────────────────────────────────────────────────────────────


class URLRequest(BaseModel):
    url: str


ADMIN_KEY = os.environ.get("ADMIN_KEY", "")


def check_admin(key: str | None):
    if ADMIN_KEY and key != ADMIN_KEY:
        raise HTTPException(401, "Invalid admin key")


@app.post("/api/ingest/file")
async def ingest_file(file: UploadFile = File(...), key: str | None = None):
    check_admin(key)
    name = (file.filename or "").lower()
    content = await file.read()

    if name.endswith(".pdf"):
        chunks = ingest_pdf(content)
    elif name.endswith(".docx"):
        chunks = ingest_docx(content)
    else:
        raise HTTPException(400, "Only PDF and DOCX files are supported")

    added = rag.add_documents(chunks, source=file.filename or name)
    return {"status": "ok", "source": file.filename, "chunks": added}


@app.post("/api/ingest/url")
async def ingest_url_endpoint(body: URLRequest, key: str | None = None):
    check_admin(key)
    chunks = ingest_url(body.url)
    added = rag.add_documents(chunks, source=body.url)
    return {"status": "ok", "source": body.url, "chunks": added}


class CrawlRequest(BaseModel):
    url: str
    max_pages: int = 50


@app.post("/api/ingest/crawl")
async def crawl_endpoint(body: CrawlRequest, key: str | None = None):
    check_admin(key)
    pages = crawl_site(body.url, max_pages=body.max_pages)
    total_chunks = 0
    ingested = []
    for page in pages:
        if page["chunks"] and not page["error"]:
            added = rag.add_documents(page["chunks"], source=page["url"])
            total_chunks += added
            ingested.append({"url": page["url"], "chunks": added})
        elif page["error"]:
            ingested.append({"url": page["url"], "chunks": 0, "error": page["error"]})
    return {"status": "ok", "pages": len(ingested), "total_chunks": total_chunks, "detail": ingested}


# ── Sources ───────────────────────────────────────────────────────────────────


@app.get("/api/sources")
async def list_sources(key: str | None = None):
    check_admin(key)
    return {"sources": rag.list_sources()}


@app.get("/api/chunks")
async def get_chunks(source: str, key: str | None = None):
    check_admin(key)
    return {"chunks": rag.get_chunks(source)}


@app.delete("/api/sources")
async def delete_source(source: str, key: str | None = None):
    check_admin(key)
    rag.delete_source(source)
    return {"status": "ok"}
