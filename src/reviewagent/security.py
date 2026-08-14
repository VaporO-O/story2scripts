"""Input validation and redaction for subprocess-backed review tools."""

from __future__ import annotations

import os
import re


_REVISION_PATTERN = re.compile(r"^[^-\s\x00][^\s\x00]{0,199}$")
_THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_REPORT_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}", re.IGNORECASE),
)


def validate_git_revision(value: str, field_name: str = "revision") -> str:
    revision = str(value)
    if not _REVISION_PATTERN.fullmatch(revision):
        raise ValueError(
            f"Invalid {field_name}: revisions must be non-empty, contain no whitespace, "
            "and must not start with '-'."
        )
    return revision


def validate_thread_id(value: str) -> str:
    thread_id = str(value)
    if not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise ValueError("Thread id may contain only letters, numbers, '-' and '_'.")
    return thread_id


def validate_report_prefix(value: str) -> str:
    prefix = str(value)
    if not _REPORT_PREFIX_PATTERN.fullmatch(prefix):
        raise ValueError("Report prefix may contain only letters, numbers, '.', '-' and '_'.")
    return prefix


def redact_output(value: str) -> str:
    result = str(value or "")
    for env_name in ("AI_API_KEY", "AI_BASE_URL", "GITHUB_TOKEN", "GH_TOKEN"):
        secret = os.getenv(env_name, "").strip()
        if len(secret) >= 8:
            result = result.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def truncate_output(value: str, limit: int) -> str:
    text = redact_output(value)
    if len(text) <= limit:
        return text
    marker = f"\n... [truncated {len(text) - limit} chars] ...\n"
    remaining = max(0, limit - len(marker))
    head = remaining * 2 // 3
    tail = remaining - head
    return text[:head] + marker + text[-tail:]
