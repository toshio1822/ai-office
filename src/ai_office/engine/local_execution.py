"""Run-bound, one-use file-change verification in a fail-closed Linux sandbox."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_office.engine.artifact import WorkflowArtifact, read_run_artifact
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)

_SCHEMA = "local-execution-authorization.v1"
_EVIDENCE_SCHEMA = "local-execution-evidence.v1"
_FILE_CHANGE_TYPE = "application/vnd.ai-office.file-change-set+json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SENSITIVE = re.compile(
    r"(?i)(?:\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"
    r"|\b[A-Z0-9_-]*(?:API[-_]?KEY|ACCESS[-_]?TOKEN|"
    r"SECRET(?:[-_]?ACCESS[-_]?KEY)?|PASSWORD|TOKEN)\s*[:=]\s*\S+)"
)
_ASSIGNMENT = re.compile(
    r"(?i)(\b[A-Z0-9_-]*(?:API[-_]?KEY|ACCESS[-_]?TOKEN|"
    r"SECRET(?:[-_]?ACCESS[-_]?KEY)?|PASSWORD|TOKEN)\s*[:=]\s*)\S+"
)
_MAX_ARTIFACT_BYTES = 1_048_576
_MAX_SOURCE_BYTES = 2_097_152
_MAX_CHANGE_BYTES = 1_048_576
_MAX_RESULT_BYTES = 8_388_608
_PATH_TYPE = type(Path())


class LocalExecutionLimits(BaseModel):
    """Finite host-enforced bounds copied into one Run-bound authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    wall_timeout_seconds: int = Field(ge=1, le=300)
    cpu_seconds: int = Field(ge=1, le=300)
    memory_bytes: int = Field(ge=67_108_864, le=2_147_483_648)
    workspace_bytes: int = Field(ge=1_048_576, le=134_217_728)
    max_file_bytes: int = Field(ge=1, le=16_777_216)
    max_output_bytes: int = Field(ge=1, le=1_048_576)
    max_processes: int = Field(ge=1, le=1024)
    max_open_files: int = Field(ge=16, le=256)


