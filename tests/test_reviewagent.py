import json
import subprocess
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from reviewagent.evaluation import load_review_dataset, summarize_evaluation
from reviewagent.evaluation import ReviewEvalCaseReport
from reviewagent.models import (
    ApprovalDecision,
    ReviewFinding,
    ReviewRequest,
    ToolResult,
)
from reviewagent.reporting import render_review_markdown, write_review_reports
from reviewagent.runner import resume_review, start_review
from reviewagent.security import (
    redact_output,
    truncate_output,
    validate_git_revision,
    validate_report_prefix,
)
from reviewagent.synthesis import DeterministicSynthesizer, LLMReviewSynthesizer
from reviewagent.tools import MAX_FINDINGS_PER_TOOL, SubprocessToolRunner, make_finding


ROOT = Path(__file__).parents[1]
DATASET = ROOT / "evals" / "reviewagent" / "v1" / "cases.json"


class BarrierToolRunner:
    def __init__(self, count: int) -> None:
        self.barrier = threading.Barrier(count, timeout=5)
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def run(self, tool, request):
        del request
        with self.lock:
            self.calls.append(tool)
        self.barrier.wait()
        return ToolResult(tool=tool, status="passed", output=f"{tool} ok")


def test_review_request_rejects_duplicate_tools() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        ReviewRequest(repo_path=".", base_ref="main", tools=["diff", "diff"])


@pytest.mark.parametrize("target", ["../outside.py", "-k", "C:/outside.py", "bad\x00.py"])
def test_review_request_rejects_unsafe_pytest_targets(target: str) -> None:
    with pytest.raises(ValidationError, match="pytest targets"):
        ReviewRequest(repo_path=".", base_ref="main", pytest_targets=[target])


@pytest.mark.parametrize(
    "revision", ["", "-pwn", "HEAD main", "HEAD\nmain", "HEAD ", "\x00"]
)
def test_git_revision_validation_rejects_unsafe_values(revision: str) -> None:
    with pytest.raises(ValueError, match="Invalid"):
        validate_git_revision(revision)


def test_report_prefix_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="Report prefix"):
        validate_report_prefix("../report")


def test_redaction_happens_before_checkpoint_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_API_KEY", "secret-value-123")
    text = "prefix secret-value-123 sk-abcdefgh12345 " + "x" * 100

    redacted = truncate_output(text, 60)

    assert "secret-value-123" not in redacted
    assert "sk-abcdefgh12345" not in redacted
    assert "truncated" in redacted
    assert redact_output("ordinary") == "ordinary"


def test_tool_parsers_create_structured_findings(tmp_path: Path) -> None:
    runner = SubprocessToolRunner()
    ruff_payload = json.dumps(
        [
            {
                "code": "F821",
                "message": "Undefined name `missing`",
                "filename": str(tmp_path / "sample.py"),
                "location": {"row": 7, "column": 1},
            }
        ]
    )
    bandit_payload = json.dumps(
        {
            "results": [
                {
                    "test_id": "B602",
                    "issue_severity": "HIGH",
                    "issue_text": "subprocess call with shell=True",
                    "filename": str(tmp_path / "sample.py"),
                    "line_number": 9,
                    "code": "subprocess.run(cmd, shell=True)",
                }
            ]
        }
    )

    ruff = runner._parse_ruff(ruff_payload, tmp_path)
    bandit = runner._parse_bandit(bandit_payload, tmp_path)

    assert (ruff[0].rule_id, ruff[0].severity, ruff[0].file, ruff[0].line) == (
        "F821",
        "high",
        "sample.py",
        7,
    )
    assert (bandit[0].rule_id, bandit[0].severity, bandit[0].line) == (
        "B602",
        "high",
        9,
    )


