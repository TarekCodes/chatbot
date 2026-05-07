import os
import uuid
import anthropic
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
        self.anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.system_prompt = os.environ.get(
            "SYSTEM_PROMPT",
            "You are a helpful assistant. Answer questions based on the provided context. "
            "If the answer is not found in the context, say so honestly and offer to help with what you do know.",
        )

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

    def chat(self, message: str, history: list[dict]) -> str:
        context_chunks = self.retrieve(message)

        messages = []
        for turn in history[-10:]:
            messages.append({"role": turn["role"], "content": turn["content"]})

        if context_chunks:
            context = "\n\n---\n\n".join(context_chunks)
            user_content = (
                f"Relevant context:\n\n{context}\n\n---\n\nUser question: {message}"
            )
        else:
            user_content = message

        messages.append({"role": "user", "content": user_content})

        response = self.anthropic.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=self.system_prompt,
            messages=messages,
        )
        return response.content[0].text

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
