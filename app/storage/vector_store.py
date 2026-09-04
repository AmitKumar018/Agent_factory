from __future__ import annotations

import hashlib
import math
import os
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.logging_utils import get_logger

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = get_logger(__name__)

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chromadb")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
DOCUMENTS_COLLECTION = "documents"
PATTERNS_COLLECTION = "patterns"

_chroma_client: Any | None = None
_embed_model: Optional["SentenceTransformer"] = None
_memory_collections: dict[str, "MemoryCollection"] = {}


def _missing_dependency(name: str, install_hint: str) -> RuntimeError:
    return RuntimeError(
        f"{name} is required for vector-store operations. "
        f"Install it with: {install_hint}"
    )


def get_chroma_client() -> Any:
    """Return the shared Chroma PersistentClient, loading chromadb lazily."""
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client

    try:
        import chromadb
        from chromadb.config import Settings
    except ModuleNotFoundError as exc:
        raise _missing_dependency("chromadb", "pip install chromadb") from exc

    os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
    _chroma_client = chromadb.PersistentClient(
        path=CHROMA_PERSIST_DIR,
        settings=Settings(anonymized_telemetry=False),
    )
    logger.info("chroma_client_initialized", path=CHROMA_PERSIST_DIR)
    return _chroma_client


def _get_embed_model() -> "SentenceTransformer" | None:
    """Lazy-load sentence-transformers only when explicitly enabled."""
    global _embed_model
    if _embed_model is not None:
        return _embed_model

    enabled = os.getenv("ENABLE_SENTENCE_TRANSFORMERS", "0").lower() in {"1", "true", "yes"}
    if not enabled:
        return None

    try:
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError:
        logger.warning("embedding_model_unavailable", model=EMBED_MODEL)
        return None

    try:
        logger.info("loading_embedding_model", model=EMBED_MODEL)
        _embed_model = SentenceTransformer(EMBED_MODEL)
        logger.info("embedding_model_loaded", model=EMBED_MODEL)
        return _embed_model
    except Exception as exc:
        logger.warning("embedding_model_load_failed", model=EMBED_MODEL, error=str(exc))
        return None


def _hash_embedding(text: str, dimensions: int = 384) -> list[float]:
    """Deterministic tiny embedding fallback for local/dev tests."""
    values = [0.0] * dimensions
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = digest[0] % dimensions
        sign = 1.0 if digest[1] % 2 == 0 else -1.0
        values[index] += sign
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    model = _get_embed_model()
    if model is None:
        return [_hash_embedding(text) for text in texts]
    embeddings = model.encode(texts, convert_to_numpy=True)
    return embeddings.tolist()