class LocalExecutionPolicy(BaseModel):
    """Untrusted requested scope; authority is added only by explicit approval."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    base_revision: str
    read_paths: tuple[str, ...] = Field(min_length=1, max_length=64)
    write_paths: tuple[str, ...] = Field(min_length=1, max_length=64)
    commands: tuple[tuple[str, ...], ...] = Field(min_length=1, max_length=8)
    expires_at_ns: int = Field(gt=0)
    limits: LocalExecutionLimits

    @model_validator(mode="after")
    def validate_scope(self) -> LocalExecutionPolicy:
        if not _REVISION.fullmatch(self.base_revision):
            raise ValueError("invalid base revision")
        _validate_paths(self.read_paths)
        _validate_paths(self.write_paths)
        if not set(self.write_paths) <= set(self.read_paths):
            raise ValueError("write paths must be readable")
        if len(set(self.read_paths)) != len(self.read_paths):
            raise ValueError("read paths must be distinct")
        if len(set(self.write_paths)) != len(self.write_paths):
            raise ValueError("write paths must be distinct")
        if self.limits.max_file_bytes > self.limits.workspace_bytes:
            raise ValueError("file limit exceeds workspace limit")
        seen: set[tuple[str, ...]] = set()
        for argv in self.commands:
            if not argv or argv in seen:
                raise ValueError("commands must be non-empty and distinct")
            seen.add(argv)
            executable = Path(argv[0])
            if not executable.is_absolute() or not str(executable).startswith(
                "/usr/bin/"
            ):
                raise ValueError("commands must use an absolute /usr/bin path")
            resolved = executable.resolve(strict=True)
            if not resolved.is_file() or not os.access(resolved, os.X_OK):
                raise ValueError("command is unavailable")
            if not resolved.is_relative_to(Path("/usr/bin")):
                raise ValueError("command resolves outside /usr/bin")
            if any(
                not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv
            ):
                raise ValueError("invalid command argument")
            if any(_SENSITIVE.search(arg) for arg in argv):
                raise ValueError("secret-like command argument")
        return self


@dataclass(frozen=True)
class LocalExecutionAuthorization:
    """One immutable operator approval bound to exact Run, Artifact and workspace."""

    authorization_id: str
    run_id: str
    manifest_digest: str
    artifact_id: str
    artifact_sha256: str
    workspace_sha256: str
    policy: LocalExecutionPolicy
    approved_by: str
    approval_id: str
    issued_at_ns: int


class LocalExecutionError(ValueError):
    """Safe failure for authorization, sandbox, evidence and replay checks."""

    def __init__(self, classification: str = "contract") -> None:
        super().__init__("local execution could not be completed")
        self.classification = classification


def issue_local_execution_authorization(
    store: WorkflowRunManifestStore,
    *,
    run_id: str,
    artifact_id: str,
    workspace: Path,
    policy: LocalExecutionPolicy,
    approved_by: str,
    approval_id: str,
    now_ns: int | None = None,
) -> LocalExecutionAuthorization:
    """Persist a separate, explicit local-effect grant; it is not provider approval."""
    issued_at = time.time_ns() if now_ns is None else now_ns
    if (
        not _IDENTIFIER.fullmatch(approved_by)
        or not _IDENTIFIER.fullmatch(approval_id)
        or policy.expires_at_ns <= issued_at
        or policy.expires_at_ns - issued_at > 30 * 24 * 60 * 60 * 1_000_000_000
    ):
        _raise("approval")
    manifest = load_workflow_run_manifest(store, run_id)
    artifact = _load_bound_artifact(store, run_id, artifact_id, manifest.digest)
    root = _workspace_root(workspace)
    _require_run_store_outside_workspace(store, root)
    if artifact.content_type != _FILE_CHANGE_TYPE:
        _raise("artifact_type")
    if _git_identity(root) != policy.base_revision:
        _raise("base_revision")
    if _git_status(root) != "":
        _raise("workspace_not_clean")
    value = {
        "approval_id": approval_id,
        "approved_by": approved_by,
        "artifact_id": artifact.artifact_id,
        "artifact_sha256": artifact.content_sha256,
        "issued_at_ns": issued_at,
        "manifest_digest": manifest.digest,
        "policy": policy.model_dump(mode="json"),
        "run_id": run_id,
        "schema_version": _SCHEMA,
        "workspace_sha256": _path_digest(root),
    }
    authorization_id = _digest(_canonical(value))
    authorization = LocalExecutionAuthorization(
        authorization_id,
        run_id,
        manifest.digest,
        artifact.artifact_id,
        artifact.content_sha256,
        _path_digest(root),
        policy,
        approved_by,
        approval_id,
        issued_at,
    )
    _write_immutable(
        _authorization_path(store, run_id, authorization_id),
        _canonical({**value, "authorization_id": authorization_id}),
    )
    return authorization


def load_local_execution_authorization(
    store: WorkflowRunManifestStore, run_id: str, authorization_id: str
) -> LocalExecutionAuthorization:
    """Load one canonical authorization from the Run's existing namespace."""
    if not _SHA256.fullmatch(authorization_id):
        _raise("authorization_id")
    raw = _read_regular(_authorization_path(store, run_id, authorization_id))
    value = _strict_json(raw)
    if not isinstance(value, dict) or value.get("schema_version") != _SCHEMA:
        _raise("authorization_load")
    supplied_id = value.pop("authorization_id", None)
    if (
        supplied_id != authorization_id
        or _digest(_canonical(value)) != authorization_id
    ):
        _raise("authorization_integrity")
    try:
        policy = LocalExecutionPolicy.model_validate_json(
            _canonical(value.pop("policy"))
        )
        authorization = LocalExecutionAuthorization(
            authorization_id=authorization_id,
            run_id=value["run_id"],
            manifest_digest=value["manifest_digest"],
            artifact_id=value["artifact_id"],
            artifact_sha256=value["artifact_sha256"],
            workspace_sha256=value["workspace_sha256"],
            policy=policy,
            approved_by=value["approved_by"],
            approval_id=value["approval_id"],
            issued_at_ns=value["issued_at_ns"],
        )
    except (KeyError, TypeError, ValidationError):
        _raise("authorization_load")
    if value != {
        "approval_id": authorization.approval_id,
        "approved_by": authorization.approved_by,
        "artifact_id": authorization.artifact_id,
        "artifact_sha256": authorization.artifact_sha256,
        "issued_at_ns": authorization.issued_at_ns,
        "manifest_digest": authorization.manifest_digest,
        "run_id": authorization.run_id,
        "schema_version": _SCHEMA,
        "workspace_sha256": authorization.workspace_sha256,
    }:
        _raise("authorization_load")
    return authorization