def test_tool_parsers_bound_checkpoint_finding_count(tmp_path: Path) -> None:
    runner = SubprocessToolRunner()
    rows = [
        {
            "code": "F821",
            "message": f"Undefined name {index}",
            "filename": str(tmp_path / "sample.py"),
            "location": {"row": index + 1, "column": 1},
        }
        for index in range(MAX_FINDINGS_PER_TOOL + 5)
    ]

    findings = runner._parse_ruff(json.dumps(rows), tmp_path)

    assert len(findings) == MAX_FINDINGS_PER_TOOL + 1
    assert findings[-1].rule_id == "TOOL_FINDINGS_TRUNCATED"


def test_send_fanout_and_sqlite_resume_across_graph_instances(tmp_path: Path) -> None:
    tools = ["diff", "ruff", "pytest", "bandit"]
    runner = BarrierToolRunner(len(tools))
    checkpoint = tmp_path / "review.sqlite3"
    request = ReviewRequest(repo_path=str(tmp_path), base_ref="main", tools=tools)

    initial = start_review(
        request,
        checkpoint_path=checkpoint,
        thread_id="parallel-review",
        tool_runner=runner,
        synthesizer=DeterministicSynthesizer(),
        validate_repository=False,
    )

    assert initial.awaiting_approval is True
    assert initial.report.status == "awaiting_approval"
    assert sorted(runner.calls) == sorted(tools)
    assert [result.tool for result in initial.report.tool_results] == tools

    resumed = resume_review(
        checkpoint_path=checkpoint,
        thread_id="parallel-review",
        decision=ApprovalDecision(approved=True, comment="checked"),
        tool_runner=BarrierToolRunner(len(tools)),
        synthesizer=DeterministicSynthesizer(),
    )

    assert resumed.awaiting_approval is False
    assert resumed.report.status == "approved"
    assert resumed.report.decision == ApprovalDecision(approved=True, comment="checked")


def test_start_rejects_reusing_a_persistent_thread(tmp_path: Path) -> None:
    checkpoint = tmp_path / "review.sqlite3"
    request = ReviewRequest(repo_path=str(tmp_path), base_ref="main", tools=["diff"])

    class PassingRunner:
        def run(self, tool, request):
            return ToolResult(tool=tool, status="passed")

    start_review(
        request,
        checkpoint_path=checkpoint,
        thread_id="same-thread",
        tool_runner=PassingRunner(),
        synthesizer=DeterministicSynthesizer(),
        validate_repository=False,
    )
    with pytest.raises(ValueError, match="already exists"):
        start_review(
            request,
            checkpoint_path=checkpoint,
            thread_id="same-thread",
            tool_runner=PassingRunner(),
            synthesizer=DeterministicSynthesizer(),
            validate_repository=False,
        )


