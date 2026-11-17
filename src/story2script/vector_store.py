"""Persistent exact vector search using sqlite-vec, with immutable corpus manifests.

The DB stores source text, metadata and embeddings, not an expiring response cache.
Vectors are normalized/validated and reused by model fingerprint + exact text hash.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .job_lock import JobFileLock
from .job_store import resolve_db_path

_SCHEMA_LOCK = threading.Lock()
SCHEMA_VERSION = "story-vector-v1"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_models (
    model_key TEXT PRIMARY KEY, dimension INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rag_vectors (
    rowid INTEGER PRIMARY KEY, model_key TEXT NOT NULL, text_hash TEXT NOT NULL,
    dimension INTEGER NOT NULL, UNIQUE(model_key,text_hash));
CREATE TABLE IF NOT EXISTS rag_indexes (
    index_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, source_hash TEXT NOT NULL,
    model_key TEXT NOT NULL, model_name TEXT NOT NULL, model_revision TEXT NOT NULL,
    dimension INTEGER NOT NULL, doc_count INTEGER NOT NULL, chunk_chars INTEGER NOT NULL,
    manifest TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rag_documents (
    index_id TEXT NOT NULL REFERENCES rag_indexes(index_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, doc_id TEXT NOT NULL, kind TEXT NOT NULL,
    chapter_index INTEGER NOT NULL, chapter_title TEXT NOT NULL,
    text TEXT NOT NULL, metadata TEXT NOT NULL,
    vector_id INTEGER NOT NULL REFERENCES rag_vectors(rowid),
    PRIMARY KEY(index_id,ordinal), UNIQUE(index_id,doc_id));
CREATE INDEX IF NOT EXISTS rag_documents_scope ON rag_documents(index_id,chapter_index,kind);
CREATE INDEX IF NOT EXISTS rag_document_vectors ON rag_documents(vector_id);
"""


class VectorStoreError(ValueError):
    pass


def storage_mode():
    value = os.getenv("RAG_STORAGE", "sqlite").strip().lower()
    if value not in ("sqlite", "memory"):
        raise ValueError("RAG_STORAGE 只能为 sqlite 或 memory。")
    return value


def namespace():
    value = os.getenv("RAG_NAMESPACE", "story2script").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("RAG_NAMESPACE 只允许字母、数字、下划线和连字符。")
    return value


def database_path():
    configured = os.getenv("STORY2SCRIPT_RAG_DB", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else resolve_db_path().resolve().parent / "story_vectors.db"
    )


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalized(vector):
    if not isinstance(vector, (list, tuple)) or not 1 <= len(vector) <= 8192:
        raise VectorStoreError("Embedding 向量为空或维度超出 1–8192。")
    if any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        for v in vector
    ):
        raise VectorStoreError("Embedding 向量含无效数值。")
    norm = math.hypot(*vector)
    if not math.isfinite(norm):
        raise VectorStoreError("Embedding 向量范数无效。")
    # Zero vectors carry no semantic signal and are scored zero, not NaN.
    return [v / norm for v in vector] if norm else [0.0] * len(vector)


def _connect_once(path):
    conn = None
    try:
        import sqlite_vec

        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(
                conn
            )  # Bundled installed extension only, no user-provided library path.
        finally:
            conn.enable_load_extension(False)
        with _SCHEMA_LOCK:
            if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
            required = {"rag_models", "rag_vectors", "rag_indexes", "rag_documents"}
            existing = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not required <= existing:
                conn.executescript(_SCHEMA)
        return conn
    except (sqlite3.Error, OSError, ImportError, AttributeError):
        if conn:
            conn.close()
        raise


def _connect(path):
    for attempt in range(4):
        try:
            return _connect_once(path)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() and attempt < 3:
                time.sleep(0.05 * (attempt + 1))
                continue
        except (sqlite3.Error, OSError, ImportError, AttributeError):
            pass
        raise VectorStoreError(
            "持久化向量库不可用，请检查数据库权限与 sqlite-vec 安装；未假报保存成功。"
        ) from None


def _pack(vector):
    import sqlite_vec

    return sqlite_vec.serialize_float32(vector)


@contextmanager
def model_lock(path, model_key):
    timeout = float(os.getenv("RAG_BUILD_LOCK_SECONDS", "180"))
    if not 1 <= timeout <= 7200:
        raise ValueError("RAG_BUILD_LOCK_SECONDS 必须在 1–7200 之间。")
    lock = None
    try:
        lock = JobFileLock(path, "vector-model:" + model_key)
        start = time.monotonic()
        while not lock.acquire():
            if time.monotonic() - start > timeout:
                raise VectorStoreError("向量索引正在由其他任务构建，等待超时，请稍后重试。")
            time.sleep(0.1)
        yield
    except OSError:
        raise VectorStoreError("无法锁定向量库构建目录，请检查权限。") from None
    finally:
        if lock:
            lock.close()