def execute_local_change(
    store: WorkflowRunManifestStore,
    *,
    run_id: str,
    artifact_id: str,
    authorization_id: str,
    workspace: Path,
    now_ns: int | None = None,
) -> dict[str, object]:
    """Apply one Run-bound change Artifact and run exact commands only in bwrap."""
    authorization = load_local_execution_authorization(store, run_id, authorization_id)
    manifest = load_workflow_run_manifest(store, run_id)
    artifact = _load_bound_artifact(store, run_id, artifact_id, manifest.digest)
    root = _workspace_root(workspace)
    _require_run_store_outside_workspace(store, root)
    now = time.time_ns() if now_ns is None else now_ns
    if (
        now >= authorization.policy.expires_at_ns
        or authorization.run_id != run_id
        or authorization.artifact_id != artifact.artifact_id
        or authorization.artifact_sha256 != artifact.content_sha256
        or authorization.manifest_digest != manifest.digest
        or authorization.workspace_sha256 != _path_digest(root)
    ):
        _raise("authorization_binding")
    if _git_identity(root) != authorization.policy.base_revision:
        _raise("base_revision")
    if _git_status(root) != "":
        _raise("workspace_not_clean")
    changes = _parse_change_set(artifact, authorization.policy)
    try:
        sources = _read_sources(root, authorization.policy)
    except OSError:
        _raise("source_file")
    _validate_change_preimages(changes, sources, authorization.policy)
    bwrap = Path("/usr/bin/bwrap")
    if not bwrap.is_file() or not os.access(bwrap, os.X_OK):
        _raise("sandbox_unavailable")
    tmpfs_root = Path("/dev/shm")
    _require_tmpfs(tmpfs_root)
    attempt_id = str(uuid.uuid4())
    claim = {
        "artifact_id": artifact.artifact_id,
        "artifact_sha256": artifact.content_sha256,
        "attempt_id": attempt_id,
        "authorization_id": authorization_id,
        "created_at_ns": now,
        "manifest_digest": manifest.digest,
        "run_id": run_id,
        "schema_version": "local-execution-attempt.v1",
    }
    claim_path = _attempt_path(store, run_id, authorization_id)
    _write_immutable(claim_path, _canonical(claim))
    try:
        with tempfile.TemporaryDirectory(
            prefix="ai-office-local-", dir=tmpfs_root
        ) as temp:
            input_root = Path(temp) / "input"
            source_root = input_root / "sources"
            source_root.mkdir(mode=0o700, parents=True)
            source_descriptors: list[dict[str, object]] = []
            for relative, (content, mode) in sources.items():
                _write_input_file(source_root, relative, content)
                source_descriptors.append(
                    {"mode": mode, "path": relative, "sha256": _digest(content)}
                )
            plan = {
                "changes": changes,
                "commands": [list(item) for item in authorization.policy.commands],
                "limits": {
                    **authorization.policy.limits.model_dump(mode="json"),
                },
                "sources": source_descriptors,
            }
            _write_private_file(input_root / "plan.json", _canonical(plan))
            _write_private_file(
                input_root / "bootstrap.py",
                Path(__file__).with_name("_local_execution_bootstrap.py").read_bytes(),
            )
            process = _run_sandbox(bwrap, input_root, authorization.policy.limits)
            if process["status"] == "completed":
                result = _parse_sandbox_result(
                    process["stdout"], authorization, artifact
                )
            else:
                result = {
                    "commands": [
                        {
                            "argv": list(argv),
                            "exit_code": None,
                            "status": "unknown",
                            "stderr": "",
                            "stdout": "",
                        }
                        for argv in authorization.policy.commands
                    ],
                    "diff": "",
                    "diff_sha256": _digest(b""),
                    "sandbox_status": process["status"],
                    "status": process["status"],
                    "stderr": _redact(process["stderr"].decode("utf-8", "replace")),
                    "stdout": _redact(process["stdout"].decode("utf-8", "replace")),
                }
    except Exception as error:
        # A durable claim already exists, so no second process attempt is allowed.
        if isinstance(error, LocalExecutionError):
            result = {
                "commands": [],
                "diff": "",
                "diff_sha256": _digest(b""),
                "sandbox_status": error.classification,
                "status": error.classification,
            }
        else:
            result = {
                "commands": [],
                "diff": "",
                "diff_sha256": _digest(b""),
                "sandbox_status": "sandbox_failed",
                "status": "sandbox_failed",
            }
    evidence = {
        **result,
        "artifact_id": artifact.artifact_id,
        "artifact_sha256": artifact.content_sha256,
        "attempt_id": attempt_id,
        "authorization_id": authorization_id,
        "base_revision": authorization.policy.base_revision,
        "created_at_ns": time.time_ns(),
        "manifest_digest": manifest.digest,
        "run_id": run_id,
        "schema_version": _EVIDENCE_SCHEMA,
        "workspace_sha256": authorization.workspace_sha256,
    }
    evidence_bytes = _canonical(evidence)
    if len(evidence_bytes) > _MAX_RESULT_BYTES:
        _raise("evidence_limit")
    _write_immutable(_result_path(store, run_id, authorization_id), evidence_bytes)
    return evidence