def test_real_git_diff_and_ruff_are_limited_to_changed_python(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(  # nosec B603 - fixed test-only git argv
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )
        return result.stdout.strip()

    git("init")
    git("config", "user.email", "reviewagent@example.test")
    git("config", "user.name", "Review Agent")
    (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "notes.txt").write_text("old\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")

    (repo / "sample.py").write_text("print(missing)\n", encoding="utf-8")
    (repo / "notes.txt").write_text("new\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "head")

    request = ReviewRequest(
        repo_path=str(repo), base_ref=base, head_ref="HEAD", tools=["diff", "ruff"]
    )
    runner = SubprocessToolRunner()
    prepared = runner.prepare_request(request)
    diff = runner.run("diff", request)
    ruff = runner.run("ruff", request)

    assert diff.status == "passed"
    assert prepared.base_ref == base
    assert prepared.head_ref == git("rev-parse", "HEAD")
    assert "+print(missing)" in diff.output
    assert ruff.status == "findings"
    assert [(row.rule_id, row.file) for row in ruff.findings] == [("F821", "sample.py")]
    assert "notes.txt" not in " ".join(ruff.command)
    with pytest.raises(ValueError, match="temporary worktree"):
        runner.prepare_request(
            ReviewRequest(
                repo_path=str(repo),
                base_ref=base,
                head_ref=base,
                tools=["ruff"],
                allow_historical_head=True,
            )
        )


def test_llm_synthesis_keeps_tool_findings_and_adds_diff_findings() -> None:
    tool_finding = make_finding(
        source="ruff",
        severity="high",
        rule_id="F821",
        title="Undefined name",
        message="missing is undefined",
        file="sample.py",
        line=2,
    )

    class Completer:
        prompt = ""

        def complete_json(self, prompt, temperature=None, use_cache=True, *, prompt_id=""):
            self.prompt = prompt
            assert temperature == 0.0
            assert use_cache is False
            assert prompt_id == "reviewagent.synthesis"
            return json.dumps(
                {
                    "overview": "One behavioral issue.",
                    "findings": [
                        {
                            "source": "diff",
                            "severity": "medium",
                            "rule_id": "LOGIC_001",
                            "title": "Wrong branch",
                            "message": "The condition is reversed.",
                            "file": "sample.py",
                            "line": 4,
                            "evidence": "if not ready",
                        }
                    ],
                }
            )

    completer = Completer()
    summary = LLMReviewSynthesizer(completer).synthesize(
        ReviewRequest(repo_path=".", base_ref="main", mode="ai"),
        [ToolResult(tool="ruff", status="findings", findings=[tool_finding])],
    )

    assert summary.verdict == "changes_requested"
    assert {row.rule_id for row in summary.findings} == {"F821", "LOGIC_001"}
    assert "untrusted data" in completer.prompt


def test_evaluation_dataset_and_metrics_cover_defect_and_clean_cases() -> None:
    dataset = load_review_dataset(DATASET)
    finding = ReviewFinding(
        fingerprint="12345678",
        source="pytest",
        severity="high",
        rule_id="PYTEST_FAILURE",
        title="failed",
        message="collection failed",
    )
    reports = [
        ReviewEvalCaseReport(
            case_id="defect",
            title="defect",
            status="completed",
            duration_ms=1,
            expected_rules=["PYTEST_FAILURE"],
            predicted_rules=["PYTEST_FAILURE"],
            matched_rules=["PYTEST_FAILURE"],
            findings=[finding],
        ),
        ReviewEvalCaseReport(
            case_id="clean",
            title="clean",
            status="completed",
            duration_ms=1,
            expected_rules=[],
            predicted_rules=[],
            matched_rules=[],
        ),
    ]

    summary = summarize_evaluation(reports)

    assert dataset.version == "reviewagent-eval-v1"
    assert len(dataset.cases) == 2
    assert all(not case.pytest_collect_only for case in dataset.cases)
    assert all("::test_converter_prompt" in case.pytest_targets[0] for case in dataset.cases)
    assert all(case.pytest_import_mode == "append" for case in dataset.cases)
    assert summary.recall == 1.0
    assert summary.false_positive_rate == 0.0


def test_failed_clean_evaluation_case_is_not_counted_as_true_negative() -> None:
    summary = summarize_evaluation(
        [
            ReviewEvalCaseReport(
                case_id="clean-failed",
                title="clean",
                status="failed",
                duration_ms=1,
                expected_rules=[],
                predicted_rules=[],
                matched_rules=[],
                error="worktree unavailable",
            )
        ]
    )

    assert summary.failed_case_count == 1
    assert summary.true_negative_count == 0


def test_review_report_writes_json_and_markdown(tmp_path: Path) -> None:
    class PassingRunner:
        def run(self, tool, request):
            return ToolResult(tool=tool, status="passed")

    run = start_review(
        ReviewRequest(repo_path=str(tmp_path), base_ref="main", tools=["diff"]),
        checkpoint_path=tmp_path / "checkpoint.sqlite3",
        thread_id="report-test",
        tool_runner=PassingRunner(),
        synthesizer=DeterministicSynthesizer(),
        validate_repository=False,
    )
    json_path, markdown_path = write_review_reports(run.report, tmp_path / "reports")

    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "awaiting_approval"
    assert markdown_path.read_text(encoding="utf-8") == render_review_markdown(run.report)
