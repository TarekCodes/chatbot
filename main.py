import asyncio
import json
import os
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ingest import fetch_page, ingest_docx, ingest_pdf, ingest_url
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


@app.get("/api/ingest/crawl/stream")
async def crawl_stream(url: str, max_pages: int = 50, key: str | None = None):
    check_admin(key)

    async def generate():
        parsed = urlparse(url)
        domain = parsed.netloc
        clean_start = parsed._replace(fragment="", query="").geturl().rstrip("/") or url

        seen: set[str] = {clean_start}   # visited + queued, for O(1) dedup
        queue: list[str] = [clean_start]
        total_chunks = 0
        pages_done = 0

        while queue and pages_done < max_pages:
            current = queue.pop(0)
            try:
                result = await asyncio.to_thread(fetch_page, current, domain)

                for link in result["links"]:
                    if link not in seen:
                        seen.add(link)
                        queue.append(link)

                if result["chunks"]:
                    added = rag.add_documents(result["chunks"], source=current)
                    total_chunks += added
                    pages_done += 1
                    data = {"type": "page", "url": current, "chunks": added,
                            "done": pages_done, "queued": len(queue)}
                else:
                    data = {"type": "skip", "url": current,
                            "done": pages_done, "queued": len(queue)}

            except Exception as e:
                data = {"type": "error", "url": current, "error": str(e),
                        "done": pages_done, "queued": len(queue)}

            yield f"data: {json.dumps(data)}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'pages': pages_done, 'total_chunks': total_chunks})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
