"""Offline proof for Run-bound, disposable local change verification."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import time
import uuid
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from ai_office.cli import app
from ai_office.definitions.employee import load_employees
from ai_office.definitions.workflow import load_workflow_file
from ai_office.engine.artifact import list_run_artifacts
from ai_office.engine.local_execution import (
    LocalExecutionError,
    LocalExecutionLimits,
    LocalExecutionPolicy,
    _git_identity,
    _git_status,
    _redact,
    build_local_review_input,
    execute_local_change,
    issue_local_execution_authorization,
)
from ai_office.execution_target import DIRECT_OPENAI_EXECUTION_TARGET
from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
    execute_openai_model_invocation,
)
from tests._execution_evidence_test_support import create_test_execution_evidence
from tests.test_artifact import _commit_success, _route, _start_fixture, _success_body

_CHANGE_TYPE = "application/vnd.ai-office.file-change-set+json"
_SAMPLE_ROOT = Path(__file__).parents[1]
_CLI_RUNNER = CliRunner()


def test_redaction_covers_common_secret_assignments() -> None:
    value = _redact(
        "API_KEY=first AWS_SECRET_ACCESS_KEY:second password:third TOKEN=fourth"
    )
    for secret in ("first", "second", "third", "fourth"):
        assert secret not in value
    assert value.count("[REDACTED]") == 4


def test_cli_requires_and_persists_explicit_local_effect_approval(
    tmp_path: Path,
) -> None:
    repository, revision, source = _temporary_repo(tmp_path)
    store, artifact = _candidate(tmp_path, revision, source)
    policy = _policy(
        revision,
        commands=(("/usr/bin/python3", "-B", "-c", "pass"),),
    )
    policy_path = tmp_path / "local-effect-policy.json"
    policy_path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
    arguments = [
        "workflows",
        "local-effect-authorize",
        artifact.run_id,
        artifact.artifact_id,
        "--policy",
        str(policy_path),
        "--workspace",
        str(repository),
        "--approved-by",
        "offline-proof-operator",
        "--approval-id",
        "cli-offline-grant",
        "--run-store",
        str(store.root),
    ]

    denied = _CLI_RUNNER.invoke(app, arguments)
    assert denied.exit_code == 2
    assert "--approve-local-effect is required" in denied.output
    assert not list(store.root.glob("*.local-effect-authorization.*.json"))

    approved = _CLI_RUNNER.invoke(app, [*arguments, "--approve-local-effect"])
    assert approved.exit_code == 0, approved.output
    result = json.loads(approved.stdout)
    assert result["artifact_id"] == artifact.artifact_id
    assert result["approved_by"] == "offline-proof-operator"
    assert len(list(store.root.glob("*.local-effect-authorization.*.json"))) == 1


def test_run_store_inside_target_repository_is_rejected(tmp_path: Path) -> None:
    repository, revision, source = _temporary_repo(tmp_path)
    store, artifact = _candidate(
        tmp_path,
        revision,
        source,
        store_root=repository / "run-store",
    )
    policy = _policy(
        revision,
        commands=(("/usr/bin/python3", "-B", "-c", "pass"),),
    )

    with pytest.raises(LocalExecutionError):
        issue_local_execution_authorization(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            workspace=repository,
            policy=policy,
            approved_by="offline-proof-operator",
            approval_id="in-repository-store",
        )
    assert not list(store.root.glob("*.local-effect-authorization.*.json"))


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _temporary_repo(root: Path) -> tuple[Path, str, bytes]:
    repository = root / "repository"
    repository.mkdir()
    source = b"def answer():\n    return 1 + 1\n"
    (repository / "calculator.py").write_bytes(source)
    (repository / ".gitattributes").write_text(
        "calculator.py filter=offlineprobe\n", encoding="utf-8"
    )
    (repository / "test_calculator.py").write_text(
        "import unittest\n\n"
        "from calculator import answer\n\n\n"
        "class CalculatorTest(unittest.TestCase):\n"
        "    def test_answer(self):\n"
        "        self.assertEqual(answer(), 2)\n",
        encoding="utf-8",
    )
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Offline Proof")
    _git(repository, "config", "user.email", "offline-proof@example.invalid")
    _git(repository, "add", ".gitattributes", "calculator.py", "test_calculator.py")
    _git(repository, "commit", "--quiet", "-m", "baseline")
    revision = _git(repository, "rev-parse", "HEAD")
    return repository, revision, source


def _change_set(
    revision: str,
    source: bytes,
    *,
    path: str = "calculator.py",
    replacement: bytes = b"def answer():\n    return 2\n",
    extra: dict[str, object] | None = None,
) -> str:
    change: dict[str, object] = {
        "before_sha256": sha256(source).hexdigest(),
        "content_base64": base64.b64encode(replacement).decode("ascii"),
        "path": path,
    }
    if extra:
        change.update(extra)
    return json.dumps(
        {
            "base_revision": revision,
            "changes": [change],
            "schema_version": "file-change-set.v1",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _candidate(
    root: Path,
    revision: str,
    source: bytes,
    *,
    run_id: str = "run-local-effect",
    change_set: str | None = None,
    store_root: Path | None = None,
):
    store_root = store_root or root / f"runs-{run_id}"
    source_context = {
        "calculator.py": source.decode("utf-8"),
        "test_calculator.py": (root / "repository" / "test_calculator.py").read_text(
            encoding="utf-8"
        ),
    }
    run_input = json.dumps(
        {
            "acceptance_criteria": [
                "answer() returns 2",
                "the supplied unittest suite passes",
            ],
            "allowed_paths": {
                "read": ["calculator.py", "test_calculator.py"],
                "write": ["calculator.py"],
            },
            "repository": {
                "base_revision": revision,
                "identity": "temporary offline-proof repository",
            },
            "request": "Change answer() to return 2 without changing the test.",
            "source_context": source_context,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    captured_requests: list[object] = []
    workflow, evidence, calls, result = _start_fixture(
        store_root,
        run_id=run_id,
        artifact_content_type=_CHANGE_TYPE,
        output=(_change_set(revision, source) if change_set is None else change_set),
        run_input=run_input,
        captured_requests=captured_requests,
    )
    _commit_success(workflow, evidence, result)
    decision = _route(workflow, evidence)
    assert decision.decision == "prepare_next_step"
    artifacts = list_run_artifacts(evidence.run.store, run_id)
    assert len(artifacts) == 1
    assert calls == [1]  # the transport is the test's in-memory fake
    assert len(captured_requests) == 1
    provider_payload = json.loads(captured_requests[0].body)
    assert json.loads(provider_payload["input"])["run_input"] == run_input
    return evidence.run.store, artifacts[0]


def _policy(
    revision: str,
    *,
    commands: tuple[tuple[str, ...], ...],
    wall_timeout_seconds: int = 15,
    max_output_bytes: int = 16_384,
) -> LocalExecutionPolicy:
    return LocalExecutionPolicy(
        base_revision=revision,
        read_paths=("calculator.py", "test_calculator.py"),
        write_paths=("calculator.py",),
        commands=commands,
        expires_at_ns=time.time_ns() + 60_000_000_000,
        limits=LocalExecutionLimits(
            wall_timeout_seconds=wall_timeout_seconds,
            cpu_seconds=10,
            memory_bytes=268_435_456,
            workspace_bytes=16_777_216,
            max_file_bytes=1_048_576,
            max_output_bytes=max_output_bytes,
            max_processes=256,
            max_open_files=64,
        ),
    )


def test_fake_provider_patch_is_run_bound_and_only_verified_in_bwrap_tmpfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, revision, source = _temporary_repo(tmp_path)
    os.chmod(repository / "calculator.py", 0o600)
    test_source_mode = (repository / "test_calculator.py").stat().st_mode & 0o777
    store, artifact = _candidate(tmp_path, revision, source)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    isolation_probe = (
        "import pathlib,socket,sys\n"
        "assert not pathlib.Path('/home').exists()\n"
        "assert not pathlib.Path('/root').exists()\n"
        "assert not pathlib.Path('/etc/passwd').exists()\n"
        f"sock=socket.socket(); sock.settimeout(0.3)\n"
        f"try:\n    sock.connect(('127.0.0.1',{port}))\n"
        "except OSError:\n    print('host-loopback-unreachable')\n"
        "else:\n    sys.exit(41)\n"
        "finally:\n    sock.close()\n"
    )
    memory_probe = (
        "try:\n    bytearray(1 << 30)\n"
        "except MemoryError:\n    print('memory-bound')\n"
        "else:\n    raise SystemExit(42)\n"
    )
    secret_probe = (
        "import binascii; print(binascii.unhexlify("
        "'4150495f4b45593d6f66666c696e652d746573742d736563726574').decode())"
    )
    mode_probe = (
        "import os; assert (os.stat('calculator.py').st_mode&0o777,"
        "os.stat('test_calculator.py').st_mode&0o777)==(0o600,"
        f"{test_source_mode})"
    )
    process_probe = (
        "import resource; assert resource.getrlimit(resource.RLIMIT_NPROC)=="
        "(256,256); print('process-limit-enforced')"
    )
    commands = (
        (
            "/usr/bin/python3",
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            "/workspace",
            "-p",
            "test_*.py",
        ),
        (
            "/usr/bin/python3",
            "-B",
            "-c",
            "import ast,pathlib; ast.parse(pathlib.Path('calculator.py').read_text())",
        ),
        (
            "/usr/bin/python3",
            "-B",
            "-c",
            mode_probe,
        ),
        ("/usr/bin/python3", "-B", "-c", process_probe),
        ("/usr/bin/python3", "-B", "-c", isolation_probe),
        ("/usr/bin/python3", "-B", "-c", memory_probe),
        ("/usr/bin/python3", "-B", "-c", secret_probe),
    )
    policy = _policy(revision, commands=commands)
    authorization = issue_local_execution_authorization(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        workspace=repository,
        policy=policy,
        approved_by="offline-proof-operator",
        approval_id="offline-proof-grant",
    )

    evidence = execute_local_change(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        authorization_id=authorization.authorization_id,
        workspace=repository,
    )
    listener.close()
    assert evidence["status"] == "verified", evidence
    assert evidence["run_id"] == artifact.run_id
    assert evidence["artifact_sha256"] == artifact.content_sha256
    assert evidence["base_revision"] == revision
    assert evidence["before_tree_sha256"] != evidence["after_tree_sha256"]
    assert "return 1 + 1" in evidence["diff"]
    assert "return 2" in evidence["diff"]
    assert (
        evidence["network_namespace"] != Path("/proc/self/ns/net").readlink().as_posix()
    )
    command_results = evidence["commands"]
    assert [item["argv"] for item in command_results] == [
        list(argv) for argv in commands
    ]
    assert all(
        item["status"] == "completed" and item["exit_code"] == 0
        for item in command_results
    )
    assert "process-limit-enforced" in command_results[3]["stdout"]
    assert "host-loopback-unreachable" in command_results[4]["stdout"]
    assert "memory-bound" in command_results[5]["stdout"]
    assert "[REDACTED]" in command_results[6]["stdout"]
    assert "offline-test-secret" not in json.dumps(evidence)
    assert (repository / "calculator.py").read_bytes() == source
    assert (repository / "calculator.py").stat().st_mode & 0o777 == 0o600
    assert (
        repository / "test_calculator.py"
    ).stat().st_mode & 0o777 == test_source_mode
    assert _git(repository, "status", "--porcelain") == ""

    def replay_must_not_start(*_args: object, **_kwargs: object) -> None:
        pytest.fail("one-use authorization attempted a second sandbox execution")

    import ai_office.engine.local_execution as local_execution

    monkeypatch.setattr(local_execution, "_run_sandbox", replay_must_not_start)
    with pytest.raises(LocalExecutionError):
        execute_local_change(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            authorization_id=authorization.authorization_id,
            workspace=repository,
        )


@pytest.mark.parametrize(
    ("path", "extra"),
    [
        ("../outside.py", None),
        ("test_calculator.py", None),
        ("calculator.py", {"operation": "delete"}),
        ("calculator.py", {"mode": 0o777}),
    ],
)
def test_invalid_change_set_is_rejected_before_any_attempt_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    extra: dict[str, object] | None,
) -> None:
    repository, revision, source = _temporary_repo(tmp_path)
    candidate = _change_set(revision, source, path=path, extra=extra)
    store, artifact = _candidate(
        tmp_path,
        revision,
        source,
        run_id=f"run-invalid-{abs(hash((path, repr(extra))))}",
        change_set=candidate,
    )
    policy = _policy(
        revision,
        commands=(("/usr/bin/python3", "-B", "-c", "pass"),),
    )
    authorization = issue_local_execution_authorization(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        workspace=repository,
        policy=policy,
        approved_by="offline-proof-operator",
        approval_id="invalid-change-grant",
    )

    def runner_must_not_start(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid patch reached bwrap")

    import ai_office.engine.local_execution as local_execution

    monkeypatch.setattr(local_execution, "_run_sandbox", runner_must_not_start)
    with pytest.raises(LocalExecutionError):
        execute_local_change(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            authorization_id=authorization.authorization_id,
            workspace=repository,
        )
    claim = store.root / (
        f"{artifact.run_id}.local-effect-attempt.{authorization.authorization_id}.json"
    )
    assert not claim.exists()
    assert (repository / "calculator.py").read_bytes() == source


def test_expired_grant_is_rejected_before_claim(tmp_path: Path) -> None:
    repository, revision, source = _temporary_repo(tmp_path)
    store, artifact = _candidate(tmp_path, revision, source)
    expired = _policy(
        revision,
        commands=(("/usr/bin/python3", "-B", "-c", "raise SystemExit(99)"),),
    ).model_copy(update={"expires_at_ns": time.time_ns() - 1})
    authorization = issue_local_execution_authorization(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        workspace=repository,
        policy=expired,
        approved_by="offline-proof-operator",
        approval_id="expired-grant",
        now_ns=expired.expires_at_ns - 10,
    )

    with pytest.raises(LocalExecutionError):
        execute_local_change(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            authorization_id=authorization.authorization_id,
            workspace=repository,
        )
    assert not (
        store.root
        / f"{artifact.run_id}.local-effect-attempt.{authorization.authorization_id}"
        ".json"
    ).exists()


def test_git_preflight_contains_repository_filters_and_supports_worktrees(
    tmp_path: Path,
) -> None:
    repository, revision, _source = _temporary_repo(tmp_path)
    linked_worktree = tmp_path / "linked-worktree"
    _git(
        repository,
        "worktree",
        "add",
        "--quiet",
        "--detach",
        str(linked_worktree),
        revision,
    )
    marker = Path("/tmp") / f"ai-office-git-filter-{uuid.uuid4().hex}"
    assert not marker.exists()
    _git(
        repository,
        "config",
        "filter.offlineprobe.clean",
        " && ".join(
            (
                "/usr/bin/grep -q '^Max address space.*536870912.*536870912.*bytes' "
                "/proc/self/limits",
                "/usr/bin/grep -q '^Max processes.*128.*128.*processes' "
                "/proc/self/limits",
                f"/usr/bin/touch {marker}",
                "/usr/bin/cat",
                "/usr/bin/printf filtered",
            )
        ),
    )
    _git(repository, "config", "filter.offlineprobe.required", "true")
    future = time.time() + 5
    os.utime(repository / "calculator.py", (future, future))
    os.utime(linked_worktree / "calculator.py", (future, future))

    assert _git_identity(repository) == revision
    assert "calculator.py" in _git_status(repository)
    assert _git_identity(linked_worktree) == revision
    assert "calculator.py" in _git_status(linked_worktree)
    assert not marker.exists()


def test_symlink_source_is_rejected_before_attempt_claim(tmp_path: Path) -> None:
    repository, _revision, _source = _temporary_repo(tmp_path)
    calculator = repository / "calculator.py"
    calculator.unlink()
    calculator.symlink_to("test_calculator.py")
    _git(repository, "add", "calculator.py")
    _git(repository, "commit", "--quiet", "-m", "track source symlink")
    revision = _git(repository, "rev-parse", "HEAD")
    store, artifact = _candidate(
        tmp_path,
        revision,
        b"test_calculator.py",
        change_set=_change_set(
            revision, b"test_calculator.py", replacement=b"safe replacement\n"
        ),
    )
    policy = _policy(
        revision,
        commands=(("/usr/bin/python3", "-B", "-c", "pass"),),
    )
    authorization = issue_local_execution_authorization(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        workspace=repository,
        policy=policy,
        approved_by="offline-proof-operator",
        approval_id="symlink-grant",
    )

    with pytest.raises(LocalExecutionError):
        execute_local_change(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            authorization_id=authorization.authorization_id,
            workspace=repository,
        )
    claim = store.root / (
        f"{artifact.run_id}.local-effect-attempt.{authorization.authorization_id}.json"
    )
    assert not claim.exists()


def test_timeout_is_durable_and_not_replayed(tmp_path: Path) -> None:
    repository, revision, source = _temporary_repo(tmp_path)
    store, artifact = _candidate(tmp_path, revision, source)
    policy = _policy(
        revision,
        commands=(("/usr/bin/python3", "-B", "-c", "import time; time.sleep(30)"),),
        wall_timeout_seconds=2,
    )
    authorization = issue_local_execution_authorization(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        workspace=repository,
        policy=policy,
        approved_by="offline-proof-operator",
        approval_id="timeout-grant",
    )
    evidence = execute_local_change(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        authorization_id=authorization.authorization_id,
        workspace=repository,
    )

    assert evidence["status"] == "timeout", evidence
    assert evidence["commands"][0]["argv"] == list(policy.commands[0])
    assert evidence["commands"][0]["status"] == "timeout"
    with pytest.raises(LocalExecutionError):
        execute_local_change(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            authorization_id=authorization.authorization_id,
            workspace=repository,
        )


def test_output_limit_is_durable_and_redacted(tmp_path: Path) -> None:
    repository, revision, source = _temporary_repo(tmp_path)
    store, artifact = _candidate(tmp_path, revision, source)
    policy = _policy(
        revision,
        commands=(("/usr/bin/python3", "-B", "-c", "print('x' * 100_000)"),),
        max_output_bytes=32,
    )
    authorization = issue_local_execution_authorization(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        workspace=repository,
        policy=policy,
        approved_by="offline-proof-operator",
        approval_id="output-limit-grant",
    )

    evidence = execute_local_change(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        authorization_id=authorization.authorization_id,
        workspace=repository,
    )

    assert evidence["status"] == "output_limit"
    assert evidence["commands"][0]["status"] == "output_limit"
    assert len(evidence["commands"][0]["stdout"].encode("utf-8")) <= 32
    with pytest.raises(LocalExecutionError):
        execute_local_change(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            authorization_id=authorization.authorization_id,
            workspace=repository,
        )


def test_configured_independent_reviewers_receive_identical_exact_evidence(
    tmp_path: Path,
) -> None:
    repository, revision, source = _temporary_repo(tmp_path)
    store, artifact = _candidate(tmp_path, revision, source)
    policy = _policy(
        revision,
        commands=(
            (
                "/usr/bin/python3",
                "-B",
                "-m",
                "unittest",
                "discover",
                "-s",
                "/workspace",
            ),
        ),
    )
    authorization = issue_local_execution_authorization(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        workspace=repository,
        policy=policy,
        approved_by="offline-proof-operator",
        approval_id="review-grant",
    )
    local_evidence = execute_local_change(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        authorization_id=authorization.authorization_id,
        workspace=repository,
    )
    review_input = build_local_review_input(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        authorization_id=authorization.authorization_id,
    )
    packet = json.loads(review_input)
    assert packet["applied_candidate"]["artifact_sha256"] == artifact.content_sha256
    assert packet["local_execution_evidence"] == local_evidence
    with pytest.raises(LocalExecutionError):
        build_local_review_input(
            store,
            run_id=artifact.run_id,
            artifact_id="0" * 64,
            authorization_id=authorization.authorization_id,
        )
    run_input = json.loads(packet["run_input"])
    assert run_input["repository"]["base_revision"] == revision
    assert run_input["allowed_paths"] == {
        "read": ["calculator.py", "test_calculator.py"],
        "write": ["calculator.py"],
    }
    assert run_input["source_context"]["calculator.py"] == source.decode("utf-8")

    sample = load_workflow_file(_SAMPLE_ROOT / "workflows/bounded-change-review.yaml")
    workflow = sample.definition
    employees = tuple(
        loaded.definition for loaded in load_employees(_SAMPLE_ROOT / "employees")
    )
    employee_by_id = {employee.id: employee for employee in employees}
    assert workflow.bounded_review is not None
    reviewer_ids = workflow.bounded_review.review_steps
    reviewer_employees = [
        next(step.employee for step in workflow.steps if step.id == step_id)
        for step_id in reviewer_ids
    ]
    assert len(reviewer_ids) == 2
    assert len(set(reviewer_employees)) == len(reviewer_employees)

    seen_runs: list[tuple[str, dict[str, object]]] = []
    for step_id in reviewer_ids:
        step = next(item for item in workflow.steps if item.id == step_id)
        employee = employee_by_id[step.employee]
        run_id = f"review-{step_id}"
        execution = create_test_execution_evidence(
            tmp_path / f"runs-{run_id}",
            run_id=run_id,
            workflow=workflow,
            employees=employees,
            request=ModelInvocationRequest(
                model=step.model,
                system_instructions=employee.instructions,
                task_instructions=step.instructions,
                allowed_tools=(),
            ),
            resolved_tools=(),
            step_id=step.id,
            target=DIRECT_OPENAI_EXECUTION_TARGET,
            run_input=review_input,
        )
        response = OpenAIResponsesRawHttpResponse(
            status_code=200,
            reason="synthetic",
            headers=(("content-type", "application/json"),),
            body=_success_body("Verdict: PASS"),
        )
        seen: list[object] = []

        def fake_transport(request: object) -> OpenAIResponsesRawHttpResponse:
            seen.append(request)
            return response

        result = execute_openai_model_invocation(
            execution.request,
            (),
            OpenAIApiKey(value=SecretStr("offline-review-test-key")),
            execution.approval,
            transport=fake_transport,
            execution_evidence=execution.context,
        )
        assert result.text == "Verdict: PASS"
        payload = json.loads(seen[0].body)
        rendered_input = json.loads(payload["input"])
        assert rendered_input["run_input"] == review_input
        seen_runs.append((run_id, rendered_input))

    assert len({run_id for run_id, _ in seen_runs}) == 2
    assert all(item["run_input"] == review_input for _, item in seen_runs)
