import asyncio
import json
import os
import threading
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import metrics
from ingest import fetch_page, ingest_docx, ingest_pdf, ingest_url
from rag import RAGEngine

app = FastAPI(title="Chatbot API")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_MESSAGE_LENGTH = int(os.environ.get("MAX_MESSAGE_LENGTH", 500))
RATE_LIMIT         = os.environ.get("RATE_LIMIT", "20/minute")

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
    session_id: str | None = None
    page_url: str | None = None


@app.post("/api/chat")
@limiter.limit(RATE_LIMIT)
async def chat(request: Request, body: ChatRequest):
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")
    if len(msg) > MAX_MESSAGE_LENGTH:
        raise HTTPException(400, f"Message exceeds {MAX_MESSAGE_LENGTH} character limit")
    try:
        reply, input_tokens, output_tokens = rag.chat(msg, body.history)
    except Exception as e:
        print(f"[chat] error: {e}")
        raise HTTPException(500, str(e))
    metrics.log(input_tokens, output_tokens, rag.provider)
    if body.session_id:
        metrics.upsert_conversation(body.session_id, body.page_url)
        metrics.log_turn(body.session_id, msg, reply, input_tokens, output_tokens)
    return {"reply": reply}


@app.post("/api/chat/stream")
@limiter.limit(RATE_LIMIT)
async def chat_stream(request: Request, body: ChatRequest):
    msg = body.message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")
    if len(msg) > MAX_MESSAGE_LENGTH:
        raise HTTPException(400, f"Message exceeds {MAX_MESSAGE_LENGTH} character limit")

    async def generate():
        reply_parts: list[str] = []
        try:
            # Run blocking generator in a thread, feed tokens via a queue
            q: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def run():
                try:
                    for item in rag.chat_stream(msg, body.history):
                        loop.call_soon_threadsafe(q.put_nowait, item)
                except Exception as exc:
                    loop.call_soon_threadsafe(q.put_nowait, {"error": str(exc)})
                finally:
                    loop.call_soon_threadsafe(q.put_nowait, None)  # sentinel

            threading.Thread(target=run, daemon=True).start()

            input_tokens = output_tokens = 0
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, dict):
                    if "error" in item:
                        yield f"data: {json.dumps({'error': item['error']})}\n\n"
                        return
                    input_tokens = item.get("input_tokens", 0)
                    output_tokens = item.get("output_tokens", 0)
                else:
                    reply_parts.append(item)
                    yield f"data: {json.dumps({'token': item})}\n\n"

            reply = "".join(reply_parts)
            metrics.log(input_tokens, output_tokens, rag.provider)
            if body.session_id:
                metrics.upsert_conversation(body.session_id, body.page_url)
                metrics.log_turn(body.session_id, msg, reply, input_tokens, output_tokens)
            yield f"data: {json.dumps({'done': True})}\n\n"

        except Exception as e:
            print(f"[chat/stream] error: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

        crawled: set[str] = set()        # URLs we have fully processed
        queued: set[str] = {clean_start} # URLs already in the queue
        queue: list[str] = [clean_start]
        total_chunks = 0
        pages_done = 0

        while queue and pages_done < max_pages:
            current = queue.pop(0)

            if current in crawled:       # safety guard — skip if already done
                continue
            crawled.add(current)

            try:
                result = await asyncio.to_thread(fetch_page, current, domain)

                for link in result["links"]:
                    if link not in crawled and link not in queued:
                        queued.add(link)
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


@app.get("/api/metrics")
async def get_metrics(days: int = 30, key: str | None = None):
    check_admin(key)
    return {"daily": metrics.daily_stats(days), "totals": metrics.totals()}


@app.get("/api/conversations")
async def get_conversations(key: str | None = None):
    check_admin(key)
    return {"conversations": metrics.get_conversations()}


@app.get("/api/conversations/{session_id}")
async def get_conversation(session_id: str, key: str | None = None):
    check_admin(key)
    return {"turns": metrics.get_turns(session_id)}


@app.delete("/api/sources")
async def delete_source(source: str, key: str | None = None):
    check_admin(key)
    rag.delete_source(source)
    return {"status": "ok"}


@app.delete("/api/conversations/oldest")
async def delete_oldest_conversations(count: int = 1000, key: str | None = None):
    check_admin(key)
    deleted = metrics.delete_oldest_conversations(count)
    return {"status": "ok", "deleted": deleted}