def build_local_review_input(
    store: WorkflowRunManifestStore,
    *,
    run_id: str,
    artifact_id: str,
    authorization_id: str,
) -> str:
    """Prepare exact, bounded evidence for a separately configured reviewer Run."""
    authorization = load_local_execution_authorization(store, run_id, authorization_id)
    artifact = _load_bound_artifact(
        store, run_id, artifact_id, authorization.manifest_digest
    )
    if (
        artifact.artifact_id != authorization.artifact_id
        or artifact.content_sha256 != authorization.artifact_sha256
    ):
        _raise("review_artifact_binding")
    evidence = load_local_execution_evidence(store, run_id, authorization_id)
    if evidence.get("status") != "verified":
        _raise("review_requires_verified_candidate")
    value = {
        "applied_candidate": {
            "artifact_id": artifact.artifact_id,
            "artifact_sha256": artifact.content_sha256,
            "content_base64": base64.b64encode(artifact.content).decode("ascii"),
        },
        "local_execution_evidence": evidence,
        "run_input": load_workflow_run_manifest(store, run_id).run_input,
    }
    encoded = _canonical(value)
    if len(encoded) > 1_048_576:
        _raise("review_input_size")
    return encoded.decode("utf-8")


def load_local_execution_evidence(
    store: WorkflowRunManifestStore, run_id: str, authorization_id: str
) -> dict[str, object]:
    """Load exact immutable local-effect evidence for inspection/handoff."""
    authorization = load_local_execution_authorization(store, run_id, authorization_id)
    raw = _read_regular(_result_path(store, run_id, authorization_id))
    value = _strict_json(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != _EVIDENCE_SCHEMA
        or value.get("run_id") != run_id
        or value.get("manifest_digest") != authorization.manifest_digest
        or value.get("artifact_id") != authorization.artifact_id
        or value.get("artifact_sha256") != authorization.artifact_sha256
        or value.get("authorization_id") != authorization_id
        or raw != _canonical(value)
    ):
        _raise("evidence_integrity")
    return value


