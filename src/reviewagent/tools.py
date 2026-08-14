"""Safe subprocess wrappers for the review graph's deterministic tools."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
# This module is the constrained subprocess boundary for fixed review commands.
import subprocess  # nosec B404
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import ReviewFinding, ReviewRequest, Severity, ToolName, ToolResult
from .security import redact_output, truncate_output, validate_git_revision


MAX_FINDINGS_PER_TOOL = 200


@dataclass(frozen=True)
class _CompletedCommand:
    args: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    error: str = ""


class ToolRunner(Protocol):
    def run(self, tool: ToolName, request: ReviewRequest) -> ToolResult: ...


def _fingerprint(
    source: ToolName,
    rule_id: str,
    file: str,
    line: int,
    message: str,
) -> str:
    payload = "\x00".join((source, rule_id, file, str(line), message))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_finding(
    *,
    source: ToolName,
    severity: Severity,
    rule_id: str,
    title: str,
    message: str,
    file: str = "",
    line: int = 0,
    evidence: str = "",
) -> ReviewFinding:
    clean_message = redact_output(message)
    return ReviewFinding(
        fingerprint=_fingerprint(source, rule_id, file, line, clean_message),
        source=source,
        severity=severity,
        rule_id=rule_id,
        title=title,
        message=clean_message,
        file=file,
        line=line,
        evidence=truncate_output(evidence, 2_000),
    )


class SubprocessToolRunner:
    """Execute a fixed command set without a shell."""

    def __init__(self, python_executable: str | None = None) -> None:
        self.python_executable = python_executable or sys.executable
        pytest_executable = shutil.which("pytest")
        self.pytest_prefix = (
            [pytest_executable]
            if pytest_executable
            else [self.python_executable, "-m", "pytest"]
        )

    def prepare_request(self, request: ReviewRequest) -> ReviewRequest:
        base_ref = validate_git_revision(request.base_ref, "base_ref")
        head_ref = validate_git_revision(request.head_ref, "head_ref")
        candidate = Path(request.repo_path).expanduser().resolve()
        if not candidate.is_dir():
            raise ValueError(f"Repository path does not exist: {candidate}")

        root_result = self._execute(
            ["git", "rev-parse", "--show-toplevel"], candidate, request.timeout_seconds
        )
        if root_result.exit_code != 0:
            detail = root_result.stderr.strip() or root_result.error or "not a Git repository"
            raise ValueError(f"Unable to resolve Git repository: {redact_output(detail)}")
        root = Path(root_result.stdout.strip()).resolve()

        base_sha = self._resolve_commit(root, base_ref, request.timeout_seconds)
        head_sha = self._resolve_commit(root, head_ref, request.timeout_seconds)
        current_sha = self._resolve_commit(root, "HEAD", request.timeout_seconds)
        if head_sha != current_sha:
            if not request.allow_historical_head:
                raise ValueError(
                    "head_ref must resolve to the current checkout. Use a temporary worktree "
                    "or explicitly enable historical-head review."
                )
            checkout_tools = sorted(set(request.tools) - {"diff"})
            if checkout_tools:
                raise ValueError(
                    "Historical-head review can only run the diff tool because Ruff, pytest, "
                    "and Bandit inspect the current checkout. Use a temporary worktree for: "
                    + ", ".join(checkout_tools)
                )

        return request.model_copy(
            update={"repo_path": str(root), "base_ref": base_sha, "head_ref": head_sha}
        )

    def run(self, tool: ToolName, request: ReviewRequest) -> ToolResult:
        prepared = self.prepare_request(request)
        handlers = {
            "diff": self._run_diff,
            "ruff": self._run_ruff,
            "pytest": self._run_pytest,
            "bandit": self._run_bandit,
        }
        return handlers[tool](prepared)

    def _resolve_commit(self, repo: Path, revision: str, timeout: int) -> str:
        result = self._execute(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], repo, timeout
        )
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.error or "unknown revision"
            raise ValueError(f"Unable to resolve Git revision {revision!r}: {redact_output(detail)}")
        return result.stdout.strip()

    def _changed_python_files(self, request: ReviewRequest) -> list[str]:
        repo = Path(request.repo_path)
        result = self._execute(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACMR",
                "-z",
                f"{request.base_ref}...{request.head_ref}",
                "--",
            ],
            repo,
            request.timeout_seconds,
        )
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.error or "git diff failed"
            raise ValueError(redact_output(detail))

        files: list[str] = []
        for raw_name in result.stdout.split("\x00"):
            name = raw_name.strip()
            if not name.lower().endswith(".py"):
                continue
            resolved = (repo / name).resolve()
            if not resolved.is_relative_to(repo) or not resolved.is_file():
                continue
            files.append("./" + Path(name).as_posix().lstrip("/"))
        return sorted(set(files))

    def _run_diff(self, request: ReviewRequest) -> ToolResult:
        result = self._execute(
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--unified=3",
                f"{request.base_ref}...{request.head_ref}",
                "--",
            ],
            Path(request.repo_path),
            request.timeout_seconds,
        )
        if result.exit_code != 0:
            return self._failed_result("diff", result, request.max_output_chars)
        return ToolResult(
            tool="diff",
            status="passed",
            command=result.args,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            output=truncate_output(result.stdout, request.max_output_chars),
        )

    def _run_ruff(self, request: ReviewRequest) -> ToolResult:
        try:
            files = self._changed_python_files(request)
        except ValueError as exc:
            return self._configuration_failure("ruff", str(exc))
        if not files:
            return ToolResult(tool="ruff", status="skipped", error="No changed Python files.")

        args = [
            self.python_executable,
            "-m",
            "ruff",
            "check",
            "--output-format",
            "json",
            "--",
            *files,
        ]
        result = self._execute(args, Path(request.repo_path), request.timeout_seconds)
        findings = self._parse_ruff(result.stdout, Path(request.repo_path))
        if result.exit_code not in {0, 1} or (result.exit_code == 1 and not findings):
            return self._failed_result("ruff", result, request.max_output_chars)
        return ToolResult(
            tool="ruff",
            status="findings" if findings else "passed",
            command=result.args,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            output=truncate_output(result.stdout, request.max_output_chars),
            error=truncate_output(result.stderr, 2_000),
            findings=findings,
        )

    def _run_pytest(self, request: ReviewRequest) -> ToolResult:
        repo = Path(request.repo_path)
        target_args: list[str] = []
        args = [
            *self.pytest_prefix,
            "-q",
            "--disable-warnings",
            f"--import-mode={request.pytest_import_mode}",
        ]
        if request.pytest_collect_only:
            args.append("--collect-only")
        if request.pytest_targets:
            for target in request.pytest_targets:
                path_text = target.split("::", 1)[0]
                resolved = (repo / path_text).resolve()
                if not resolved.is_relative_to(repo) or not resolved.exists():
                    return self._configuration_failure(
                        "pytest", f"pytest target does not exist: {path_text}"
                    )
            target_args = ["--", *request.pytest_targets]
        temp_root = repo / ".reviewagent"
        try:
            temp_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="pytest-", dir=temp_root, ignore_cleanup_errors=True
            ) as basetemp:
                command = [*args, "--basetemp", basetemp, *target_args]
                result = self._execute(command, repo, request.timeout_seconds)
        except OSError as exc:
            return self._configuration_failure("pytest", str(exc))
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if result.exit_code == 0:
            return ToolResult(
                tool="pytest",
                status="passed",
                command=result.args,
                exit_code=0,
                duration_ms=result.duration_ms,
                output=truncate_output(combined, request.max_output_chars),
            )
        if result.exit_code is None:
            return self._failed_result("pytest", result, request.max_output_chars)

        file, line = self._pytest_location(combined)
        finding = make_finding(
            source="pytest",
            severity="high",
            rule_id="PYTEST_FAILURE",
            title="Test suite failed",
            message=self._pytest_summary(combined, result.exit_code),
            file=file,
            line=line,
            evidence=combined,
        )
        return ToolResult(
            tool="pytest",
            status="findings",
            command=result.args,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            output=truncate_output(combined, request.max_output_chars),
            findings=[finding],
        )

    def _run_bandit(self, request: ReviewRequest) -> ToolResult:
        try:
            files = self._changed_python_files(request)
        except ValueError as exc:
            return self._configuration_failure("bandit", str(exc))
        if not files:
            return ToolResult(tool="bandit", status="skipped", error="No changed Python files.")

        args = [self.python_executable, "-m", "bandit", "-f", "json", "-q", *files]
        result = self._execute(args, Path(request.repo_path), request.timeout_seconds)
        findings = self._parse_bandit(result.stdout, Path(request.repo_path))
        if result.exit_code not in {0, 1} or (result.exit_code == 1 and not findings):
            return self._failed_result("bandit", result, request.max_output_chars)
        return ToolResult(
            tool="bandit",
            status="findings" if findings else "passed",
            command=result.args,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            output=truncate_output(result.stdout, request.max_output_chars),
            error=truncate_output(result.stderr, 2_000),
            findings=findings,
        )

    def _execute(self, args: list[str], cwd: Path, timeout: int) -> _CompletedCommand:
        started = time.perf_counter()
        try:
            # Arguments are fixed arrays; user revisions are validated before reaching this call.
            completed = subprocess.run(  # nosec B603
                args,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
            )
            return _CompletedCommand(
                args=list(args),
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_ms=int((time.perf_counter() - started) * 1_000),
            )
        except subprocess.TimeoutExpired as exc:
            return _CompletedCommand(
                args=list(args),
                exit_code=None,
                stdout=self._coerce_output(exc.stdout),
                stderr=self._coerce_output(exc.stderr),
                duration_ms=int((time.perf_counter() - started) * 1_000),
                error=f"Command timed out after {timeout} seconds.",
            )
        except OSError as exc:
            return _CompletedCommand(
                args=list(args),
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=int((time.perf_counter() - started) * 1_000),
                error=redact_output(str(exc)),
            )

    @staticmethod
    def _coerce_output(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value or ""

    @staticmethod
    def _relative_file(filename: str, repo: Path) -> str:
        candidate = Path(filename)
        try:
            return candidate.resolve().relative_to(repo).as_posix()
        except (OSError, ValueError):
            return candidate.as_posix()

    def _parse_ruff(self, output: str, repo: Path) -> list[ReviewFinding]:
        try:
            payload = json.loads(output or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []

        findings: list[ReviewFinding] = []
        for row in payload[:MAX_FINDINGS_PER_TOOL]:
            if not isinstance(row, dict):
                continue
            location = row.get("location") if isinstance(row.get("location"), dict) else {}
            code = str(row.get("code") or "RUFF")
            message = str(row.get("message") or "Ruff reported a lint violation.")
            filename = self._relative_file(str(row.get("filename") or ""), repo)
            line = int(location.get("row") or 0)
            severity: Severity = "high" if code.startswith(("F", "E9")) else "medium"
            findings.append(
                make_finding(
                    source="ruff",
                    severity=severity,
                    rule_id=code,
                    title=f"Ruff {code}",
                    message=message,
                    file=filename,
                    line=line,
                    evidence=f"{filename}:{line}: {code} {message}",
                )
            )
        if len(payload) > MAX_FINDINGS_PER_TOOL:
            findings.append(self._truncation_finding("ruff", len(payload)))
        return findings

    def _parse_bandit(self, output: str, repo: Path) -> list[ReviewFinding]:
        try:
            payload = json.loads(output or "{}")
        except json.JSONDecodeError:
            return []
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []

        findings: list[ReviewFinding] = []
        for row in rows[:MAX_FINDINGS_PER_TOOL]:
            if not isinstance(row, dict):
                continue
            raw_severity = str(row.get("issue_severity") or "LOW").lower()
            severity: Severity = raw_severity if raw_severity in {"high", "medium", "low"} else "low"  # type: ignore[assignment]
            rule_id = str(row.get("test_id") or "BANDIT")
            message = str(row.get("issue_text") or "Bandit reported a security issue.")
            filename = self._relative_file(str(row.get("filename") or ""), repo)
            line = int(row.get("line_number") or 0)
            findings.append(
                make_finding(
                    source="bandit",
                    severity=severity,
                    rule_id=rule_id,
                    title=f"Bandit {rule_id}",
                    message=message,
                    file=filename,
                    line=line,
                    evidence=str(row.get("code") or ""),
                )
            )
        if len(rows) > MAX_FINDINGS_PER_TOOL:
            findings.append(self._truncation_finding("bandit", len(rows)))
        return findings

    @staticmethod
    def _truncation_finding(tool: ToolName, total: int) -> ReviewFinding:
        return make_finding(
            source=tool,
            severity="info",
            rule_id="TOOL_FINDINGS_TRUNCATED",
            title=f"{tool} findings were truncated",
            message=(
                f"Stored the first {MAX_FINDINGS_PER_TOOL} of {total} findings. "
                "Inspect the bounded raw tool output for additional context."
            ),
        )

    @staticmethod
    def _pytest_location(output: str) -> tuple[str, int]:
        location = re.search(r"(?m)^([^\r\n:]+\.py):(\d+)(?::|$)", output)
        if location:
            return location.group(1).replace("\\", "/"), int(location.group(2))
        failed = re.search(r"(?m)^FAILED\s+([^\s:]+\.py)(?:::|\s|$)", output)
        if failed:
            return failed.group(1).replace("\\", "/"), 0
        return "", 0

    @staticmethod
    def _pytest_summary(output: str, exit_code: int) -> str:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        for line in reversed(lines):
            if re.search(r"\b(failed|error|errors)\b", line, re.IGNORECASE):
                return line[:500]
        return f"pytest exited with code {exit_code}."

    def _configuration_failure(self, tool: ToolName, message: str) -> ToolResult:
        clean_message = redact_output(message)
        finding = make_finding(
            source=tool,
            severity="high",
            rule_id="TOOL_CONFIGURATION_FAILED",
            title=f"{tool} could not prepare its inputs",
            message=clean_message,
        )
        return ToolResult(
            tool=tool, status="failed", error=clean_message, findings=[finding]
        )

    def _failed_result(
        self, tool: ToolName, result: _CompletedCommand, output_limit: int
    ) -> ToolResult:
        combined = "\n".join(
            part for part in (result.stdout, result.stderr, result.error) if part
        )
        message = result.error or result.stderr.strip() or f"{tool} exited unexpectedly."
        finding = make_finding(
            source=tool,
            severity="high",
            rule_id="TOOL_EXECUTION_FAILED",
            title=f"{tool} execution failed",
            message=message,
            evidence=combined,
        )
        return ToolResult(
            tool=tool,
            status="failed",
            command=result.args,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            output=truncate_output(combined, output_limit),
            error=truncate_output(message, 2_000),
            findings=[finding],
        )
