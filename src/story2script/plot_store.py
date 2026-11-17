"""Durable immutable plans and checkpoints; a failed write never reports success."""

import json
import sqlite3
from contextlib import contextmanager

from .job_store import resolve_db_path


@contextmanager
def plot_connection(db_path=None):
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""CREATE TABLE IF NOT EXISTS plot_records (
            kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(kind, id))""")
        yield connection


def save_record(kind: str, record_id: str, payload: dict, *, db_path=None) -> None:
    with plot_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO plot_records VALUES (?, ?, ?) "
            "ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload",
            (kind, record_id, json.dumps(payload, ensure_ascii=False)),
        )


def load_record(kind: str, record_id: str, *, db_path=None) -> dict:
    with plot_connection(db_path) as connection:
        row = connection.execute(
            "SELECT payload FROM plot_records WHERE kind=? AND id=?", (kind, record_id)
        ).fetchone()
    if row is None:
        raise KeyError(record_id)
    return json.loads(row[0])