def _parse_change_set(
    artifact: WorkflowArtifact, policy: LocalExecutionPolicy
) -> list[dict[str, str]]:
    if len(artifact.content) > _MAX_ARTIFACT_BYTES:
        _raise("artifact_size")
    value = _strict_json(artifact.content)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "base_revision", "changes"}
        or value.get("schema_version") != "file-change-set.v1"
        or value.get("base_revision") != policy.base_revision
        or not isinstance(value.get("changes"), list)
        or not value["changes"]
        or len(value["changes"]) > len(policy.write_paths)
    ):
        _raise("change_set")
    changes: list[dict[str, str]] = []
    seen: set[str] = set()
    total_change_bytes = 0
    for item in value["changes"]:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "before_sha256",
            "content_base64",
        }:
            _raise("change_set")
        path = _validate_relative_path(item.get("path"))
        digest = item.get("before_sha256")
        encoded = item.get("content_base64")
        if (
            path in seen
            or path not in policy.write_paths
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
            or not isinstance(encoded, str)
        ):
            _raise("change_set_scope")
        try:
            content = base64.b64decode(encoded, validate=True)
            content.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            _raise("change_set_content")
        if len(content) > policy.limits.max_file_bytes or _SENSITIVE.search(
            content.decode("utf-8")
        ):
            _raise("change_set_secret_or_size")
        total_change_bytes += len(content)
        if total_change_bytes > _MAX_CHANGE_BYTES:
            _raise("change_set_size")
        seen.add(path)
        changes.append(
            {"before_sha256": digest, "content_base64": encoded, "path": path}
        )
    return changes


def _validate_change_preimages(
    changes: list[dict[str, str]],
    sources: dict[str, tuple[bytes, int]],
    policy: LocalExecutionPolicy,
) -> None:
    source_by_path = {path: content for path, (content, _) in sources.items()}
    for change in changes:
        before = source_by_path.get(change["path"])
        if before is None or _digest(before) != change["before_sha256"]:
            _raise("change_preimage")