class PersistentVectorIndex:
    kind = "embedding"

    def __init__(self, client, docs, manifest, *, path=None):
        self.client, self.docs = client, docs
        self.path = Path(path).resolve() if path else database_path()
        self.namespace = namespace()
        self.model_revision = os.getenv("RAG_EMBEDDING_REVISION", "1")
        self.model_key = digest(
            {
                "namespace": self.namespace,
                "base_url": client.base_url,
                "model": client.embed_model,
                "revision": self.model_revision,
                "account": digest(client.api_key),
                "normalization": "unit-float32-v1",
            }
        )
        self.source_hash = manifest["source_hash"]
        self.chunk_chars = manifest["chunk_chars"]
        self.manifest = json.dumps(
            {
                "schema": SCHEMA_VERSION,
                "documents": [d.__dict__ for d in docs],
                **manifest,
                "model_key": self.model_key,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self.index_id = digest(self.manifest)
        self.dimension = 0
        self.reused = False
        self.embedded_documents = 0

    @contextmanager
    def _building(self):
        with model_lock(self.path, self.model_key):
            yield

    def index(self, texts):
        if not texts or len(texts) != len(self.docs):
            raise VectorStoreError("向量索引必须包含至少一个原文文档。")
        if len({d.doc_id for d in self.docs}) != len(self.docs):
            raise VectorStoreError("向量索引文档 ID 重复。")
        with self._building():
            conn = _connect(self.path)
            try:
                stored = conn.execute(
                    "SELECT * FROM rag_indexes WHERE index_id=?", (self.index_id,)
                ).fetchone()
                if stored:
                    self.dimension = stored["dimension"]
                    if not isinstance(self.dimension, int) or not 1 <= self.dimension <= 8192:
                        raise VectorStoreError("保存的向量维度无效，请删除损坏索引并重建。")
                    records = conn.execute(
                        "SELECT doc_id,kind,chapter_index,chapter_title,text,metadata,vector_id FROM rag_documents WHERE index_id=? ORDER BY ordinal",
                        (self.index_id,),
                    ).fetchall()
                    expected = [
                        (
                            d.doc_id,
                            d.kind,
                            d.chapter_index,
                            d.chapter_title,
                            d.text,
                            json.dumps(d.metadata, ensure_ascii=False, sort_keys=True),
                        )
                        for d in self.docs
                    ]
                    actual = [tuple(r)[:6] for r in records]
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM rag_documents d JOIN vectors_{self.dimension} v ON v.rowid=d.vector_id WHERE d.index_id=?",
                        (self.index_id,),
                    ).fetchone()[0]
                    if (
                        stored["manifest"] != self.manifest
                        or actual != expected
                        or count != len(self.docs)
                    ):
                        raise VectorStoreError(
                            "保存的向量索引不完整或原文元数据不匹配，请删除该索引后重建。"
                        )
                    self.reused = True
                    return
                hashes = [digest(text) for text in texts]
                unique = dict(zip(hashes, texts))
                # JSON table avoids SQLite's variable-count limit for long novels.
                rows = conn.execute(
                    "SELECT rowid,text_hash,dimension FROM rag_vectors WHERE model_key=? AND text_hash IN (SELECT value FROM json_each(?))",
                    (self.model_key, json.dumps(list(unique))),
                ).fetchall()
                cache = {r["text_hash"]: r for r in rows}
                missing = [h for h in unique if h not in cache]
                generated = self.client.embed([unique[h] for h in missing]) if missing else []
                if len(generated) != len(missing):
                    raise VectorStoreError("Embedding 返回数量与原文分块数不一致。")
                vectors = [normalized(v) for v in generated]
                dimensions = {len(v) for v in vectors} | {r["dimension"] for r in rows}
                if len(dimensions) != 1:
                    raise VectorStoreError("Embedding 文档向量维度不一致。")
                self.dimension = dimensions.pop()
                if not isinstance(self.dimension, int) or not 1 <= self.dimension <= 8192:
                    raise VectorStoreError("保存的向量维度无效，请清理损坏索引后重建。")
                existing_model = conn.execute(
                    "SELECT dimension FROM rag_models WHERE model_key=?", (self.model_key,)
                ).fetchone()
                if existing_model and existing_model[0] != self.dimension:
                    raise VectorStoreError(
                        "Embedding 模型维度已变化，请更新 RAG_EMBEDDING_REVISION 后重建，不能混用旧向量。"
                    )
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute(
                        f"CREATE VIRTUAL TABLE IF NOT EXISTS vectors_{self.dimension} USING vec0(embedding float[{self.dimension}] distance_metric=cosine)"
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO rag_models VALUES(?,?)",
                        (self.model_key, self.dimension),
                    )
                    for h, vector in zip(missing, vectors):
                        cur = conn.execute(
                            "INSERT INTO rag_vectors(model_key,text_hash,dimension) VALUES(?,?,?)",
                            (self.model_key, h, self.dimension),
                        )
                        vector_id = cur.lastrowid
                        conn.execute(
                            f"INSERT INTO vectors_{self.dimension}(rowid,embedding) VALUES(?,?)",
                            (vector_id, _pack(vector)),
                        )
                        cache[h] = {"rowid": vector_id, "dimension": self.dimension}
                    conn.execute(
                        "INSERT INTO rag_indexes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            self.index_id,
                            self.namespace,
                            self.source_hash,
                            self.model_key,
                            self.client.embed_model,
                            self.model_revision,
                            self.dimension,
                            len(self.docs),
                            self.chunk_chars,
                            self.manifest,
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    conn.executemany(
                        "INSERT INTO rag_documents VALUES(?,?,?,?,?,?,?,?,?)",
                        [
                            (
                                self.index_id,
                                i,
                                d.doc_id,
                                d.kind,
                                d.chapter_index,
                                d.chapter_title,
                                d.text,
                                json.dumps(d.metadata, ensure_ascii=False, sort_keys=True),
                                cache[h]["rowid"],
                            )
                            for i, (d, h) in enumerate(zip(self.docs, hashes))
                        ],
                    )
                self.embedded_documents = len(missing)
            except sqlite3.Error:
                raise VectorStoreError(
                    "向量索引读写失败，未发布不完整索引；请检查存储后重试。"
                ) from None
            finally:
                conn.close()

    def scores(self, query):
        return self.scores_eligible(query, list(range(len(self.docs))))

    def scores_eligible(self, query, eligible):
        scores = [0.0] * len(self.docs)
        if not eligible:
            return scores
        vectors = self.client.embed([query])
        if len(vectors) != 1:
            raise VectorStoreError("查询向量数量无效。")
        vector = normalized(vectors[0])
        if len(vector) != self.dimension:
            raise VectorStoreError("Embedding 文档与查询向量维度不一致，请核对模型版本。")
        if not any(vector):
            return scores
        conn = _connect(self.path)
        try:
            # Scope is applied within the DB, before semantic ranking and RRF.
            rows = conn.execute(
                f"SELECT d.ordinal,1-vec_distance_cosine(v.embedding,?) score FROM rag_documents d JOIN vectors_{self.dimension} v ON v.rowid=d.vector_id WHERE d.index_id=? AND d.ordinal IN (SELECT value FROM json_each(?))",
                (_pack(vector), self.index_id, json.dumps(eligible)),
            ).fetchall()
            if len(rows) != len(eligible):
                raise VectorStoreError("当前索引已删除或不完整，请重建后查询。")
            for row in rows:
                value = row["score"]
                scores[row["ordinal"]] = (
                    max(-1.0, min(1.0, round(value, 6)))
                    if value is not None and math.isfinite(value)
                    else 0.0
                )
            return scores
        except sqlite3.Error:
            raise VectorStoreError("持久化向量查询失败，请重建索引或检查数据库。") from None
        finally:
            conn.close()

    def stats(self):
        return {
            "backend": "sqlite-vec",
            "persistent": True,
            "index_id": self.index_id,
            "source_hash": self.source_hash,
            "namespace": self.namespace,
            "dimension": self.dimension,
            "reused": self.reused,
            "embedded_documents": self.embedded_documents,
            "search": "exact_cosine",
        }


def list_indexes():
    path = database_path()
    if not path.exists():
        return []
    conn = _connect(path)
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT index_id,source_hash,model_name,model_revision,dimension,doc_count,chunk_chars,created_at FROM rag_indexes WHERE namespace=? ORDER BY created_at DESC LIMIT 200",
                (namespace(),),
            )
        ]
    except sqlite3.Error:
        raise VectorStoreError("无法读取已保存索引列表，请检查向量数据库。") from None
    finally:
        conn.close()


