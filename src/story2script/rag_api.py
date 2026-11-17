"""Manage persistent vector snapshots independently of Redis response caching."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .parser import parse_chapters
from .rag import build_story_knowledge
from .security import redact_secrets
from .vector_store import (
    VectorStoreError,
    database_path,
    delete_index,
    list_indexes,
    namespace,
    storage_mode,
)

router = APIRouter(prefix="/api/rag", tags=["rag-indexes"])


class BuildIndexRequest(BaseModel):
    novel_text: str = Field(min_length=1, max_length=2_000_000)


@router.get("/indexes")
def get_indexes():
    try:
        return {
            "backend": "sqlite-vec",
            "configured_storage": storage_mode(),
            "namespace": namespace(),
            "exists": database_path().exists(),
            "indexes": list_indexes(),
        }
    except VectorStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, redact_secrets(str(exc))) from exc


@router.post("/indexes")
def build_index(request: BuildIndexRequest):
    try:
        if storage_mode() != "sqlite":
            raise ValueError("当前 RAG_STORAGE=memory，请启用 sqlite 后再建立持久化索引。")
        knowledge = build_story_knowledge(
            parse_chapters(request.novel_text), mode="ai", retrieval="embedding"
        )
        if not knowledge.storage_info.get("persistent"):
            raise ValueError(
                "；".join(knowledge.warnings) or "Embedding 模型不可用，尚未建立向量库。"
            )
        return knowledge.stats()
    except ValueError as exc:
        raise HTTPException(422, redact_secrets(str(exc))) from exc


@router.delete("/indexes/{index_id}")
def remove_index(index_id: str):
    try:
        delete_index(index_id)
        return {
            "deleted": index_id,
            "message": "已删除该索引及不再共享的向量，原小说和剧本未修改。",
        }
    except KeyError as exc:
        raise HTTPException(404, "当前命名空间下没有该索引。") from exc
    except VectorStoreError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
