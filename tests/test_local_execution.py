"""Offline proof for Run-bound, disposable local change verification."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr
from typer.testing import CliRunner

from ai_office.cli import app
from ai_office.definitions.employee import load_employees
from ai_office.definitions.workflow import load_workflow_file
from ai_office.engine.artifact import list_run_artifacts
from ai_office.engine.local_execution import (
    FILE_CHANGE_SET_CONTENT_TYPE,
    LocalExecutionError,
    LocalExecutionLimits,
    LocalExecutionPolicy,
    _capture_bounded_output,
    _git_identity,
    _git_status,
    _redact,
    execute_local_change,
    issue_local_execution_authorization,
)
from ai_office.engine.publication_intent import (
    PUBLICATION_INTENT_EFFECTS,
    PublicationIntentError,
    build_publication_intent_handoff,
    verify_publication_intent_handoff,
)
from ai_office.engine.workflow_bounded_job import (
    BoundedJobActionSlot,
    bounded_job_execution_view,
    bounded_review_action_plan,
    issue_bounded_workflow_job_scope,
    run_bounded_document_job,
)
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    build_workflow_run_manifest,
    persist_workflow_run_manifest,
)
from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    execution_target_fingerprint,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
)
from tests._offline_git_delivery import (
    FakeGitHubDeliveryAPI,
    OfflineDeliveryError,
    SimulatedEffectCrash,
    _optional_git,
    _repository_identity,
    _validate_attributes,
    create_fake_bare_remote,
    fake_github_credential_for_test,
    issue_test_delivery_grant,
    revoke_test_delivery_grant,
    run_offline_delivery,
)
from tests.test_artifact import _commit_success, _route, _start_fixture

_CHANGE_TYPE = "application/vnd.ai-office.file-change-set+json"
_SAMPLE_ROOT = Path(__file__).parents[1]
_CLI_RUNNER = CliRunner()


def _sandbox_namespaces_available() -> bool:
    bwrap = Path("/usr/bin/bwrap")
    prlimit = Path("/usr/bin/prlimit")
    if any(
        not executable.is_file() or not os.access(executable, os.X_OK)
        for executable in (bwrap, prlimit)
    ):
        return False
    argv = [
        str(bwrap),
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-net",
        "--new-session",
        "--die-with-parent",
        "--disable-userns",
        "--assert-userns-disabled",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--clearenv",
        "--",
        "/usr/bin/true",
    ]
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={},
            shell=False,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@pytest.fixture(scope="module")
def require_local_sandbox() -> None:
    if not _sandbox_namespaces_available():
        pytest.skip("host cannot enforce required bwrap namespaces; no fallback")


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
    if not _sandbox_namespaces_available():
        assert approved.exit_code == 2
        assert "local-effect approval could not be persisted" in approved.output
        assert not list(store.root.glob("*.local-effect-authorization.*.json"))
        return
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


@pytest.mark.parametrize(
    ("mismatch", "expected_classification"),
    [
        ("write_scope", "run_input_scope"),
        ("base_revision", "run_input_scope"),
        ("repository_path", "run_input_scope"),
        ("source_context", "run_input_source_context"),
    ],
)
def test_local_authority_cannot_exceed_fixed_run_input(
    tmp_path: Path, mismatch: str, expected_classification: str
) -> None:
    repository, revision, source = _temporary_repo(tmp_path)
    read_paths = ["calculator.py", "test_calculator.py"]
    write_paths = ["calculator.py"]
    run_revision = revision
    run_repository_path = str(repository)
    context = {
        "calculator.py": source.decode("utf-8"),
        "test_calculator.py": (repository / "test_calculator.py").read_text(
            encoding="utf-8"
        ),
    }
    policy_revision = revision
    policy_read_paths = ("calculator.py", "test_calculator.py")
    policy_write_paths = ("calculator.py",)
    if mismatch == "write_scope":
        policy_write_paths = ("calculator.py", "test_calculator.py")
    elif mismatch == "base_revision":
        policy_revision = "f" * 40
    elif mismatch == "repository_path":
        run_repository_path = str(tmp_path / "another-repository")
    elif mismatch == "source_context":
        context["calculator.py"] = "def answer():\n    return 99\n"
    run_input = _run_input(
        tmp_path,
        run_revision,
        source,
        repository_path=run_repository_path,
        read_paths=read_paths,
        write_paths=write_paths,
        source_context=context,
    )
    store, artifact = _candidate(
        tmp_path,
        revision,
        source,
        run_id=f"run-scope-{mismatch}",
        run_input_override=run_input,
    )
    policy = _policy(
        policy_revision,
        commands=(("/usr/bin/python3", "-B", "-c", "pass"),),
    ).model_copy(
        update={"read_paths": policy_read_paths, "write_paths": policy_write_paths}
    )

    with pytest.raises(LocalExecutionError) as error:
        issue_local_execution_authorization(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            workspace=repository,
            policy=policy,
            approved_by="offline-proof-operator",
            approval_id=f"mismatch-{mismatch}",
        )

    assert error.value.classification == expected_classification
    assert not list(store.root.glob("*.local-effect-authorization.*.json"))


def test_expiry_during_preflight_stops_before_attempt_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ai_office.engine.local_execution as local_execution_module

    repository, revision, source = _temporary_repo(tmp_path)
    store, artifact = _candidate(tmp_path, revision, source)
    policy = _policy(
        revision,
        commands=(("/usr/bin/python3", "-B", "-c", "pass"),),
    )
    monkeypatch.setattr(local_execution_module, "_git_identity", lambda _root: revision)
    monkeypatch.setattr(local_execution_module, "_git_status", lambda _root: "")
    authorization = issue_local_execution_authorization(
        store,
        run_id=artifact.run_id,
        artifact_id=artifact.artifact_id,
        workspace=repository,
        policy=policy,
        approved_by="offline-proof-operator",
        approval_id="expiry-during-preflight",
    )
    clock = [time.time_ns()]
    read_sources = local_execution_module._read_sources

    def expire_after_source_read(root: Path, selected_policy: LocalExecutionPolicy):
        result = read_sources(root, selected_policy)
        clock[0] = policy.expires_at_ns
        return result

    monkeypatch.setattr(local_execution_module.time, "time_ns", lambda: clock[0])
    monkeypatch.setattr(
        local_execution_module, "_read_sources", expire_after_source_read
    )
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda path: path == Path("/usr/bin/bwrap") or original_is_file(path),
    )
    original_access = os.access
    monkeypatch.setattr(
        local_execution_module.os,
        "access",
        lambda path, mode: (
            os.fspath(path) == "/usr/bin/bwrap" or original_access(path, mode)
        ),
    )
    monkeypatch.setattr(local_execution_module, "_require_tmpfs", lambda _path: None)

    with pytest.raises(LocalExecutionError) as error:
        execute_local_change(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            authorization_id=authorization.authorization_id,
            workspace=repository,
        )

    assert error.value.classification == "authorization_expired"
    assert not list(store.root.glob("*.local-effect-attempt.*.json"))


def test_sandbox_parent_captures_only_bounded_output() -> None:
    process = subprocess.Popen(
        [
            "/usr/bin/python3",
            "-c",
            "import os\nwhile True: os.write(1, b'x' * 65536)",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    status, stdout, stderr = _capture_bounded_output(
        process,
        timeout_seconds=5,
        stdout_limit=4096,
        stderr_limit=1024,
    )
    assert status == "sandbox_output_limit"
    assert stdout == stderr == b""
    assert process.returncode is not None


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
    _git(repository, "add", "calculator.py", "test_calculator.py")
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


def _run_input(
    root: Path,
    revision: str,
    source: bytes,
    *,
    repository_path: str | None = None,
    read_paths: list[str] | None = None,
    write_paths: list[str] | None = None,
    source_context: dict[str, str] | None = None,
) -> str:
    paths_to_read = read_paths or ["calculator.py", "test_calculator.py"]
    paths_to_write = write_paths or ["calculator.py"]
    context = source_context or {
        "calculator.py": source.decode("utf-8"),
        "test_calculator.py": (root / "repository" / "test_calculator.py").read_text(
            encoding="utf-8"
        ),
    }
    return json.dumps(
        {
            "acceptance_criteria": [
                "answer() returns 2",
                "the supplied unittest suite passes",
            ],
            "allowed_paths": {"read": paths_to_read, "write": paths_to_write},
            "repository": {
                "base_revision": revision,
                "identity": "temporary offline-proof repository",
                "path": repository_path or str(root / "repository"),
            },
            "request": "Change answer() to return 2 without changing the test.",
            "source_context": context,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _candidate(
    root: Path,
    revision: str,
    source: bytes,
    *,
    run_id: str = "run-local-effect",
    change_set: str | None = None,
    store_root: Path | None = None,
    run_input_override: str | None = None,
):
    store_root = store_root or root / f"runs-{run_id}"
    run_input = run_input_override or _run_input(root, revision, source)
    captured_requests: list[object] = []
    workflow, evidence, calls, result = _start_fixture(
        store_root,
        run_id=run_id,
        artifact_content_type=FILE_CHANGE_SET_CONTENT_TYPE,
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    require_local_sandbox: None,
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
    require_local_sandbox: None,
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


def test_expired_grant_is_rejected_before_claim(
    tmp_path: Path, require_local_sandbox: None
) -> None:
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
    tmp_path: Path, require_local_sandbox: None
) -> None:
    repository, _revision, _source = _temporary_repo(tmp_path)
    (repository / ".gitattributes").write_text(
        "calculator.py filter=offlineprobe\n", encoding="utf-8"
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "--quiet", "-m", "declare test filter")
    revision = _git(repository, "rev-parse", "HEAD")
    with pytest.raises(OfflineDeliveryError):
        _validate_attributes(repository, ["calculator.py"])
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
    with pytest.raises(LocalExecutionError):
        issue_local_execution_authorization(
            store,
            run_id=artifact.run_id,
            artifact_id=artifact.artifact_id,
            workspace=repository,
            policy=policy,
            approved_by="offline-proof-operator",
            approval_id="symlink-grant",
        )
    assert not list(store.root.glob("*.local-effect-authorization.*.json"))
    assert not list(store.root.glob("*.local-effect-attempt.*.json"))


def test_timeout_is_durable_and_not_replayed(
    tmp_path: Path, require_local_sandbox: None
) -> None:
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


def test_output_limit_is_durable_and_redacted(
    tmp_path: Path, require_local_sandbox: None
) -> None:
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


@pytest.mark.parametrize(
    "needs_revision",
    [True, False],
    ids=["reviewed-after-revision", "candidate-no-revision"],
)
def test_bounded_workflow_validates_revises_and_re_reviews_exact_artifacts(
    tmp_path: Path,
    require_local_sandbox: None,
    needs_revision: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_office.engine.artifact import read_run_artifact

    repository, revision, source = _temporary_repo(tmp_path)
    source_context = {
        "calculator.py": source.decode("utf-8"),
        "test_calculator.py": (repository / "test_calculator.py").read_text(
            encoding="utf-8"
        ),
    }
    assignment = json.dumps(
        {
            "acceptance_criteria": ["answer() returns 2", "tests pass"],
            "allowed_paths": {
                "read": ["calculator.py", "test_calculator.py"],
                "write": ["calculator.py"],
            },
            "repository": {
                "base_ref": "refs/heads/main",
                "base_revision": revision,
                "full_name": "offline-owner/offline-repository",
                "id": 805001,
                "path": str(repository),
            },
            "publication": {
                "requested_effects": list(PUBLICATION_INTENT_EFFECTS),
                "target_ref": "refs/heads/codex/issue-805-intent-handoff",
            },
            "request": "Change answer() to return 2 without changing the test.",
            "source_context": source_context,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    store_root = tmp_path / "bounded-workflow-runs"
    store_root.mkdir(mode=0o700)
    store = WorkflowRunManifestStore(store_root)
    workflow = load_workflow_file(_SAMPLE_ROOT / "workflows/bounded-change-review.yaml")
    employees = {
        loaded.definition.id: loaded
        for loaded in load_employees(_SAMPLE_ROOT / "employees")
    }
    target = DIRECT_OPENAI_EXECUTION_TARGET
    source_manifest = build_workflow_run_manifest(
        "offline-source-definition",
        assignment,
        workflow,
        tuple(employees.values()),
        execution_targets=tuple(target for _ in workflow.definition.steps),
    )
    first_step_id = workflow.definition.bounded_review.draft_step
    initial_view = bounded_job_execution_view(workflow, first_step_id)
    initial_manifest = build_workflow_run_manifest(
        "offline-integrated-initial",
        assignment,
        initial_view,
        (employees["change-author"],),
        execution_targets=(target,),
    )
    persist_workflow_run_manifest(store, source_manifest)
    persist_workflow_run_manifest(store, initial_manifest)

    def digest(value: object) -> str:
        encoded = json.dumps(
            asdict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    slots = []
    for ordinal, (step_id, step_index, input_source) in enumerate(
        bounded_review_action_plan(workflow, 1)
    ):
        view = bounded_job_execution_view(workflow, step_id)
        employee_id = view.definition.steps[step_index - 1].employee
        manifest = (
            initial_manifest
            if ordinal == 0
            else build_workflow_run_manifest(
                f"offline-template-{ordinal}",
                assignment,
                view,
                (employees[employee_id],),
                execution_targets=(target,),
            )
        )
        employee_snapshot = next(
            item for item in manifest.employee_snapshots if item.id == employee_id
        )
        slots.append(
            BoundedJobActionSlot(
                ordinal=ordinal,
                workflow_id=workflow.definition.id,
                step_id=step_id,
                step_index=step_index,
                employee_id=employee_id,
                workflow_snapshot_sha256=digest(manifest.workflow_snapshot),
                employee_snapshot_sha256=digest(employee_snapshot),
                provider=target.provider,
                execution_target_fingerprint=execution_target_fingerprint(target),
                model=manifest.workflow_snapshot.steps[0].model,
                input_source=input_source,
            )
        )
    assignment_sha = sha256(assignment.encode("utf-8")).hexdigest()
    scope = issue_bounded_workflow_job_scope(
        store,
        job_id="offline-integrated-job",
        initial_run_id=initial_manifest.run_id,
        assignment_sha256=assignment_sha,
        initial_input_sha256=assignment_sha,
        actions=tuple(slots),
        max_provider_attempts=6,
        max_revisions=1,
        source_manifest_run_id=source_manifest.run_id,
        source_manifest_digest=source_manifest.digest,
        expires_at_ns=9_999_999_999_999_999_999,
        business_approved_by="offline-proof-operator",
        business_grant_id="offline-integrated-business",
        execution_approved_by="offline-proof-operator",
        execution_grant_id="offline-integrated-execution",
        now_ns=1,
    )
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
    ).model_copy(update={"expires_at_ns": time.time_ns() + 600_000_000_000})
    citation_line = next(
        (index, line)
        for index, line in enumerate(assignment.splitlines(), start=1)
        if "answer() returns 2" in line
    )
    citation = f"assignment:L{citation_line[0]} `{citation_line[1]}`"
    calls: list[dict[str, object]] = []
    reviewer_inputs: list[dict[str, object]] = []

    def response(text: str) -> OpenAIResponsesRawHttpResponse:
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("content-type", "application/json"),),
            json.dumps(
                {
                    "id": f"offline-response-{len(calls)}",
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": text}],
                        }
                    ],
                }
            ).encode(),
        )

    def pass_review() -> str:
        return (
            "Verdict: PASS\n\n### Check 1\n"
            "Requirement: answer() returns 2.\n"
            "Artifact location: `return 2`\n"
            f"Evidence source/reference: {citation}\n"
            "Assessment: The exact diff and successful test evidence meet the "
            "request.\n"
            "Unresolved material issues: none"
        )

    findings = (
        "Verdict: CHANGES_REQUIRED\n\n### Finding 1\n"
        "Requirement: Keep the implementation minimal.\n"
        "Artifact location: `return 2  # unnecessary comment`\n"
        f"Evidence source/reference: {citation}\n"
        "Impact: The candidate adds an unnecessary comment.\n"
        "Requested correction: Remove the comment."
    )

    def fake_transport(request: object) -> OpenAIResponsesRawHttpResponse:
        payload = json.loads(request.body)
        task_input = json.loads(payload["input"])
        run_input = json.loads(task_input["run_input"])
        calls.append({"request": request, "run_input": run_input})
        if "source_artifact" in run_input and "review_artifacts" not in run_input:
            reviewer_inputs.append(run_input)
        index = len(calls) - 1
        if index == 0:
            output = _change_set(
                revision,
                source,
                replacement=(
                    b"def answer():\n    return 2  # unnecessary comment\n"
                    if needs_revision
                    else b"def answer():\n    return 2\n"
                ),
            )
        elif needs_revision and index == 1:
            output = findings
        elif needs_revision and index == 2:
            output = pass_review()
        elif needs_revision and index == 3:
            output = _change_set(revision, source)
        else:
            output = pass_review()
        return response(output)

    authorizations: list[str] = []

    def authorize_and_validate(run_id: str, artifact_id: str, ordinal: int) -> str:
        authorization = issue_local_execution_authorization(
            store,
            run_id=run_id,
            artifact_id=artifact_id,
            workspace=repository,
            policy=policy,
            approved_by="offline-proof-operator",
            approval_id=f"integrated-local-effect-{ordinal}",
        )
        evidence = execute_local_change(
            store,
            run_id=run_id,
            artifact_id=artifact_id,
            authorization_id=authorization.authorization_id,
            workspace=repository,
        )
        assert evidence["status"] == "verified"
        authorizations.append(authorization.authorization_id)
        return authorization.authorization_id

    outcome = run_bounded_document_job(
        store,
        scope,
        initial_run_id=initial_manifest.run_id,
        assignment=assignment,
        workflows={workflow.definition.id: workflow},
        employees=employees,
        api_key=OpenAIApiKey(value=SecretStr("offline-only")),
        transport=fake_transport,
        local_effect_handler=authorize_and_validate,
    )

    assert outcome.status == ("reviewed" if needs_revision else "candidate"), outcome
    assert outcome.revisions_used == (1 if needs_revision else 0)
    assert len(calls) == (6 if needs_revision else 3)
    assert len(authorizations) == (2 if needs_revision else 1)
    assert len(reviewer_inputs) == (4 if needs_revision else 2)
    if needs_revision:
        first_round = reviewer_inputs[:2]
        second_round = reviewer_inputs[2:]
        assert (
            first_round[0]["local_execution_evidence"]
            == first_round[1]["local_execution_evidence"]
        )
        assert (
            second_round[0]["local_execution_evidence"]
            == second_round[1]["local_execution_evidence"]
        )
        first_evidence = first_round[0]["local_execution_evidence"]
        second_evidence = second_round[0]["local_execution_evidence"]
        assert first_evidence["status"] == second_evidence["status"] == "verified"
        assert first_evidence["artifact_id"] != second_evidence["artifact_id"]
        assert first_evidence["diff"] != second_evidence["diff"]
        assert "# unnecessary comment" in first_evidence["diff"]
        assert "# unnecessary comment" not in second_evidence["diff"]
        assert (
            len({item["source_artifact"]["content_sha256"] for item in first_round})
            == 1
        )
        assert (
            len({item["source_artifact"]["content_sha256"] for item in second_round})
            == 1
        )
    else:
        assert all(
            item["local_execution_evidence"]["status"] == "verified"
            for item in reviewer_inputs
        )
    final_artifact = read_run_artifact(
        store,
        outcome.final_artifact_run_id,
        outcome.final_artifact_id,
    )
    assert final_artifact.content_type == FILE_CHANGE_SET_CONTENT_TYPE
    final_change = json.loads(final_artifact.content)
    final_source = base64.b64decode(
        final_change["changes"][0]["content_base64"], validate=True
    )
    assert final_source == b"def answer():\n    return 2\n"

    # Publication intent is a signed, authority-free handoff derived from the
    # canonical Run/Job/Artifact/Evidence owners. No Run data is written.
    intent_now = time.time_ns()
    intent_expires = intent_now + 30 * 60 * 1_000_000_000
    workflow_run_id = 805_000_001
    workflow_run_attempt = 1
    signing_key = Ed25519PrivateKey.generate()
    public_key = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    trusted_keys = {sha256(public_key).hexdigest(): public_key}
    run_snapshot_before = {
        path.name: path.read_bytes() for path in store.root.iterdir() if path.is_file()
    }
    handoff = build_publication_intent_handoff(
        store,
        job_id=scope.job_id,
        workflow_run_id=workflow_run_id,
        workflow_run_attempt=workflow_run_attempt,
        expires_at_ns=intent_expires,
        signing_key=signing_key,
        now_ns=intent_now,
    )
    expected_handoff_context = {
        "expected_workflow_run_id": workflow_run_id,
        "expected_workflow_run_attempt": workflow_run_attempt,
        "expected_repository_id": 805001,
        "expected_repository_full_name": "offline-owner/offline-repository",
        "expected_base_ref": "refs/heads/main",
        "expected_base_revision": revision,
        "expected_target_ref": "refs/heads/codex/issue-805-intent-handoff",
        "now_ns": intent_now,
    }
    intent = verify_publication_intent_handoff(
        handoff, trusted_keys, **expected_handoff_context
    )
    assert intent.authority == "intent_only"
    assert intent.job_status == ("reviewed" if needs_revision else "candidate")
    assert intent.revisions_used == (1 if needs_revision else 0)
    assert intent.artifact_id == final_artifact.artifact_id
    assert intent.artifact_sha256 == final_artifact.content_sha256
    assert base64.b64decode(intent.artifact_content_base64, validate=True) == (
        final_artifact.content
    )
    handoff_record = json.loads(handoff)
    assert "approved_by" not in handoff_record["intent"]
    assert "approval" not in handoff_record["intent"]
    assert (
        handoff_record["intent"]["verification_authorization_id"] == authorizations[-1]
    )
    assert {
        path.name: path.read_bytes() for path in store.root.iterdir() if path.is_file()
    } == run_snapshot_before

    # Handoff bytes cannot be replaced, reused for another run/attempt, or
    # turned into a human approval by adding caller-controlled metadata.
    tampered_signature = json.loads(handoff)
    signature_bytes = bytearray(
        base64.b64decode(tampered_signature["signature_base64"], validate=True)
    )
    signature_bytes[0] ^= 1
    tampered_signature["signature_base64"] = base64.b64encode(signature_bytes).decode(
        "ascii"
    )
    with pytest.raises(PublicationIntentError):
        verify_publication_intent_handoff(
            json.dumps(
                tampered_signature,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            trusted_keys,
            **expected_handoff_context,
        )

    tampered_content = json.loads(handoff)
    tampered_content["intent"]["artifact_content_base64"] = base64.b64encode(
        b"different candidate"
    ).decode("ascii")
    with pytest.raises(PublicationIntentError):
        verify_publication_intent_handoff(
            json.dumps(
                tampered_content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            trusted_keys,
            **expected_handoff_context,
        )
    for context_change in (
        {"expected_workflow_run_id": workflow_run_id + 1},
        {"expected_workflow_run_attempt": workflow_run_attempt + 1},
        {"expected_repository_id": 805002},
        {"expected_repository_full_name": "other-owner/other-repository"},
        {"expected_base_revision": "0" * 40},
        {"expected_target_ref": "refs/heads/codex/other-intent"},
    ):
        with pytest.raises(PublicationIntentError):
            verify_publication_intent_handoff(
                handoff,
                trusted_keys,
                **{**expected_handoff_context, **context_change},
            )
    forged_approval = json.loads(handoff)
    forged_approval["intent"]["approved_by"] = "pytest-forged-human"
    forged_approval["signature_base64"] = base64.b64encode(
        signing_key.sign(
            json.dumps(
                forged_approval["intent"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    ).decode("ascii")
    with pytest.raises(PublicationIntentError):
        verify_publication_intent_handoff(
            json.dumps(
                forged_approval,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            trusted_keys,
            **expected_handoff_context,
        )
    with pytest.raises(PublicationIntentError):
        verify_publication_intent_handoff(
            handoff,
            trusted_keys,
            **{**expected_handoff_context, "now_ns": intent_expires},
        )

    # #807 offline proof: the reviewed Artifact is admitted only to a new
    # local branch, local bare remote, and persistent fake GitHub API.
    remote = tmp_path / "fake-remote.git"
    create_fake_bare_remote(remote, repository, revision)
    hook_marker = tmp_path / "pre-push-hook-ran"
    hook = repository / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\n/usr/bin/touch {hook_marker}\n", encoding="utf-8")
    hook.chmod(0o700)
    target_ref = "refs/heads/codex/issue-807-offline-proof"
    expiry = time.time_ns() + 30 * 60 * 1_000_000_000
    repository_id = _repository_identity(repository)
    api_state = tmp_path / "fake-github-api" / "state.json"
    api = FakeGitHubDeliveryAPI(
        remote=remote,
        state_path=api_state,
        credential=fake_github_credential_for_test(repository_id, expires_at_ns=expiry),
    )

    if needs_revision:
        # The first candidate was locally verified but rejected by review.
        # Supplying its old Artifact and Evidence must fail before a grant or
        # any of the three delivery effects can be recorded.
        stale_source = reviewer_inputs[0]["source_artifact"]
        stale_outcome = replace(
            outcome,
            final_artifact_run_id=stale_source["run_id"],
            final_artifact_id=stale_source["artifact_id"],
        )
        grants_before = set(store.root.glob("*.offline-delivery.*.grant.json"))
        stale_ref = "refs/heads/codex/issue-807-rejected-candidate"
        with pytest.raises(OfflineDeliveryError, match="terminal_outcome_mismatch"):
            issue_test_delivery_grant(
                store,
                scope,
                stale_outcome,
                workspace=repository,
                remote=remote,
                target_ref=stale_ref,
                verification_authorization_id=authorizations[0],
                expires_at_ns=expiry,
            )
        assert set(store.root.glob("*.offline-delivery.*.grant.json")) == grants_before
        assert not list(store.root.glob("*.offline-delivery.*.attempt.json"))
        assert _optional_git(repository, "rev-parse", stale_ref) is None
        assert _optional_git(remote, "rev-parse", stale_ref) is None
        assert api.posts == 0

    # Protected refs and unexpired/revoked grants fail before an Attempt exists.
    with pytest.raises(OfflineDeliveryError):
        issue_test_delivery_grant(
            store,
            scope,
            outcome,
            workspace=repository,
            remote=remote,
            target_ref="refs/heads/main",
            verification_authorization_id=authorizations[-1],
            expires_at_ns=expiry,
        )

    # Product CLI proof: standard Git writes only to a local bare fake remote;
    # every gh operation is served by this test executable, never GitHub.
    product_remote = tmp_path / "product-fake-remote.git"
    create_fake_bare_remote(product_remote, repository, revision)
    remote_url = "https://github.com/offline-owner/offline-repository.git"
    _git(repository, "remote", "add", "origin", remote_url)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    git_wrapper = fake_bin / "git"
    git_wrapper.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """\
            import os
            import sys

            args = sys.argv[1:]
            url = os.environ['AI_OFFICE_TEST_REMOTE_URL']
            for index, arg in enumerate(args):
                if arg == url:
                    args[index] = os.environ['AI_OFFICE_TEST_REMOTE_PATH']
            if 'push' in args:
                with open(os.environ['AI_OFFICE_TEST_PUSH_LOG'], 'a') as stream:
                    stream.write('push\\n')
            os.execv('/usr/bin/git', ['/usr/bin/git', *args])
            """
        ),
        encoding="utf-8",
    )
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """\
            import json
            import os
            import subprocess
            import sys

            args = sys.argv[1:]
            state_path = os.environ['AI_OFFICE_TEST_GH_STATE']
            remote = os.environ['AI_OFFICE_TEST_REMOTE_PATH']

            def state():
                try:
                    with open(state_path) as stream:
                        return json.load(stream)
                except FileNotFoundError:
                    return {'prs': [], 'events': [], 'queries': []}

            def save(value):
                with open(state_path, 'w') as stream:
                    json.dump(value, stream)

            def git(*parts):
                command = ['/usr/bin/git', '--git-dir', remote, *parts]
                return subprocess.check_output(command, text=True).strip()

            def option(name):
                return args[args.index(name) + 1]

            if args[:2] == ['auth', 'status']:
                print('Logged in to github.com')
            elif args[0] == 'api':
                endpoint = args[1]
                if endpoint == 'repos/offline-owner/offline-repository':
                    print(json.dumps({
                        'id': 805001,
                        'full_name': 'offline-owner/offline-repository',
                        'default_branch': 'main',
                    }))
                elif endpoint == (
                    'repos/offline-owner/offline-repository/branches/main'
                ):
                    print(json.dumps({'protected': False}))
                else:
                    raise SystemExit(2)
            elif args[:2] == ['pr', 'list']:
                data = state()
                branch = option('--head')
                if ':' in branch:
                    raise SystemExit(2)
                matches = [
                    item for item in data['prs']
                    if item['headRefName'] == branch
                ]
                data.setdefault('queries', []).append({
                    'head': branch,
                    'matches': len(matches),
                })
                save(data)
                print(json.dumps(matches))
            elif args[:2] == ['pr', 'create']:
                data = state()
                branch = option('--head')
                base = option('--base')
                head_sha = git('rev-parse', 'refs/heads/' + branch)
                base_sha = git('rev-parse', 'refs/heads/' + base)
                changed = git(
                    'diff-tree', '--no-commit-id', '--name-only', '-r',
                    base_sha, head_sha,
                ).splitlines()
                number = len(data['prs']) + 1
                url = (
                    'https://github.com/offline-owner/offline-repository/'
                    f'pull/{number}'
                )
                with open(option('--body-file')) as stream:
                    body = stream.read()
                record = {
                    'number': number,
                    'url': url,
                    'state': 'OPEN',
                    'isDraft': True,
                    'baseRefName': base,
                    'baseRefOid': base_sha,
                    'headRefName': branch,
                    'headRefOid': head_sha,
                    'headRepository': {
                        'nameWithOwner': 'offline-owner/offline-repository',
                    },
                    'body': body,
                    'files': [{'path': path} for path in changed],
                }
                data['prs'].append(record)
                data['events'].append('create')
                save(data)
                if os.environ.get('AI_OFFICE_TEST_DROP_PR_RESPONSE') == '1':
                    data['events'].append('create_response_dropped')
                    save(data)
                    raise SystemExit(1)
                print(url)
            elif args[:2] == ['pr', 'view']:
                data = state()
                url = args[2]
                record = next(item for item in data['prs'] if item['url'] == url)
                print(json.dumps(record))
            else:
                raise SystemExit(2)
            """
        ),
        encoding="utf-8",
    )
    git_wrapper.chmod(0o700)
    fake_gh.chmod(0o700)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}")
    monkeypatch.setenv("AI_OFFICE_TEST_REMOTE_URL", remote_url)
    monkeypatch.setenv("AI_OFFICE_TEST_REMOTE_PATH", str(product_remote))
    monkeypatch.setenv("AI_OFFICE_TEST_PUSH_LOG", str(tmp_path / "push.log"))
    gh_state_path = tmp_path / "gh-state.json"
    monkeypatch.setenv("AI_OFFICE_TEST_GH_STATE", str(gh_state_path))
    monkeypatch.setenv(
        "AI_OFFICE_TEST_DROP_PR_RESPONSE", "1" if needs_revision else "0"
    )

    product_branch = (
        f"ai-office/{scope.job_id}-{'reviewed' if needs_revision else 'candidate'}"
    )
    worktree_before = _git(
        repository, "status", "--porcelain=v1", "--untracked-files=all"
    )
    head_before = _git(repository, "rev-parse", "HEAD")
    command = [
        "workflows",
        "draft-pr",
        scope.job_id,
        "--run-store",
        str(store.root),
        "--git-repository",
        str(repository),
        "--repository",
        "offline-owner/offline-repository",
        "--base-ref",
        "main",
        "--branch",
        product_branch,
        "--yes",
    ]

    # A pre-existing PR from this exact head repository is a safe conflict;
    # the fake rejects the unsupported OWNER:BRANCH filter syntax.
    gh_state_path.write_text(
        json.dumps(
            {
                "events": [],
                "prs": [
                    {
                        "number": 99,
                        "url": "https://github.com/offline-owner/offline-repository/pull/99",
                        "state": "OPEN",
                        "isDraft": True,
                        "baseRefName": "main",
                        "baseRefOid": revision,
                        "headRefName": product_branch,
                        "headRefOid": revision,
                        "headRepository": {
                            "nameWithOwner": "offline-owner/offline-repository"
                        },
                    }
                ],
                "queries": [],
            }
        ),
        encoding="utf-8",
    )
    collision = _CLI_RUNNER.invoke(app, command)
    assert collision.exit_code != 0
    assert "target_pull_request_already_exists" in collision.output
    collision_state = json.loads(gh_state_path.read_text(encoding="utf-8"))
    assert collision_state["queries"] == [{"head": product_branch, "matches": 1}]
    assert not (tmp_path / "push.log").exists()
    assert not (store.root / f"{scope.job_id}.draft-pr-delivery.attempt.json").exists()

    # Reset the fake remote's PR state for the successful delivery scenario.
    gh_state_path.write_text(
        json.dumps(
            {
                "events": [],
                "prs": [
                    {
                        "number": 98,
                        "url": "https://github.com/offline-owner/offline-repository/pull/98",
                        "state": "OPEN",
                        "isDraft": True,
                        "baseRefName": "main",
                        "baseRefOid": revision,
                        "headRefName": product_branch,
                        "headRefOid": revision,
                        "headRepository": {
                            "nameWithOwner": "offline-fork/offline-repository"
                        },
                    }
                ],
                "queries": [],
            }
        ),
        encoding="utf-8",
    )
    delivered = _CLI_RUNNER.invoke(app, command)
    assert delivered.exit_code == 0, delivered.output
    delivery_result = json.loads(delivered.stdout.splitlines()[-1])
    assert delivery_result["status"] == "completed"
    assert delivery_result["artifact_id"] == outcome.final_artifact_id
    assert delivery_result["base_sha"] == revision
    assert delivery_result["branch"] == product_branch
    assert delivery_result["pr_url"].endswith("/pull/2")
    assert delivery_result["reconciled_read_only"] is needs_revision
    assert "protected=false" in delivered.output
    assert (
        _git(product_remote, "rev-parse", f"refs/heads/{product_branch}")
        == delivery_result["commit_sha"]
    )
    assert (
        _git(product_remote, "show", "-s", "--format=%P", delivery_result["commit_sha"])
        == revision
    )
    assert _git(product_remote, "rev-parse", "refs/heads/main") == revision
    assert (repository / "calculator.py").read_bytes() == source
    assert (
        _git(repository, "status", "--porcelain=v1", "--untracked-files=all")
        == worktree_before
    )
    assert _git(repository, "rev-parse", "HEAD") == head_before
    gh_state = json.loads((tmp_path / "gh-state.json").read_text(encoding="utf-8"))
    assert gh_state["events"] == (
        ["create", "create_response_dropped"] if needs_revision else ["create"]
    )
    assert [query["matches"] for query in gh_state["queries"]] == [1, 1, 2]
    assert all(query["head"] == product_branch for query in gh_state["queries"])
    pull_request = gh_state["prs"][1]
    assert pull_request["isDraft"] is True
    assert pull_request["baseRefName"] == "main"
    assert pull_request["baseRefOid"] == revision
    assert pull_request["headRefName"] == product_branch
    assert pull_request["headRefOid"] == delivery_result["commit_sha"]
    assert pull_request["headRepository"]["nameWithOwner"] == (
        "offline-owner/offline-repository"
    )
    assert "Closes #805" not in pull_request["body"]
    assert "Fixes #805" not in pull_request["body"]
    assert (tmp_path / "push.log").read_text(encoding="utf-8").splitlines() == ["push"]

    # Re-running proves the exact recorded result with reads only.
    replay = _CLI_RUNNER.invoke(app, command)
    assert replay.exit_code == 0, replay.output
    replay_result = json.loads(replay.stdout.splitlines()[-1])
    assert replay_result["commit_sha"] == delivery_result["commit_sha"]
    assert replay_result["reconciled_read_only"] is True
    replay_state = json.loads(gh_state_path.read_text(encoding="utf-8"))
    assert replay_state["events"] == gh_state["events"]
    assert [query["matches"] for query in replay_state["queries"]] == [1, 1, 2, 2]
    assert (tmp_path / "push.log").read_text(encoding="utf-8").splitlines() == ["push"]
    expired = issue_test_delivery_grant(
        store,
        scope,
        outcome,
        workspace=repository,
        remote=remote,
        target_ref="refs/heads/codex/issue-807-expired",
        verification_authorization_id=authorizations[-1],
        expires_at_ns=expiry,
    )
    with pytest.raises(OfflineDeliveryError):
        run_offline_delivery(
            store,
            scope,
            outcome,
            workspace=repository,
            remote=remote,
            target_ref="refs/heads/codex/issue-807-expired",
            verification_authorization_id=authorizations[-1],
            grant_id=expired,
            fake_pr_api=api,
            now_ns=expiry,
        )
    revoked = issue_test_delivery_grant(
        store,
        scope,
        outcome,
        workspace=repository,
        remote=remote,
        target_ref="refs/heads/codex/issue-807-revoked",
        verification_authorization_id=authorizations[-1],
        expires_at_ns=expiry,
    )
    revoke_test_delivery_grant(store, scope.job_id, revoked)
    with pytest.raises(OfflineDeliveryError):
        run_offline_delivery(
            store,
            scope,
            outcome,
            workspace=repository,
            remote=remote,
            target_ref="refs/heads/codex/issue-807-revoked",
            verification_authorization_id=authorizations[-1],
            grant_id=revoked,
            fake_pr_api=api,
        )
    expired_attempts = store.root.glob(
        f"{scope.job_id}.offline-delivery.{expired}.*.attempt.json"
    )
    revoked_attempts = store.root.glob(
        f"{scope.job_id}.offline-delivery.{revoked}.*.attempt.json"
    )
    assert not list(expired_attempts)
    assert not list(revoked_attempts)

    grants_before = set(store.root.glob("*.offline-delivery.*.grant.json"))
    with pytest.raises(OfflineDeliveryError, match="synthetic_human_approval"):
        issue_test_delivery_grant(
            store,
            scope,
            outcome,
            workspace=repository,
            remote=remote,
            target_ref="refs/heads/codex/issue-807-forged-approval",
            verification_authorization_id=authorizations[-1],
            expires_at_ns=expiry,
            approval={"approved_by": "pytest-offline-trust-boundary"},
        )
    assert set(store.root.glob("*.offline-delivery.*.grant.json")) == grants_before
    assert not list(store.root.glob("*.offline-delivery.*.attempt.json"))
    assert api.posts == 0

    grant_id = issue_test_delivery_grant(
        store,
        scope,
        outcome,
        workspace=repository,
        remote=remote,
        target_ref=target_ref,
        verification_authorization_id=authorizations[-1],
        expires_at_ns=expiry,
    )

    wrong_api = FakeGitHubDeliveryAPI(
        remote=remote,
        state_path=tmp_path / "wrong-repository-api" / "state.json",
        credential=fake_github_credential_for_test(
            "wrong-repository", expires_at_ns=expiry
        ),
    )
    with pytest.raises(OfflineDeliveryError, match="fake_credential_scope"):
        run_offline_delivery(
            store,
            scope,
            outcome,
            workspace=repository,
            remote=remote,
            target_ref=target_ref,
            verification_authorization_id=authorizations[-1],
            grant_id=grant_id,
            fake_pr_api=wrong_api,
        )
    assert not list(
        store.root.glob(f"{scope.job_id}.offline-delivery.{grant_id}.*.attempt.json")
    )
    assert _optional_git(repository, "rev-parse", target_ref) is None
    assert wrong_api.posts == 0
    # A crash after each effect leaves an Attempt. Resumption reconciles the
    # existing state read-only; it never sends the same push or POST again.
    with pytest.raises(SimulatedEffectCrash):
        run_offline_delivery(
            store,
            scope,
            outcome,
            workspace=repository,
            remote=remote,
            target_ref=target_ref,
            verification_authorization_id=authorizations[-1],
            grant_id=grant_id,
            fake_pr_api=api,
            crash_after="local_commit",
        )
    with pytest.raises(SimulatedEffectCrash):
        run_offline_delivery(
            store,
            scope,
            outcome,
            workspace=repository,
            remote=remote,
            target_ref=target_ref,
            verification_authorization_id=authorizations[-1],
            grant_id=grant_id,
            fake_pr_api=api,
            crash_after="remote_ref_create",
        )

    payload = {
        "api_state": str(api_state),
        "grant_id": grant_id,
        "job_id": scope.job_id,
        "remote": str(remote),
        "repository_id": repository_id,
        "store_root": str(store.root),
        "target_ref": target_ref,
        "verification_authorization_id": authorizations[-1],
        "workspace": str(repository),
    }
    child_home = tmp_path / "restart-home"
    child_home.mkdir(mode=0o700)
    driver = """\
