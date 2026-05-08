import os
import queue
import threading
import uuid

import chromadb
from chromadb.utils import embedding_functions


class RAGEngine:
    def __init__(self):
        self.db = chromadb.PersistentClient(path="./chroma_db")
        ef = embedding_functions.DefaultEmbeddingFunction()
        self.collection = self.db.get_or_create_collection(
            name="documents",
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        self.system_prompt = os.environ.get(
            "SYSTEM_PROMPT",
            "You are a helpful assistant. Answer questions based on the provided context. "
            "If the answer is not found in the context, say so honestly and offer to help with what you do know.",
        )
        self.provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
        self._ranker = None
        self._init_client()

    def _init_client(self):
        if self.provider == "openai":
            from openai import OpenAI
            self._openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            self._model = os.environ.get("OPENAI_MODEL", "gpt-4o")
        else:
            import anthropic
            self._anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            self._model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

        try:
            from flashrank import Ranker
            self._ranker = Ranker()
        except Exception as e:
            print(f"[rerank] flashrank unavailable, skipping: {e}")

    def add_documents(self, chunks: list[str], source: str) -> int:
        if not chunks:
            return 0
        ids = [str(uuid.uuid4()) for _ in chunks]
        metadatas = [{"source": source} for _ in chunks]
        self.collection.add(documents=chunks, ids=ids, metadatas=metadatas)
        return len(chunks)

    def retrieve(self, query: str, n: int = 5) -> list[str]:
        count = self.collection.count()
        if count == 0:
            return []

        # Fetch more candidates when reranker is available
        candidates = 10 if self._ranker else n
        results = self.collection.query(
            query_texts=[query], n_results=min(candidates, count)
        )
        docs = results["documents"][0] if results["documents"] else []

        if not docs or not self._ranker:
            return docs[:n]

        try:
            from flashrank import RerankRequest
            rerank_req = RerankRequest(query=query, passages=[{"text": d} for d in docs])
            ranked = self._ranker.rerank(rerank_req)
            ranked.sort(key=lambda x: x["score"], reverse=True)
            return [r["text"] for r in ranked[:n]]
        except Exception as e:
            print(f"[rerank] error: {e}")
            return docs[:n]

    def chat(self, message: str, history: list) -> tuple[str, int, int]:
        """Returns (reply, input_tokens, output_tokens)."""
        context_chunks = self.retrieve(message)
        user_content = self._build_user_content(message, context_chunks)

        if self.provider == "openai":
            return self._chat_openai(user_content, history)
        return self._chat_anthropic(user_content, history)

    def chat_stream(self, message: str, history: list):
        """Yields text tokens, then a final sentinel dict with token counts."""
        context_chunks = self.retrieve(message)
        user_content = self._build_user_content(message, context_chunks)

        if self.provider == "openai":
            yield from self._stream_openai(user_content, history)
        else:
            yield from self._stream_anthropic(user_content, history)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _build_user_content(self, message: str, context_chunks: list[str]) -> str:
        if context_chunks:
            context = "\n\n---\n\n".join(context_chunks)
            return f"Relevant context:\n\n{context}\n\n---\n\nUser question: {message}"
        return message

    def _build_anthropic_messages(self, user_content: str, history: list) -> list:
        messages = [{"role": t["role"], "content": t["content"]} for t in history[-10:]]
        messages.append({"role": "user", "content": user_content})
        return messages

    def _build_openai_messages(self, user_content: str, history: list) -> list:
        messages = [{"role": "system", "content": self.system_prompt}]
        for t in history[-10:]:
            messages.append({"role": t["role"], "content": t["content"]})
        messages.append({"role": "user", "content": user_content})
        return messages

    # ── non-streaming ─────────────────────────────────────────────────────────

    def _chat_anthropic(self, user_content: str, history: list) -> tuple[str, int, int]:
        response = self._anthropic.messages.create(
            model=self._model,
            max_tokens=1024,
            system=self.system_prompt,
            messages=self._build_anthropic_messages(user_content, history),
        )
        return (response.content[0].text,
                response.usage.input_tokens,
                response.usage.output_tokens)

    def _chat_openai(self, user_content: str, history: list) -> tuple[str, int, int]:
        response = self._openai.chat.completions.create(
            model=self._model,
            max_completion_tokens=2048,
            messages=self._build_openai_messages(user_content, history),
        )
        choice = response.choices[0]
        content = choice.message.content
        if content is None:
            reason = choice.finish_reason
            print(f"[openai] null content, finish_reason={reason}")
            raise ValueError(f"OpenAI returned no content (finish_reason={reason})")
        return (content,
                response.usage.prompt_tokens,
                response.usage.completion_tokens)

    # ── streaming ─────────────────────────────────────────────────────────────

    def _stream_anthropic(self, user_content: str, history: list):
        input_tokens = output_tokens = 0
        with self._anthropic.messages.stream(
            model=self._model,
            max_tokens=1024,
            system=self.system_prompt,
            messages=self._build_anthropic_messages(user_content, history),
        ) as stream:
            for text in stream.text_stream:
                yield text
            usage = stream.get_final_message().usage
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
        yield {"input_tokens": input_tokens, "output_tokens": output_tokens}

    def _stream_openai(self, user_content: str, history: list):
        input_tokens = output_tokens = 0
        stream = self._openai.chat.completions.create(
            model=self._model,
            max_completion_tokens=2048,
            messages=self._build_openai_messages(user_content, history),
            stream=True,
            stream_options={"include_usage": True},
        )
        for chunk in stream:
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens
                output_tokens = chunk.usage.completion_tokens
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
        yield {"input_tokens": input_tokens, "output_tokens": output_tokens}

    # ── source management ─────────────────────────────────────────────────────

    def list_sources(self) -> list[dict]:
        result = self.collection.get(include=["metadatas"])
        tally: dict[str, int] = {}
        for meta in result["metadatas"]:
            src = meta.get("source", "unknown")
            tally[src] = tally.get(src, 0) + 1
        return [{"source": s, "chunks": c} for s, c in tally.items()]

    def get_chunks(self, source: str) -> list[dict]:
        result = self.collection.get(
            where={"source": source},
            include=["documents"],
        )
        return [
            {"id": doc_id, "text": doc}
            for doc_id, doc in zip(result["ids"], result["documents"])
        ]

    def delete_source(self, source: str) -> None:
        self.collection.delete(where={"source": source})
