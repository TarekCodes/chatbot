import os
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
        results = self.collection.query(
            query_texts=[query], n_results=min(n, count)
        )
        return results["documents"][0] if results["documents"] else []

    def chat(self, message: str, history: list) -> tuple[str, int, int]:
        """Returns (reply, input_tokens, output_tokens)."""
        context_chunks = self.retrieve(message)

        if context_chunks:
            context = "\n\n---\n\n".join(context_chunks)
            user_content = f"Relevant context:\n\n{context}\n\n---\n\nUser question: {message}"
        else:
            user_content = message

        if self.provider == "openai":
            return self._chat_openai(user_content, history)
        return self._chat_anthropic(user_content, history)

    def _chat_anthropic(self, user_content: str, history: list) -> tuple[str, int, int]:
        messages = [{"role": t["role"], "content": t["content"]} for t in history[-10:]]
        messages.append({"role": "user", "content": user_content})
        response = self._anthropic.messages.create(
            model=self._model,
            max_tokens=1024,
            system=self.system_prompt,
            messages=messages,
        )
        return (response.content[0].text,
                response.usage.input_tokens,
                response.usage.output_tokens)

    def _chat_openai(self, user_content: str, history: list) -> tuple[str, int, int]:
        messages = [{"role": "system", "content": self.system_prompt}]
        for t in history[-10:]:
            messages.append({"role": t["role"], "content": t["content"]})
        messages.append({"role": "user", "content": user_content})
        response = self._openai.chat.completions.create(
            model=self._model,
            max_completion_tokens=1024,
            messages=messages,
        )
        return (response.choices[0].message.content,
                response.usage.prompt_tokens,
                response.usage.completion_tokens)

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