import json
import sys
from tests._offline_git_delivery import (
    OfflineDeliveryError,
    SimulatedEffectCrash,
    resume_offline_delivery_process,
)

try:
    result = resume_offline_delivery_process(json.loads(sys.argv[1]))
except SimulatedEffectCrash:
    raise SystemExit(86)
except OfflineDeliveryError as exc:
    print(json.dumps({"stopped": str(exc)}, sort_keys=True))
    raise SystemExit(87)
print(json.dumps(result, sort_keys=True))
"""
    child_env = {
        "HOME": str(child_home),
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": f"{_SAMPLE_ROOT / 'src'}:{_SAMPLE_ROOT}",
        "AI_OFFICE_TEST_FAKE_GITHUB_TOKEN": ("offline-only-fake-installation-token"),
    }

    def restart_process(*, lose_pr_response: bool, process_payload=payload):
        env = dict(child_env)
        if lose_pr_response:
            env["AI_OFFICE_TEST_DROP_PR_RESPONSE"] = "1"
        return subprocess.run(
            [sys.executable, "-c", driver, json.dumps(process_payload)],
            capture_output=True,
            check=False,
            cwd=_SAMPLE_ROOT,
            env=env,
            shell=False,
            text=True,
            timeout=60,
        )

    first_process = restart_process(lose_pr_response=True)
    assert first_process.returncode == 86, first_process.stderr
    assert "offline-only-fake-installation-token" not in (
        first_process.stdout + first_process.stderr
    )
    assert api.posts == 5
    second_process = restart_process(lose_pr_response=False)
    assert second_process.returncode == 0, second_process.stderr
    assert "offline-only-fake-installation-token" not in (
        second_process.stdout + second_process.stderr
    )
    receipt = json.loads(second_process.stdout)
    assert receipt["artifact_id"] == final_artifact.artifact_id
    assert receipt["draft_pr"]["draft"] is True
    assert receipt["draft_pr"]["url"].startswith("https://fake.invalid/")
    assert receipt["draft_pr"]["base_sha"] == revision
    assert receipt["local_commit_sha"] != receipt["remote_commit_sha"]
    assert receipt["commit_sha"] == receipt["remote_commit_sha"]
    assert receipt["draft_pr"]["head_sha"] == receipt["remote_commit_sha"]
    assert receipt["draft_pr"]["diff_sha256"]
    effects = {item["effect"]: item for item in receipt["effects"]}
    assert effects["local_commit"]["reconciled_read_only"] is True
    assert effects["remote_ref_create"]["reconciled_read_only"] is True
    assert effects["draft_pr"]["reconciled_read_only"] is True
    assert all(
        effects[name]["reconciled_read_only"] is False
        for name in (
            "remote_blob_create",
            "remote_tree_create",
            "remote_commit_create",
        )
    )
    post_endpoints = [
        item["endpoint"] for item in api.requests if item["method"] == "POST"
    ]
    assert post_endpoints == [
        "/git/blobs",
        "/git/trees",
        "/git/commits",
        "/git/refs",
        "/pulls",
    ]
    assert api.posts == 5
    assert not hook_marker.exists()
    assert _git(repository, "rev-parse", "HEAD") == revision
    assert _git(remote, "rev-parse", "refs/heads/main") == revision
    assert _git(remote, "rev-parse", target_ref) == receipt["remote_commit_sha"]
    assert (
        _git(
            repository,
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            revision,
            receipt["local_commit_sha"],
        )
        == "M\tcalculator.py"
    )
    assert (repository / "calculator.py").read_bytes() == source
    attempts = store.root.glob(
        f"{scope.job_id}.offline-delivery.{grant_id}.*.attempt.json"
    )
    assert len(list(attempts)) == 6

    # A repeated completed call is read-only and produces no second PR POST.
    replay = run_offline_delivery(
        store,
        scope,
        outcome,
        workspace=repository,
        remote=remote,
        target_ref=target_ref,
        verification_authorization_id=authorizations[-1],
        grant_id=grant_id,
        fake_pr_api=api,
    )
    assert replay == receipt
    assert api.posts == 5

    # A lost REST create-commit response can be unresolvable because GitHub may
    # assign a different SHA from the local candidate. A fresh process must
    # stop after a read miss rather than repeat the POST or create a ref/PR.
    ambiguous_ref = "refs/heads/codex/issue-807-unconfirmed-commit"
    ambiguous_grant = issue_test_delivery_grant(
        store,
        scope,
        outcome,
        workspace=repository,
        remote=remote,
        target_ref=ambiguous_ref,
        verification_authorization_id=authorizations[-1],
        expires_at_ns=expiry,
    )
    with pytest.raises(SimulatedEffectCrash, match="remote_commit_create"):
        run_offline_delivery(
            store,
            scope,
            outcome,
            workspace=repository,
            remote=remote,
            target_ref=ambiguous_ref,
            verification_authorization_id=authorizations[-1],
            grant_id=ambiguous_grant,
            fake_pr_api=api,
            crash_after="remote_commit_create",
        )
    posts_before_resume = api.posts
    request_count_before_resume = len(api.requests)
    ambiguous_payload = {
        **payload,
        "grant_id": ambiguous_grant,
        "target_ref": ambiguous_ref,
    }
    stopped_process = restart_process(
        lose_pr_response=False, process_payload=ambiguous_payload
    )
    assert stopped_process.returncode == 87, stopped_process.stderr
    assert json.loads(stopped_process.stdout) == {
        "stopped": "git_commit_outcome_ambiguous"
    }
    assert "offline-only-fake-installation-token" not in (
        stopped_process.stdout + stopped_process.stderr
    )
    assert api.posts == posts_before_resume
    assert not [
        item
        for item in api.requests[request_count_before_resume:]
        if item["method"] == "POST"
    ]
    assert _optional_git(remote, "rev-parse", ambiguous_ref) is None
    ambiguous_prefix = f"{scope.job_id}.offline-delivery.{ambiguous_grant}"
    assert not (
        store.root / f"{ambiguous_prefix}.remote_commit_create.result.json"
    ).exists()
    assert not (
        store.root / f"{ambiguous_prefix}.remote_ref_create.attempt.json"
    ).exists()
    assert not (store.root / f"{ambiguous_prefix}.draft_pr.attempt.json").exists()
    fake_token = b"offline-only-fake-installation-token"
    assert all(
        fake_token not in path.read_bytes() for path in store.root.glob("*.json")
    )
    assert fake_token not in api_state.read_bytes()

    # The same Run binding cannot be reused after its source base has moved.
    _git(repository, "checkout", "--quiet", target_ref)
    with pytest.raises(OfflineDeliveryError):
        issue_test_delivery_grant(
            store,
            scope,
            outcome,
            workspace=repository,
            remote=remote,
            target_ref="refs/heads/codex/issue-807-wrong-base",
            verification_authorization_id=authorizations[-1],
            expires_at_ns=expiry,
        )