def _matches_where(metadata: dict[str, Any], where: Optional[Dict[str, Any]]) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_matches_where(metadata, item) for item in where["$and"])
    if "$or" in where:
        return any(_matches_where(metadata, item) for item in where["$or"])

    for key, expected in where.items():
        actual = metadata.get(key)
        if isinstance(expected, dict):
            if "$eq" in expected and actual != expected["$eq"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$contains" in expected and str(expected["$contains"]) not in str(actual or ""):
                return False
        elif actual != expected:
            return False
    return True


class MemoryCollection:
    """Small Chroma-shaped collection used when ChromaDB is unavailable."""

    def __init__(self, name: str):
        self.name = name
        self._rows: dict[str, dict[str, Any]] = {}

    def upsert(self, ids: list[str], documents: list[str], embeddings=None, metadatas=None) -> None:
        embeddings = embeddings or [None] * len(ids)
        metadatas = metadatas or [{} for _ in ids]
        for index, row_id in enumerate(ids):
            self._rows[row_id] = {
                "id": row_id,
                "document": documents[index],
                "embedding": embeddings[index] if index < len(embeddings) else None,
                "metadata": metadatas[index] if index < len(metadatas) else {},
            }

    def query(
        self,
        query_texts=None,
        query_embeddings=None,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
        include: Optional[list[str]] = None,
    ) -> dict[str, list[list[Any]]]:
        query_text = (query_texts or [""])[0] if query_texts is not None else ""
        query_tokens = set(str(query_text).lower().split())
        query_embedding = (query_embeddings or [None])[0] if query_embeddings is not None else None

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in self._rows.values():
            if not _matches_where(row["metadata"], where):
                continue
            score = 0.0
            if query_tokens:
                doc_tokens = set(row["document"].lower().split())
                score = len(query_tokens & doc_tokens) / max(len(query_tokens), 1)
            elif query_embedding is not None and row.get("embedding") is not None:
                score = sum(float(a) * float(b) for a, b in zip(query_embedding, row["embedding"]))
            scored.append((score, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        rows = [row for _score, row in scored[:n_results]]
        distances = [max(0.0, 1.0 - score) for score, _row in scored[:n_results]]
        return {
            "ids": [[row["id"] for row in rows]],
            "documents": [[row["document"] for row in rows]],
            "metadatas": [[row["metadata"] for row in rows]],
            "distances": [distances],
        }

    def delete(self, ids: Optional[list[str]] = None, where: Optional[Dict[str, Any]] = None) -> None:
        if ids is not None:
            for row_id in ids:
                self._rows.pop(row_id, None)
            return
        for row_id, row in list(self._rows.items()):
            if _matches_where(row["metadata"], where):
                self._rows.pop(row_id, None)

    def count(self) -> int:
        return len(self._rows)


def _get_memory_collection(name: str) -> MemoryCollection:
    if name not in _memory_collections:
        _memory_collections[name] = MemoryCollection(name)
        logger.warning("memory_vector_collection_initialized", collection=name)
    return _memory_collections[name]


def _get_collection(name: str):
    try:
        return get_chroma_client().get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
    except RuntimeError as exc:
        logger.warning("chroma_unavailable_using_memory", collection=name, error=str(exc))
        return _get_memory_collection(name)


def get_documents_collection():
    return _get_collection(DOCUMENTS_COLLECTION)


def get_patterns_collection():
    return _get_collection(PATTERNS_COLLECTION)


class DocumentStore:
    """Document vector store with Chroma primary and memory fallback."""

    def __init__(self):
        self._collection = get_documents_collection()
        logger.info(
            "document_store_initialized",
            collection=DOCUMENTS_COLLECTION,
            persist_dir=CHROMA_PERSIST_DIR,
            embed_model=EMBED_MODEL,
        )

    def upsert_chunks(self, document_id: str, chunks: list, project_id: str | None = None) -> int:
        if not chunks:
            logger.warning("upsert_chunks_empty", document_id=document_id)
            return 0

        texts = [chunk["text"] for chunk in chunks]
        ids = [
            str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}::{chunk['section_id']}::{index}"))
            for index, chunk in enumerate(chunks)
        ]
        metadatas = [
            {
                "document_id": document_id,
                "project_id": project_id or "",
                "section_id": chunk.get("section_id", ""),
                "section_title": chunk.get("section_title", ""),
                "page": str(chunk.get("page", 0)),
                "kind": chunk.get("kind", "other"),
            }
            for chunk in chunks
        ]

        logger.info("embedding_chunks", document_id=document_id, chunk_count=len(texts))
        self._collection.upsert(
            ids=ids,
            documents=texts,
            embeddings=_embed_texts(texts),
            metadatas=metadatas,
        )
        logger.info("upsert_chunks_complete", document_id=document_id, chunk_count=len(chunks))
        return len(chunks)

    def query(
        self,
        query_text: str,
        project_id: str,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        filters: Dict[str, Any] = {"project_id": project_id}
        if where:
            filters = {"$and": [{"project_id": project_id}, where]}

        results = self._collection.query(
            query_embeddings=[_embed_texts([query_text])[0]],
            query_texts=[query_text] if isinstance(self._collection, MemoryCollection) else None,
            n_results=n_results,
            where=filters,
            include=["documents", "metadatas", "distances"],
        )

        documents = results.get("documents") or [[]]
        metadatas = results.get("metadatas") or [[]]
        distances = results.get("distances") or [[]]

        output: list[dict[str, Any]] = []
        for index, document in enumerate(documents[0]):
            distance = distances[0][index] if index < len(distances[0]) else 1
            output.append(
                {
                    "text": document,
                    "metadata": metadatas[0][index] if index < len(metadatas[0]) else {},
                    "score": 1 - distance,
                }
            )
        return output

    def delete_document_chunks(self, document_id: str) -> bool:
        self._collection.delete(where={"document_id": document_id})
        logger.info("chunks_deleted", document_id=document_id)
        return True

    def count(self) -> int:
        return self._collection.count()

    def health_check(self) -> bool:
        try:
            self._collection.count()
            return True
        except Exception as exc:
            logger.error("chroma_health_check_failed", error=str(exc))
            return False


_document_store_instance: DocumentStore | None = None


def get_document_store() -> DocumentStore:
    global _document_store_instance
    if _document_store_instance is None:
        _document_store_instance = DocumentStore()
    return _document_store_instance


class _DocumentStoreProxy:
    def __getattr__(self, name: str):
        return getattr(get_document_store(), name)


document_store = _DocumentStoreProxy()
vector_store = document_store