def _read_sources(
    root: Path, policy: LocalExecutionPolicy
) -> dict[str, tuple[bytes, int]]:
    result: dict[str, tuple[bytes, int]] = {}
    total = 0
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        for relative in policy.read_paths:
            components = relative.split("/")
            directory_fd = os.dup(root_fd)
            try:
                for component in components[:-1]:
                    next_fd = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                    os.close(directory_fd)
                    directory_fd = next_fd
                descriptor = os.open(
                    components[-1],
                    os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                with os.fdopen(descriptor, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(before.st_mode)
                        or before.st_nlink != 1
                        or before.st_size > policy.limits.max_file_bytes
                    ):
                        _raise("source_file")
                    mode = stat.S_IMODE(before.st_mode)
                    if mode & 0o7000:
                        _raise("source_mode")
                    content = stream.read(policy.limits.max_file_bytes + 1)
                    after = os.fstat(stream.fileno())
                if len(content) != before.st_size or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    _raise("source_changed")
                text = content.decode("utf-8")
                if _SENSITIVE.search(text):
                    _raise("source_secret_like")
                total += len(content)
                if total > _MAX_SOURCE_BYTES:
                    _raise("source_size")
                result[relative] = (content, mode)
            finally:
                os.close(directory_fd)
    finally:
        os.close(root_fd)
    return result


def _run_sandbox(
    bwrap: Path, input_root: Path, limits: LocalExecutionLimits
) -> dict[str, object]:
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
        "--size",
        str(limits.workspace_bytes),
        "--tmpfs",
        "/workspace",
        "--size",
        str(min(limits.workspace_bytes // 4, 16_777_216)),
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(input_root),
        "/input",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin",
        "--setenv",
        "HOME",
        "/nonexistent",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--chdir",
        "/workspace",
        "--",
        "/usr/bin/python3",
        "-B",
        "/input/bootstrap.py",
    ]

    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={},
        close_fds=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=limits.wall_timeout_seconds + 5)
        status = "completed" if process.returncode == 0 else "sandbox_failed"
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        status = "sandbox_timeout"
    if len(stdout) > _MAX_RESULT_BYTES or len(stderr) > 65_536:
        return {"status": "sandbox_output_limit", "stdout": b"", "stderr": b""}
    return {"status": status, "stdout": stdout, "stderr": stderr}


def _parse_sandbox_result(
    output: bytes,
    authorization: LocalExecutionAuthorization,
    artifact: WorkflowArtifact,
) -> dict[str, object]:
    value = _strict_json(output)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "local-execution-result.v1"
        or value.get("status")
        not in {
            "verified",
            "command_failed",
            "timeout",
            "output_limit",
            "workspace_changed_by_validation",
            "unexpected_file_set",
            "sandbox_setup_failed",
        }
    ):
        _raise("sandbox_result")
    if value.get("network_namespace") == _host_network_namespace():
        _raise("network_isolation")
    diff = value.get("diff")
    if not isinstance(diff, str) or len(diff.encode("utf-8")) > _MAX_RESULT_BYTES:
        _raise("sandbox_result_size")
    if value.get("diff_sha256") != _digest(diff.encode("utf-8")):
        _raise("sandbox_result_integrity")
    command_results = value.get("commands")
    if not isinstance(command_results, list):
        _raise("sandbox_result_commands")
    if len(command_results) != len(authorization.policy.commands):
        _raise("sandbox_result_commands")
    for item, authorized in zip(
        command_results, authorization.policy.commands, strict=True
    ):
        if not isinstance(item, dict) or tuple(item.get("argv", ())) != authorized:
            _raise("sandbox_result_commands")
        if item.get("status") not in {
            "completed",
            "timeout",
            "output_limit",
            "command_failed",
            "workspace_changed_by_validation",
            "not_started",
        }:
            _raise("sandbox_result_commands")
        if item.get("status") == "completed" and (
            type(item.get("exit_code")) is not int or item["exit_code"] != 0
        ):
            _raise("sandbox_result_commands")
        for key in ("stdout_base64", "stderr_base64"):
            try:
                decoded = base64.b64decode(item.get(key, ""), validate=True)
            except (binascii.Error, TypeError):
                _raise("sandbox_result_commands")
            item[key.removesuffix("_base64")] = _redact(
                decoded.decode("utf-8", "replace")
            )
            del item[key]
    if value["status"] == "verified" and any(
        item["status"] != "completed" for item in command_results
    ):
        _raise("sandbox_result_commands")
    value.update(
        {
            "artifact_id": artifact.artifact_id,
            "artifact_sha256": artifact.content_sha256,
            "authorization_id": authorization.authorization_id,
            "sandbox_status": value["status"],
        }
    )
    return value


def _load_bound_artifact(
    store: WorkflowRunManifestStore,
    run_id: str,
    artifact_id: str,
    manifest_digest: str,
) -> WorkflowArtifact:
    try:
        artifact = read_run_artifact(store, run_id, artifact_id)
    except Exception:
        _raise("artifact_unavailable")
    if artifact.manifest_digest != manifest_digest or artifact.run_id != run_id:
        _raise("artifact_binding")
    return artifact


def _workspace_root(workspace: Path) -> Path:
    if type(workspace) is not _PATH_TYPE or workspace.is_symlink():
        _raise("workspace_path")
    try:
        root = workspace.resolve(strict=True)
        mode = root.stat().st_mode
    except OSError:
        _raise("workspace_path")
    marker = root / ".git"
    if not stat.S_ISDIR(mode) or marker.is_symlink():
        _raise("workspace_not_repository")
    if marker.is_dir():
        return root
    if not marker.is_file() or marker.stat().st_size > 1024:
        _raise("workspace_not_repository")
    try:
        contents = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        _raise("workspace_not_repository")
    if not contents.startswith("gitdir: ") or not contents[8:]:
        _raise("workspace_not_repository")
    return root


def _require_run_store_outside_workspace(
    store: WorkflowRunManifestStore, workspace: Path
) -> None:
    try:
        store_root = store.root.resolve(strict=True)
    except (OSError, RuntimeError):
        _raise("run_store")
    if store_root.is_relative_to(workspace):
        _raise("run_store_inside_workspace")


def _git_identity(root: Path) -> str:
    result = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    revision = result.decode("ascii", "strict").strip()
    if not _REVISION.fullmatch(revision):
        _raise("base_revision")
    return revision