def delete_index(index_id):
    if not re.fullmatch(r"[a-f0-9]{64}", index_id):
        raise ValueError("索引 ID 无效。")
    path = database_path()
    conn = _connect(path)
    try:
        record = conn.execute(
            "SELECT model_key FROM rag_indexes WHERE index_id=? AND namespace=?",
            (index_id, namespace()),
        ).fetchone()
        if record is None:
            raise KeyError(index_id)
        model_key = record["model_key"]
    except sqlite3.Error:
        raise VectorStoreError("无法读取待删除索引，请检查向量数据库。") from None
    finally:
        conn.close()
    with model_lock(path, model_key):
        conn = _connect(path)
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                count = conn.execute(
                    "DELETE FROM rag_indexes WHERE index_id=? AND namespace=?",
                    (index_id, namespace()),
                ).rowcount
                if not count:
                    raise KeyError(index_id)
                rows = conn.execute(
                    "SELECT rowid,dimension FROM rag_vectors WHERE model_key=? AND rowid NOT IN (SELECT vector_id FROM rag_documents)",
                    (model_key,),
                ).fetchall()
                for row in rows:
                    if not isinstance(row["dimension"], int) or not 1 <= row["dimension"] <= 8192:
                        raise VectorStoreError("向量元数据已损坏，索引删除已回滚。")
                    conn.execute(
                        f"DELETE FROM vectors_{row['dimension']} WHERE rowid=?", (row["rowid"],)
                    )
                    conn.execute("DELETE FROM rag_vectors WHERE rowid=?", (row["rowid"],))
        except sqlite3.Error:
            raise VectorStoreError("索引删除失败，已保留原有数据，请检查存储后重试。") from None
        finally:
            conn.close()
