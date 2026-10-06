from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.logging_utils import get_logger

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = get_logger(__name__)

EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
DOCUMENTS_COLLECTION = "documents"
PATTERNS_COLLECTION = "patterns"

_embed_model: Optional["SentenceTransformer"] = None
_sqlite_connection: sqlite3.Connection | None = None
_sqlite_lock = threading.RLock()


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


def _get_sqlite_connection() -> sqlite3.Connection:
    """Return the process-local SQLite database used for vector storage."""
    global _sqlite_connection
    if _sqlite_connection is None:
        _sqlite_connection = sqlite3.connect(":memory:", check_same_thread=False)
        _sqlite_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS vector_rows (
                collection TEXT NOT NULL,
                row_id TEXT NOT NULL,
                document TEXT NOT NULL,
                embedding TEXT,
                metadata TEXT NOT NULL,
                PRIMARY KEY (collection, row_id)
            )
            """
        )
        _sqlite_connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_vector_rows_collection "
            "ON vector_rows(collection)"
        )
        _sqlite_connection.commit()
        logger.info("sqlite_memory_vector_store_initialized")
    return _sqlite_connection


class SQLiteMemoryCollection:
    """Chroma-compatible collection API backed by an in-memory SQLite table."""

    def __init__(self, name: str):
        self.name = name

    def upsert(self, ids: list[str], documents: list[str], embeddings=None, metadatas=None) -> None:
        embeddings = embeddings or [None] * len(ids)
        metadatas = metadatas or [{} for _ in ids]
        with _sqlite_lock:
            connection = _get_sqlite_connection()
            connection.executemany(
                """
                INSERT INTO vector_rows(collection, row_id, document, embedding, metadata)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(collection, row_id) DO UPDATE SET
                    document = excluded.document,
                    embedding = excluded.embedding,
                    metadata = excluded.metadata
                """,
                [
                    (
                        self.name,
                        row_id,
                        documents[index],
                        json.dumps(embeddings[index]) if index < len(embeddings) and embeddings[index] is not None else None,
                        json.dumps(metadatas[index] if index < len(metadatas) else {}),
                    )
                    for index, row_id in enumerate(ids)
                ],
            )
            connection.commit()

    def query(
        self,
        query_texts=None,
        query_embeddings=None,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
        include: Optional[list[str]] = None,
    ) -> dict[str, list[list[Any]]]:
        query_text = (query_texts or [""])[0] if query_texts is not None else ""
        query_embedding = (query_embeddings or [None])[0] if query_embeddings is not None else None
        if query_embedding is None and query_text:
            query_embedding = _embed_texts([str(query_text)])[0]

        scored: list[tuple[float, dict[str, Any]]] = []
        with _sqlite_lock:
            rows = _get_sqlite_connection().execute(
                "SELECT row_id, document, embedding, metadata FROM vector_rows WHERE collection = ?",
                (self.name,),
            ).fetchall()

        for row_id, document, embedding_json, metadata_json in rows:
            metadata = json.loads(metadata_json) if metadata_json else {}
            if not _matches_where(metadata, where):
                continue
            embedding = json.loads(embedding_json) if embedding_json else None
            score = 0.0
            if query_embedding is not None and embedding is not None:
                score = sum(float(a) * float(b) for a, b in zip(query_embedding, embedding))
            scored.append((score, {
                "id": row_id,
                "document": document,
                "embedding": embedding,
                "metadata": metadata,
            }))

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
        with _sqlite_lock:
            connection = _get_sqlite_connection()
            if ids is not None:
                connection.executemany(
                    "DELETE FROM vector_rows WHERE collection = ? AND row_id = ?",
                    [(self.name, row_id) for row_id in ids],
                )
            else:
                rows = connection.execute(
                    "SELECT row_id, metadata FROM vector_rows WHERE collection = ?",
                    (self.name,),
                ).fetchall()
                connection.executemany(
                    "DELETE FROM vector_rows WHERE collection = ? AND row_id = ?",
                    [
                        (self.name, row_id)
                        for row_id, metadata_json in rows
                        if _matches_where(json.loads(metadata_json), where)
                    ],
                )
            connection.commit()

    def count(self) -> int:
        with _sqlite_lock:
            return _get_sqlite_connection().execute(
                "SELECT COUNT(*) FROM vector_rows WHERE collection = ?", (self.name,)
            ).fetchone()[0]


_sqlite_collections: dict[str, SQLiteMemoryCollection] = {}


def _get_collection(name: str) -> SQLiteMemoryCollection:
    if name not in _sqlite_collections:
        _sqlite_collections[name] = SQLiteMemoryCollection(name)
        logger.info("sqlite_memory_collection_initialized", collection=name)
    return _sqlite_collections[name]


def get_documents_collection():
    return _get_collection(DOCUMENTS_COLLECTION)


def get_patterns_collection():
    return _get_collection(PATTERNS_COLLECTION)


class DocumentStore:
    """Document vector store backed by process-local in-memory SQLite."""

    def __init__(self):
        self._collection = get_documents_collection()
        logger.info(
            "document_store_initialized",
            collection=DOCUMENTS_COLLECTION,
            database=":memory:",
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
            query_texts=[query_text],
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
            logger.error("sqlite_memory_health_check_failed", error=str(exc))
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