def _git_status(root: Path) -> str:
    return _git(root, "status", "--porcelain=v1", "--untracked-files=all").decode(
        "utf-8", "strict"
    )


def _git(root: Path, *arguments: str) -> bytes:
    bwrap = Path("/usr/bin/bwrap")
    git = Path("/usr/bin/git")
    prlimit = Path("/usr/bin/prlimit")
    if (
        not bwrap.is_file()
        or not os.access(bwrap, os.X_OK)
        or not git.is_file()
        or not os.access(git, os.X_OK)
        or not prlimit.is_file()
        or not os.access(prlimit, os.X_OK)
    ):
        _raise("sandbox_unavailable")
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
        "--size",
        "16777216",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(root),
        "/repository",
    ]
    metadata_roots = _external_git_metadata_roots(root)
    existing_mounts = {
        Path("/bin"),
        Path("/dev"),
        Path("/lib"),
        Path("/lib64"),
        Path("/proc"),
        Path("/repository"),
        Path("/tmp"),
        Path("/usr"),
    }
    metadata_directories = sorted(
        {
            parent
            for metadata_root in metadata_roots
            for parent in metadata_root.parents
            if parent != Path("/") and parent not in existing_mounts
        },
        key=lambda path: (len(path.parts), path.as_posix()),
    )
    for directory in metadata_directories:
        argv.extend(("--dir", str(directory)))
    for metadata_root in metadata_roots:
        argv.extend(("--ro-bind", str(metadata_root), str(metadata_root)))
    argv.extend(
        [
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin",
            "--setenv",
            "HOME",
            "/nonexistent",
            "--setenv",
            "GIT_CONFIG_GLOBAL",
            "/dev/null",
            "--setenv",
            "GIT_CONFIG_NOSYSTEM",
            "1",
            "--setenv",
            "GIT_OPTIONAL_LOCKS",
            "0",
            "--setenv",
            "GIT_TERMINAL_PROMPT",
            "0",
            "--setenv",
            "GIT_ASKPASS",
            "/bin/false",
            "--setenv",
            "GIT_CONFIG_COUNT",
            "2",
            "--setenv",
            "GIT_CONFIG_KEY_0",
            "core.fsmonitor",
            "--setenv",
            "GIT_CONFIG_VALUE_0",
            "false",
            "--setenv",
            "GIT_CONFIG_KEY_1",
            "core.hooksPath",
            "--setenv",
            "GIT_CONFIG_VALUE_1",
            "/dev/null",
            "--chdir",
            "/repository",
            "--",
            str(prlimit),
            "--cpu=5",
            "--as=536870912",
            "--fsize=16777216",
            "--nproc=128",
            "--nofile=64",
            "--core=0",
            "--",
            str(git),
            "-C",
            "/repository",
            *arguments,
        ]
    )
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},
            shell=False,
            start_new_session=True,
        )
    except OSError:
        _raise("git_inspection")
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + 5
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_group(process)
                _raise("git_timeout")
            events = selector.select(min(remaining, 0.1))
            if process.poll() is not None and not events:
                continue
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 65_537 - len(output))
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                output.extend(chunk)
                if len(output) > 65_536:
                    _terminate_process_group(process)
                    _raise("git_output_limit")
        return_code = process.wait()
    except (OSError, subprocess.SubprocessError):
        _terminate_process_group(process)
        _raise("git_inspection")
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_process_group(process)
    if return_code != 0:
        _raise("git_inspection")
    return bytes(output)


