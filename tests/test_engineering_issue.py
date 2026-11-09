"""Offline evidence for bounded Issue research-input preparation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import ai_office.engineering_issue as engineering_issue
from ai_office.cli import app
from ai_office.definitions.employee import load_employees
from ai_office.definitions.workflow import load_workflows
from ai_office.engine.local_execution import (
    LocalExecutionPolicy,
    validate_local_execution_scope,
)
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    build_workflow_run_manifest,
    persist_workflow_run_manifest,
)
from ai_office.github_snapshot import GitHubRawResponse

_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY = "example/project"
_ISSUE_URL = "https://github.com/example/project/issues/828"
_ORACLE_PROFILE = "python-syntax"


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def fixed_repository(tmp_path: Path) -> tuple[Path, str, dict[str, bytes]]:
    root = tmp_path / "repository"
    root.mkdir()
    sources = {
        "src/pkg/engine.py": b"def calculate(values):\n    return sum(values)\n",
        "tests/unit/test_engine.py": (
            b"from engine import calculate\n\n"
            b"def test_calculate():\n    assert calculate([1, 2]) == 3\n"
        ),
        "src/pkg/unused.py": b"def unused():\n    return None\n",
    }
    for relative, contents in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Offline Test")
    _git(root, "config", "user.email", "offline@example.invalid")
    _git(root, "add", ".")
    environment = dict(os.environ)
    environment["GIT_AUTHOR_DATE"] = "2000-01-01T00:00:00Z"
    environment["GIT_COMMITTER_DATE"] = "2000-01-01T00:00:00Z"
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "fixture"],
        check=True,
        env=environment,
    )
    _git(root, "remote", "add", "origin", "https://github.com/example/project.git")
    revision = _git(root, "rev-parse", "HEAD")
    return root, revision, sources


def _prepare(
    repository: Path,
    revision: str,
    *,
    initial_files: int = 1,
    max_files: int = 8,
    send_patterns: tuple[str, ...] = ("src/**/*.py", "tests/**/*.py"),
    send_issue_body: bool = True,
    oracle_profile: str = _ORACLE_PROFILE,
    transport=None,
) -> tuple[str, dict[str, object], list[object]]:
    requests: list[object] = []

    def fake_transport(request):
        requests.append(request)
        return GitHubRawResponse(
            200,
            json.dumps(
                {
                    "number": 828,
                    "title": "Correct calculation handling",
                    "body": (
                        "The calculation gives an incorrect result for a small input."
                    ),
                }
            ).encode("utf-8"),
        )

    input_text, summary = engineering_issue.prepare_engineering_issue_input(
        issue_url=_ISSUE_URL,
        repository=_REPOSITORY,
        repository_path=repository,
        base_revision=revision,
        search_patterns=("src/**/*.py", "tests/**/*.py"),
        read_patterns=("src/**/*.py", "tests/**/*.py"),
        send_patterns=send_patterns,
        write_patterns=("src/**/*.py",),
        initial_files=initial_files,
        max_files=max_files,
        max_source_bytes=100_000,
        max_provider_input_bytes=900_000,
        max_provider_total_bytes=3_600_000,
        max_provider_attempts=3,
        oracle_profile=oracle_profile,
        send_issue_body=send_issue_body,
        github_transport=transport or fake_transport,
    )
    return input_text, summary, requests


def _successful_run(
    run_store: Path,
    run_id: str,
    run_input: str,
    output: dict[str, object],
) -> Path:
    run_store.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = WorkflowRunManifestStore(run_store)
    workflow = next(
        item
        for item in load_workflows(_ROOT / "workflows")
        if item.definition.id == "engineering-issue-investigation"
    )
    employee = next(
        item
        for item in load_employees(_ROOT / "employees")
        if item.definition.id == "general-researcher"
    )
    manifest = build_workflow_run_manifest(
        run_id,
        run_input,
        workflow,
        (employee,),
    )
    persist_workflow_run_manifest(store, manifest)
    return store.state_path(run_id)


def _ready_plan() -> dict[str, object]:
    return {
        "outcome": "ready",
        "cause_hypotheses": [
            {
                "hypothesis": "The implementation only sums the supplied values.",
                "confidence": "medium",
                "evidence": ["src/pkg/engine.py"],
            }
        ],
        "evidence": [
            {
                "path": "src/pkg/engine.py",
                "start_line": 2,
                "end_line": 2,
                "quote": "return sum(values)",
                "reason": "This is the current calculation behavior.",
            }
        ],
        "read_paths": ["src/pkg/engine.py", "tests/unit/test_engine.py"],
        "additional_read_paths": [],
        "write_paths": ["src/pkg/engine.py"],
        "test_paths": ["tests/unit/test_engine.py"],
        "acceptance_criteria": ["The reported calculation case is handled."],
    }


def _install_successful_history(
    monkeypatch: pytest.MonkeyPatch,
    histories: dict[Path, str],
) -> None:
    def fake_history(targets, *, require_terminal_evidence):
        assert require_terminal_evidence is True
        output = histories[targets.state_path]
        return SimpleNamespace(
            state=SimpleNamespace(
                status="succeeded",
                workflow_id="engineering-issue-investigation",
                current_step_id="investigate",
                current_step_index=1,
            ),
            events=[
                SimpleNamespace(
                    event_type="step_succeeded",
                    step_id="investigate",
                    step_index=1,
                    output_text=output,
                )
            ],
        )

    monkeypatch.setattr(
        engineering_issue, "load_workflow_execution_history", fake_history
    )


def test_initial_preparation_is_fixed_base_scoped_and_get_only(
    fixed_repository: tuple[Path, str, dict[str, bytes]],
) -> None:
    repository, revision, source_bytes = fixed_repository
    run_input, summary, requests = _prepare(repository, revision)
    value = json.loads(run_input)

    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].path == "/repos/example/project/issues/828"
    assert value["repository"]["base_revision"] == revision
    assert value["issue"]["body"]
    assert len(value["source_context"]) == 1
    selected = next(iter(value["source_context"]))
    assert selected in source_bytes
    assert value["source_context"][selected].encode() == source_bytes[selected]
    assert (
        value["source_sha256"][selected]
        == hashlib.sha256(source_bytes[selected]).hexdigest()
    )
    assert all(
        source["path"].startswith(("src/", "tests/"))
        for source in value["available_sources"]
    )
    assert summary["workflow_id"] == "engineering-issue-investigation"
    assert summary["oracle_profile"] == _ORACLE_PROFILE
    assert value["authorization"]["oracle_profile"] == _ORACLE_PROFILE
    assert value["provider_input_bytes_used"] == summary["provider_input_bytes"]


def test_issue_body_send_must_be_explicit_and_stops_before_get(
    fixed_repository: tuple[Path, str, dict[str, bytes]],
) -> None:
    repository, revision, _ = fixed_repository
    called = False

    def forbidden_transport(_request):
        nonlocal called
        called = True
        raise AssertionError("GET must not happen without explicit body consent")

    with pytest.raises(engineering_issue.EngineeringIssueError) as caught:
        _prepare(
            repository,
            revision,
            send_issue_body=False,
            transport=forbidden_transport,
        )
    assert caught.value.classification == "issue_provider_send_not_approved"
    assert called is False


def test_unregistered_oracle_profile_stops_before_issue_get(
    fixed_repository: tuple[Path, str, dict[str, bytes]],
) -> None:
    repository, revision, _ = fixed_repository
    called = False

    def forbidden_transport(_request):
        nonlocal called
        called = True
        raise AssertionError("an unregistered Oracle must stop before GitHub GET")

    with pytest.raises(engineering_issue.EngineeringIssueError) as caught:
        _prepare(
            repository,
            revision,
            oracle_profile="model-selected-command",
            transport=forbidden_transport,
        )
    assert caught.value.classification == "oracle_profile_invalid"
    assert called is False


def test_prepare_cli_writes_run_input_exclusively_with_private_mode(
    tmp_path: Path,
    fixed_repository: tuple[Path, str, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, revision, _ = fixed_repository
    output_directory = tmp_path / "output"
    output_directory.mkdir(mode=0o700)
    original = engineering_issue.prepare_engineering_issue_input

    def fake_prepare(**kwargs):
        kwargs["github_transport"] = lambda _request: GitHubRawResponse(
            200,
            json.dumps(
                {
                    "number": 828,
                    "title": "Correct calculation handling",
                    "body": "The calculation gives an incorrect result.",
                }
            ).encode("utf-8"),
        )
        return original(**kwargs)

    monkeypatch.setattr(
        engineering_issue, "prepare_engineering_issue_input", fake_prepare
    )
    result = CliRunner().invoke(
        app,
        [
            "workflows",
            "prepare-engineering-issue",
            "--issue-url",
            _ISSUE_URL,
            "--repository",
            _REPOSITORY,
            "--repository-path",
            str(repository),
            "--base-revision",
            revision,
            "--search-pattern",
            "src/**/*.py",
            "--search-pattern",
            "tests/**/*.py",
            "--read-pattern",
            "src/**/*.py",
            "--read-pattern",
            "tests/**/*.py",
            "--send-pattern",
            "src/**/*.py",
            "--send-pattern",
            "tests/**/*.py",
            "--write-pattern",
            "src/**/*.py",
            "--initial-files",
            "1",
            "--max-files",
            "8",
            "--max-source-bytes",
            "100000",
            "--max-provider-input-bytes",
            "900000",
            "--max-provider-total-bytes",
            "3600000",
            "--max-provider-attempts",
            "3",
            "--oracle-profile",
            _ORACLE_PROFILE,
            "--output-directory",
            str(output_directory),
            "--send-issue-body",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    output_file = output_directory / summary["output"]
    assert summary["output"] == "engineering-issue-round-1.json"
    value = json.loads(output_file.read_text(encoding="utf-8"))
    assert value["round"] == 1
    assert value["authorization"]["oracle_profile"] == _ORACLE_PROFILE
    assert output_file.stat().st_mode & 0o777 == 0o600


def test_provider_scope_excludes_readable_but_unapproved_paths(
    fixed_repository: tuple[Path, str, dict[str, bytes]],
) -> None:
    repository, revision, _ = fixed_repository
    run_input, _summary, _requests = _prepare(
        repository,
        revision,
        initial_files=3,
        send_patterns=("src/**/*.py",),
    )
    value = json.loads(run_input)

    assert value["available_sources"]
    assert all(item["path"].startswith("src/") for item in value["available_sources"])
    assert all(path.startswith("src/") for path in value["source_context"])


def test_candidate_count_over_budget_selects_a_finite_reported_subset(
    fixed_repository: tuple[Path, str, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, revision, _ = fixed_repository
    for index in range(70):
        path = repository / f"src/pkg/module_{index:03}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"VALUE_{index} = {index}\n", encoding="utf-8")
    for index in range(63):
        path = repository / f"tests/unit/test_module_{index:03}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"def test_value_{index}():\n    assert {index} == {index}\n",
            encoding="utf-8",
        )
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "add bounded-search candidates")
    revision = _git(repository, "rev-parse", "HEAD")
    read_blob = engineering_issue._read_blob
    read_paths: list[str] = []

    def record_blob_read(root: Path, entry) -> bytes:
        read_paths.append(entry.path)
        return read_blob(root, entry)

    monkeypatch.setattr(engineering_issue, "_read_blob", record_blob_read)

    run_input, summary, _requests = _prepare(
        repository, revision, max_files=12
    )
    value = json.loads(run_input)
    selection = value["authorization"]["candidate_selection"]

    assert len(value["available_sources"]) == 12
    assert selection["matched_tree_path_count"] == 136
    assert selection["eligible_python_candidate_count"] == 136
    assert selection["write_candidate_count"] == 72
    assert selection["read_only_candidate_count"] == 64
    assert selection["candidate_limit"] == 12
    assert selection["provided_candidate_count"] == 12
    assert selection["provided_write_candidate_count"] == 6
    assert selection["provided_read_only_candidate_count"] == 6
    assert selection["unselected_candidate_count"] == 124
    assert selection["omitted_candidate_count"] == 124
    assert selection["truncated"] is True
    assert selection["search_patterns"] == ["src/**/*.py", "tests/**/*.py"]
    assert selection["read_patterns"] == ["src/**/*.py", "tests/**/*.py"]
    assert selection["provider_send_patterns"] == [
        "src/**/*.py",
        "tests/**/*.py",
    ]
    assert (
        selection["selection_method"] == engineering_issue._CANDIDATE_SELECTION_METHOD
    )
    assert selection["limitation"]
    assert read_paths == summary["selected_initial_paths"]
    assert len(read_paths) == 1
    assert sum(item["write_allowed"] for item in value["available_sources"]) == 6
    assert sum(not item["write_allowed"] for item in value["available_sources"]) == 6
    assert all(
        item["path"].startswith("tests/")
        for item in value["available_sources"]
        if not item["write_allowed"]
    )
    assert set(summary["selected_candidate_reasons"]) == {
        item["path"] for item in value["available_sources"]
    }
    assert summary["candidate_selection"] == selection


@pytest.mark.parametrize(
    ("path", "pattern", "matches"),
    [
        ("src/engine.py", "src/**/*.py", True),
        ("src/pkg/deep/engine.py", "src/**/*.py", True),
        ("tests/unit/test_engine.py", "tests/unit/*.py", True),
        ("tests/unit/deep/test_engine.py", "tests/unit/*.py", False),
    ],
)
def test_scope_globs_use_segmented_recursive_semantics(
    path: str, pattern: str, matches: bool
) -> None:
    assert engineering_issue._matches_any(path, (pattern,)) is matches


@pytest.mark.parametrize(
    ("pattern", "root"),
    [
        ("src/**/*.py", "src"),
        ("src/pkg/**/*.py", "src/pkg"),
        ("src/pkg/generated_*.py", "src/pkg"),
        ("*.py", "."),
        ("src/pkg/module.py", "src/pkg/module.py"),
    ],
)
def test_git_tree_search_roots_are_the_narrowest_fixed_prefix(
    pattern: str, root: str
) -> None:
    assert engineering_issue._search_roots((pattern,)) == (root,)


def test_finite_additional_read_and_job_input_use_exact_sent_bytes(
    tmp_path: Path,
    fixed_repository: tuple[Path, str, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, revision, source_bytes = fixed_repository
    first_input, _summary, _requests = _prepare(repository, revision, initial_files=1)
    first_value = json.loads(first_input)
    sent_path = next(iter(first_value["source_context"]))
    assert sent_path == "src/pkg/engine.py"
    more_plan = {
        "outcome": "needs_more_evidence",
        "cause_hypotheses": [
            {
                "hypothesis": (
                    "The implementation behavior needs comparison with its test."
                ),
                "confidence": "low",
                "evidence": ["src/pkg/engine.py"],
            }
        ],
        "evidence": [
            {
                "path": "src/pkg/engine.py",
                "start_line": 99,
                "end_line": 99,
                "quote": "return sum(values)",
                "reason": "This is the code being investigated.",
            }
        ],
        "read_paths": ["src/pkg/engine.py"],
        "additional_read_paths": ["tests/unit/test_engine.py"],
        "write_paths": [],
        "test_paths": [],
        "acceptance_criteria": [],
    }
    run_store = tmp_path / "runs"
    first_state_path = _successful_run(
        run_store,
        "issue-round-1",
        first_input,
        more_plan,
    )
    histories = {first_state_path: json.dumps(more_plan)}
    _install_successful_history(monkeypatch, histories)

    second_input, next_summary = engineering_issue.prepare_next_engineering_issue_input(
        run_store, "issue-round-1"
    )
    second_value = json.loads(second_input)
    test_bytes = source_bytes["tests/unit/test_engine.py"]
    assert second_value["round"] == 2
    assert (
        second_value["source_context"]["tests/unit/test_engine.py"]
        == test_bytes.decode()
    )
    assert (
        second_value["source_sha256"]["tests/unit/test_engine.py"]
        == hashlib.sha256(test_bytes).hexdigest()
    )
    assert second_value["prior_research"][0]["run_id"] == "issue-round-1"
    assert (
        second_value["prior_research"][0]["output"]["evidence"][0]["start_line"]
        == 2
    )
    first_result = engineering_issue._load_research_result(run_store, "issue-round-1")
    assert json.loads(first_result[2])["evidence"][0]["start_line"] == 99
    assert second_value["prior_research"][0]["output_sha256"] == first_result[3]
    assert next_summary["provider_attempts_used"] == 2

    ready_plan = _ready_plan()
    ready_plan["acceptance_criteria"] = [
        "The candidate must pass the selected AST syntax Oracle in this stage.",
        "The existing pytest suite must pass in the later exact-head Draft PR CI.",
        "Ruff must pass in the later exact-head Draft PR CI.",
    ]
    ready_plan["evidence"][0]["start_line"] = 99
    ready_plan["evidence"][0]["end_line"] = 99
    second_state_path = _successful_run(
        run_store,
        "issue-round-2",
        second_input,
        ready_plan,
    )
    histories[second_state_path] = json.dumps(ready_plan)
    _install_successful_history(monkeypatch, histories)
    assignment, policy_text, summary = engineering_issue.prepare_bounded_job_inputs(
        run_store,
        "issue-round-2",
        now_ns=1_000_000_000,
    )
    job_input = json.loads(assignment)
    policy = json.loads(policy_text)

    assert job_input["allowed_paths"] == {
        "read": ["src/pkg/engine.py", "tests/unit/test_engine.py"],
        "write": ["src/pkg/engine.py"],
    }
    assert job_input["investigation"]["evidence"][0]["start_line"] == 2
    assert job_input["investigation"]["evidence"][0]["end_line"] == 2
    second_result = engineering_issue._load_research_result(
        run_store, "issue-round-2"
    )
    assert json.loads(second_result[2])["evidence"][0]["start_line"] == 99
    assert (
        job_input["source_context"]["tests/unit/test_engine.py"] == test_bytes.decode()
    )
    assert set(job_input["source_sha256"]) == {
        "src/pkg/engine.py",
        "tests/unit/test_engine.py",
    }
    assert policy["base_revision"] == revision
    assert policy["read_paths"] == ["src/pkg/engine.py", "tests/unit/test_engine.py"]
    assert policy["write_paths"] == ["src/pkg/engine.py"]
    assert policy["commands"][0][:4] == [
        "/usr/bin/python3",
        "-S",
        "-B",
        "-c",
    ]
    assert "ast.parse" in policy["commands"][0][4]
    assert summary["base_revision"] == revision
    assert summary["oracle_profile"] == _ORACLE_PROFILE
    assert summary["candidate_selection"]["limitation"]
    request_value = json.loads(job_input["request"])
    assert request_value["candidate_search"] == summary["candidate_selection"]
    assert job_input["acceptance_criteria"] == ready_plan["acceptance_criteria"]
    verification_stage = request_value["verification_stage"]
    assert verification_stage["oracle_profile"] == summary["oracle_profile"]
    assert verification_stage["oracle_policy_sha256"] == summary[
        "oracle_policy_sha256"
    ]
    assert verification_stage["required_now_commands"] == policy["commands"]
    assert verification_stage["deferred_checks"] == [
        {
            "acceptance_criterion": ready_plan["acceptance_criteria"][1],
            "checks": ["pytest"],
            "gate": "exact-head GitHub CI for the resulting Draft PR",
            "status": "pending",
        },
        {
            "acceptance_criterion": ready_plan["acceptance_criteria"][2],
            "checks": ["Ruff"],
            "gate": "exact-head GitHub CI for the resulting Draft PR",
            "status": "pending",
        },
    ]
    assert (
        validate_local_execution_scope(
            assignment,
            repository,
            LocalExecutionPolicy.model_validate_json(policy_text),
        )
        == repository
    )
    bounded_workflow = next(
        item
        for item in load_workflows(_ROOT / "workflows")
        if item.definition.id == "bounded-change-review"
    )
    assert (
        build_workflow_run_manifest(
            "prepared-bounded-job",
            assignment,
            bounded_workflow,
            tuple(load_employees(_ROOT / "employees")),
        ).workflow_id
        == "bounded-change-review"
    )

    metadata_requests: list[object] = []

    def fake_repository_metadata(request):
        metadata_requests.append(request)
        return GitHubRawResponse(
            200,
            json.dumps(
                {
                    "id": 828001,
                    "full_name": _REPOSITORY,
                    "default_branch": "main",
                }
            ).encode("utf-8"),
        )

    issue_assignment, issue_policy, issue_summary = (
        engineering_issue.prepare_bounded_job_inputs_for_issue(
            _ISSUE_URL,
            run_store,
            now_ns=1_000_000_000,
            github_transport=fake_repository_metadata,
        )
    )
    issue_value = json.loads(issue_assignment)
    assert len(metadata_requests) == 1
    assert metadata_requests[0].method == "GET"
    assert metadata_requests[0].path == "/repos/example/project"
    assert issue_value["repository"] == {
        **job_input["repository"],
        "base_ref": "main",
        "full_name": _REPOSITORY,
        "id": 828001,
    }
    issue_request = json.loads(issue_value["request"])
    assert issue_request["issue"]["url"] == _ISSUE_URL
    assert issue_value["investigation"]["research_run_id"] == "issue-round-2"
    assert issue_policy == policy_text
    assert issue_summary["research_run_id"] == "issue-round-2"
    assert issue_summary["issue_url"] == _ISSUE_URL
    assert issue_summary["github_repository_id"] == 828001


@pytest.mark.parametrize("invalid_evidence", ["missing", "other_path", "ambiguous"])
def test_bounded_job_input_stops_when_fixed_source_cannot_resolve_quote(
    tmp_path: Path,
    fixed_repository: tuple[Path, str, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
    invalid_evidence: str,
) -> None:
    repository, revision, _ = fixed_repository
    if invalid_evidence == "ambiguous":
        source_path = repository / "src/pkg/engine.py"
        source_path.write_bytes(
            b"def calculate(values):\n"
            b"    return sum(values)\n"
            b"    return sum(values)\n"
        )
        _git(repository, "add", "src/pkg/engine.py")
        _git(repository, "commit", "-qm", "duplicate evidence quote")
        revision = _git(repository, "rev-parse", "HEAD")

    run_input, _summary, _requests = _prepare(
        repository, revision, initial_files=2, max_files=2
    )
    plan = _ready_plan()
    evidence = plan["evidence"][0]
    evidence["start_line"] = 99
    evidence["end_line"] = 99
    if invalid_evidence == "missing":
        evidence["quote"] = "not present in the fixed Git source"
    elif invalid_evidence == "other_path":
        evidence["path"] = "tests/unit/test_engine.py"
    else:
        evidence["quote"] = "return sum(values)"

    run_store = tmp_path / "runs"
    state_path = _successful_run(run_store, "unverifiable-quote", run_input, plan)
    _install_successful_history(monkeypatch, {state_path: json.dumps(plan)})

    with pytest.raises(engineering_issue.EngineeringIssueError) as caught:
        engineering_issue.prepare_bounded_job_inputs(
            run_store, "unverifiable-quote", now_ns=1_000_000_000
        )
    assert caught.value.classification == "evidence_quote_mismatch"


def test_bounded_job_input_keeps_line_range_that_disambiguates_duplicate_quote(
    tmp_path: Path,
    fixed_repository: tuple[Path, str, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _revision, _ = fixed_repository
    source_path = repository / "src/pkg/engine.py"
    source_path.write_bytes(
        b"def calculate(values):\n"
        b"    return sum(values)\n"
        b"    return sum(values)\n"
    )
    _git(repository, "add", "src/pkg/engine.py")
    _git(repository, "commit", "-qm", "duplicate evidence quote")
    revision = _git(repository, "rev-parse", "HEAD")
    run_input, _summary, _requests = _prepare(
        repository, revision, initial_files=2, max_files=2
    )
    plan = _ready_plan()
    plan["evidence"][0]["start_line"] = 3
    plan["evidence"][0]["end_line"] = 3
    run_store = tmp_path / "runs"
    state_path = _successful_run(run_store, "selected-duplicate-quote", run_input, plan)
    _install_successful_history(monkeypatch, {state_path: json.dumps(plan)})

    assignment, _policy, _summary = engineering_issue.prepare_bounded_job_inputs(
        run_store, "selected-duplicate-quote", now_ns=1_000_000_000
    )
    evidence = json.loads(assignment)["investigation"]["evidence"][0]
    assert evidence["start_line"] == 3
    assert evidence["end_line"] == 3


def test_unlisted_additional_path_and_changed_base_stop_without_preparation(
    tmp_path: Path,
    fixed_repository: tuple[Path, str, dict[str, bytes]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, revision, _ = fixed_repository
    first_input, _summary, _requests = _prepare(repository, revision, initial_files=1)
    malicious_plan = {
        "outcome": "needs_more_evidence",
        "cause_hypotheses": [],
        "evidence": [],
        "read_paths": [],
        "additional_read_paths": ["secrets/token.py"],
        "write_paths": [],
        "test_paths": [],
        "acceptance_criteria": [],
    }
    run_store = tmp_path / "runs"
    state_path = _successful_run(
        run_store, "untrusted-research", first_input, malicious_plan
    )
    _install_successful_history(monkeypatch, {state_path: json.dumps(malicious_plan)})
    with pytest.raises(engineering_issue.EngineeringIssueError) as caught:
        engineering_issue.prepare_next_engineering_issue_input(
            run_store, "untrusted-research"
        )
    assert caught.value.classification == "additional_source_outside_approved_scope"

    _git(repository, "config", "user.name", "Offline Test")
    (repository / "src/pkg/unused.py").write_text(
        "def changed():\n    return True\n", encoding="utf-8"
    )
    _git(repository, "add", "src/pkg/unused.py")
    _git(repository, "commit", "-qm", "move head")
    with pytest.raises(engineering_issue.EngineeringIssueError) as caught:
        engineering_issue.prepare_next_engineering_issue_input(
            run_store, "untrusted-research"
        )
    assert caught.value.classification == "repository_base_mismatch"


def test_private_outputs_require_existing_private_directory_and_use_0600(
    tmp_path: Path,
    fixed_repository: tuple[Path, str, dict[str, bytes]],
) -> None:
    repository, _revision, _ = fixed_repository
    output = tmp_path / "private-output"
    output.mkdir(mode=0o700)
    sizes = engineering_issue.write_private_outputs(
        output,
        {"prepared.json": "{}"},
        repository_path=repository,
    )

    assert sizes == {"prepared.json": 2}
    assert (output / "prepared.json").stat().st_mode & 0o777 == 0o600
    output.chmod(0o750)
    with pytest.raises(engineering_issue.EngineeringIssueError) as caught:
        engineering_issue.write_private_outputs(
            output,
            {"other.json": "{}"},
            repository_path=repository,
        )
    assert caught.value.classification == "output_directory_not_private"
