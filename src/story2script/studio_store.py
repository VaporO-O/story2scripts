"""Draft history with compare-and-swap saves; no background overwrite of newer edits."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from .creative_models import EditingState
from .plot_state import screenplay_hash
from .plot_store import plot_connection
from .screenplay import Screenplay


class DraftConflict(ValueError):
    pass


def save_draft(request):
    now = datetime.now(timezone.utc).isoformat()
    with plot_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft_id = request.draft_id or str(uuid4())
        row = conn.execute(
            "SELECT payload FROM plot_records WHERE kind='studio_draft' AND id=?", (draft_id,)
        ).fetchone()
        existing = json.loads(row[0]) if row else None
        if request.draft_id and existing is None:
            raise KeyError(draft_id)
        version = existing["version"] if existing else 0
        if request.expected_version != version:
            raise DraftConflict(
                "此草稿已在其他窗口更新，保留了你的编辑；请先载入最新版本或另存草稿。"
            )
        script = request.screenplay.model_copy(deep=True)
        if existing:
            baseline = Screenplay.model_validate(existing["screenplay"])
        elif request.base_yaml:
            from .yaml_export import screenplay_from_yaml

            baseline = screenplay_from_yaml(request.base_yaml)
        else:
            baseline = script
        old_scenes = {s.id: s for s in baseline.scenes}
        changed = [s.id for s in script.scenes if old_scenes.get(s.id) != s]
        if [s.id for s in script.scenes] != [s.id for s in baseline.scenes]:
            changed = [s.id for s in script.scenes]
        verified = conn.execute(
            "SELECT 1 FROM plot_records WHERE kind='studio_checked' AND id=?",
            (screenplay_hash(script),),
        ).fetchone()
        if verified:
            pass
        elif (
            changed
            or script.characters != baseline.characters
            or script.plot_state != baseline.plot_state
        ):
            dirty = set(changed) | set(baseline.editing.dirty_scene_ids if baseline.editing else [])
            if script.plot_state != baseline.plot_state or script.characters != baseline.characters:
                dirty.update(s.id for s in script.scenes)
            script.editing = EditingState(
                based_on_hash=screenplay_hash(baseline),
                dirty_scene_ids=[s.id for s in script.scenes if s.id in dirty],
                note="草稿已保存；变更的正文或剧情账本需要重新核验。",
            )
        elif baseline.editing and baseline.editing.status == "needs_check":
            script.editing = baseline.editing.model_copy(deep=True)
        payload = {
            "draft_id": draft_id,
            "version": version + 1,
            "saved_at": now,
            "title": script.title,
            "hash": screenplay_hash(script),
            "screenplay": script.model_dump(mode="json"),
        }
        for kind, key in (
            ("studio_draft", draft_id),
            ("studio_snapshot", f"{draft_id}:{version + 1:08d}"),
        ):
            conn.execute(
                "INSERT INTO plot_records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
                (kind, key, json.dumps(payload, ensure_ascii=False)),
            )
    return payload


def list_drafts():
    with plot_connection() as conn:
        rows = conn.execute("SELECT payload FROM plot_records WHERE kind='studio_draft'").fetchall()
    records = [json.loads(row[0]) for row in rows]
    return [
        {k: r[k] for k in ("draft_id", "version", "saved_at", "title", "hash")}
        for r in sorted(records, key=lambda r: r["saved_at"], reverse=True)[:100]
    ]


def draft_versions(draft_id):
    with plot_connection() as conn:
        rows = conn.execute(
            "SELECT payload FROM plot_records WHERE kind='studio_snapshot' AND id LIKE ? ORDER BY id DESC LIMIT 100",
            (draft_id + ":%",),
        ).fetchall()
    return [
        {k: r[k] for k in ("draft_id", "version", "saved_at", "title", "hash")}
        for row in rows
        for r in [json.loads(row[0])]
    ]