def _external_git_metadata_roots(root: Path) -> tuple[Path, ...]:
    marker = root / ".git"
    if marker.is_dir():
        return ()
    contents = marker.read_text(encoding="utf-8").strip()
    git_dir = Path(contents[8:])
    if not git_dir.is_absolute():
        git_dir = marker.parent / git_dir
    resolved_git_dir = git_dir.resolve(strict=True)
    if Path(os.path.abspath(git_dir)) != resolved_git_dir:
        _raise("workspace_git_metadata")
    git_dir = resolved_git_dir
    if not git_dir.is_dir():
        _raise("workspace_git_metadata")
    common_dir_file = git_dir / "commondir"
    if not common_dir_file.exists():
        return (git_dir,)
    if common_dir_file.is_symlink() or common_dir_file.stat().st_size > 1024:
        _raise("workspace_git_metadata")
    common_dir = Path(common_dir_file.read_text(encoding="utf-8").strip())
    if not common_dir.is_absolute():
        common_dir = git_dir / common_dir
    resolved_common_dir = common_dir.resolve(strict=True)
    if Path(os.path.abspath(common_dir)) != resolved_common_dir:
        _raise("workspace_git_metadata")
    common_dir = resolved_common_dir
    if not common_dir.is_dir():
        _raise("workspace_git_metadata")
    candidates = tuple(
        path
        for path in dict.fromkeys((git_dir, common_dir))
        if not path.is_relative_to(root)
    )
    return tuple(
        path
        for path in candidates
        if not any(path != other and path.is_relative_to(other) for other in candidates)
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _require_tmpfs(path: Path) -> None:
    try:
        entries = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        _raise("tmpfs_unavailable")
    mounts: list[tuple[int, str]] = []
    for entry in entries:
        fields = entry.split()
        if len(fields) >= 3:
            mountpoint = fields[1].replace("\\040", " ")
            if path == Path(mountpoint) or path.is_relative_to(Path(mountpoint)):
                mounts.append((len(mountpoint), fields[2]))
    if not mounts or max(mounts)[1] != "tmpfs":
        _raise("tmpfs_unavailable")


def _write_input_file(root: Path, relative: str, content: bytes) -> None:
    destination = root.joinpath(*relative.split("/"))
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_private_file(destination, content)


def _write_private_file(path: Path, contents: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o400,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())


def _write_immutable(path: Path, contents: bytes) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        _raise("already_used_or_exists")
    except OSError:
        _raise("persistence")


def _read_regular(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > _MAX_RESULT_BYTES
            ):
                _raise("record_size_or_type")
            return stream.read(_MAX_RESULT_BYTES + 1)
    except (OSError, ValueError):
        _raise("record_unavailable")


def _strict_json(contents: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(contents, object_pairs_hook=pairs)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        _raise("invalid_json")
    if _canonical(value) != contents:
        _raise("noncanonical_json")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _validate_paths(paths: tuple[str, ...]) -> None:
    for path in paths:
        _validate_relative_path(path)


def _validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        _raise("path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        _raise("path")
    parts = path.parts
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or parts[0] == ".git"
    ):
        _raise("path")
    return value


def _authorization_path(
    store: WorkflowRunManifestStore, run_id: str, authorization_id: str
) -> Path:
    if not _SHA256.fullmatch(authorization_id):
        _raise("authorization_id")
    store.manifest_path(run_id)
    return store.root / f"{run_id}.local-effect-authorization.{authorization_id}.json"


def _attempt_path(
    store: WorkflowRunManifestStore, run_id: str, authorization_id: str
) -> Path:
    store.manifest_path(run_id)
    return store.root / f"{run_id}.local-effect-attempt.{authorization_id}.json"


def _result_path(
    store: WorkflowRunManifestStore, run_id: str, authorization_id: str
) -> Path:
    store.manifest_path(run_id)
    return store.root / f"{run_id}.local-effect-result.{authorization_id}.json"


def _path_digest(path: Path) -> str:
    identity = path.stat()
    return _digest(
        b"ai-office-local-workspace-v1\x00"
        + os.fsencode(path)
        + identity.st_dev.to_bytes(16, "big")
        + identity.st_ino.to_bytes(16, "big")
    )


def _host_network_namespace() -> str:
    try:
        return Path("/proc/self/ns/net").readlink().as_posix()
    except OSError:
        _raise("network_isolation")


def _redact(value: str) -> str:
    value = _ASSIGNMENT.sub(r"\1[REDACTED]", value)
    return _SENSITIVE.sub("[REDACTED]", value)


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _raise(classification: str) -> NoReturn:
    raise LocalExecutionError(classification) from None
