"""Run-bound, one-use file-change verification in a fail-closed Linux sandbox."""

from __future__ import annotations

import ast
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
_SECRET_NAME = (
    r"[A-Z0-9_-]*(?:API[-_]?KEY|ACCESS[-_]?TOKEN|"
    r"SECRET(?:[-_]?ACCESS[-_]?KEY)?|PASSWORD|TOKEN)"
)
_MAX_ARTIFACT_BYTES = 1_048_576
_MAX_SOURCE_BYTES = 2_097_152
_MAX_CHANGE_BYTES = 1_048_576
_MAX_RESULT_BYTES = 8_388_608
_MAX_TRUSTED_MANIFEST_BYTES = 1_048_576
_MAX_TRUSTED_FILE_BYTES = 16_777_216
_MAX_TRUSTED_FILES = 1024
_MAX_TRUSTED_DIRECTORIES = 4096
_MAX_TRUSTED_TOTAL_BYTES = 33_554_432
_PATH_TYPE = type(Path())
FILE_CHANGE_SET_CONTENT_TYPE = "application/vnd.ai-office.file-change-set+json"


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
    trusted_bundle_path: str | None = None
    trusted_bundle_manifest_sha256: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> LocalExecutionPolicy:
        if not _REVISION.fullmatch(self.base_revision):
            raise ValueError("invalid base revision")
        _validate_paths(self.read_paths)
        _validate_paths(self.write_paths)
        if not set(self.write_paths) <= set(self.read_paths):
            raise ValueError("write paths must be readable")
        if (self.trusted_bundle_path is None) != (
            self.trusted_bundle_manifest_sha256 is None
        ):
            raise ValueError("trusted bundle path and digest must be supplied together")
        if self.trusted_bundle_path is not None:
            bundle_path = Path(self.trusted_bundle_path)
            if (
                not bundle_path.is_absolute()
                or os.path.abspath(bundle_path) != self.trusted_bundle_path
                or not _SHA256.fullmatch(self.trusted_bundle_manifest_sha256 or "")
            ):
                raise ValueError("trusted bundle path or digest is invalid")
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
            if self.trusted_bundle_path is not None and (
                argv[0] != "/usr/bin/python3" or argv[1:3] != ("-S", "-B")
            ):
                raise ValueError("trusted bundle commands must use python3 -S -B")
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


@dataclass(frozen=True)
class _TrustedBundle:
    manifest_sha256: str
    manifest_bytes: bytes
    files: dict[str, bytes]


def _load_trusted_bundle(
    bundle_path: str,
    expected_manifest_sha256: str,
    *,
    workspace: Path,
    run_store: Path | None = None,
) -> _TrustedBundle:
    """Read an operator-pinned offline runtime/source/oracle bundle exactly."""
    path = Path(bundle_path)
    try:
        root = path.resolve(strict=True)
        if (
            path != root
            or not root.is_dir()
            or root.is_relative_to(workspace)
            or workspace.is_relative_to(root)
            or (
                run_store is not None
                and (root.is_relative_to(run_store) or run_store.is_relative_to(root))
            )
        ):
            _raise("trusted_bundle_path")
        actual_files, actual_directories = _trusted_bundle_tree(root)
        if "manifest.json" not in actual_files:
            _raise("trusted_bundle_manifest")
        manifest_bytes = _read_bundle_file(
            root, "manifest.json", _MAX_TRUSTED_MANIFEST_BYTES
        )
        if _digest(manifest_bytes) != expected_manifest_sha256:
            _raise("trusted_bundle_digest")
        manifest = _strict_json(manifest_bytes)
        if (
            type(manifest) is not dict
            or set(manifest)
            != {"schema_version", "python_cache_tag", "python_platform", "files"}
            or manifest.get("schema_version") != "local-execution-trusted-bundle.v1"
            or manifest_bytes != _canonical(manifest)
            or type(manifest.get("python_cache_tag")) is not str
            or type(manifest.get("python_platform")) is not str
            or type(manifest.get("files")) is not list
            or not 1 <= len(manifest["files"]) <= _MAX_TRUSTED_FILES
        ):
            _raise("trusted_bundle_manifest")
        if _sandbox_python_abi() != {
            "python_cache_tag": manifest["python_cache_tag"],
            "python_platform": manifest["python_platform"],
        }:
            _raise("trusted_bundle_abi")
        file_entries: list[dict[str, object]] = []
        expected_files = {"manifest.json"}
        expected_directories: set[str] = set()
        total = 0
        required_roots = {"oracle", "site-packages", "source"}
        present_roots: set[str] = set()
        for item in manifest["files"]:
            if (
                type(item) is not dict
                or set(item) != {"path", "sha256", "size"}
                or type(item["path"]) is not str
                or type(item["sha256"]) is not str
                or type(item["size"]) is not int
            ):
                _raise("trusted_bundle_manifest")
            relative = _validate_relative_path(item["path"])
            digest = item["sha256"]
            size = item["size"]
            first = relative.partition("/")[0]
            if (
                first not in required_roots
                or not relative.startswith(f"{first}/")
                or not _SHA256.fullmatch(digest)
                or not 0 <= size <= _MAX_TRUSTED_FILE_BYTES
                or relative in expected_files
            ):
                _raise("trusted_bundle_manifest")
            present_roots.add(first)
            total += size
            if total > _MAX_TRUSTED_TOTAL_BYTES:
                _raise("trusted_bundle_size")
            expected_files.add(relative)
            parent = PurePosixPath(relative).parent
            while str(parent) != ".":
                expected_directories.add(parent.as_posix())
                parent = parent.parent
            file_entries.append(item)
        if (
            present_roots != required_roots
            or file_entries != sorted(file_entries, key=lambda item: item["path"])
            or actual_files != expected_files
            or actual_directories != expected_directories
        ):
            _raise("trusted_bundle_file_set")
        files: dict[str, bytes] = {}
        for item in file_entries:
            relative = item["path"]
            content = _read_bundle_file(root, relative, _MAX_TRUSTED_FILE_BYTES)
            if len(content) != item["size"] or _digest(content) != item["sha256"]:
                _raise("trusted_bundle_file_digest")
            files[relative] = content
        return _TrustedBundle(expected_manifest_sha256, manifest_bytes, files)
    except LocalExecutionError:
        raise
    except (OSError, RuntimeError, UnicodeError, ValueError):
        _raise("trusted_bundle_invalid")


def _sandbox_python_abi() -> dict[str, str]:
    """Ask only the fixed sandbox interpreter for its cache tag/platform."""
    code = (
        "import json,sys,sysconfig;sys.stdout.write(json.dumps({"
        "'python_cache_tag':sys.implementation.cache_tag,"
        "'python_platform':sysconfig.get_platform()},sort_keys=True,"
        "separators=(',',':')))"
    )
    try:
        result = subprocess.run(
            ["/usr/bin/python3", "-S", "-B", "-c", code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin"},
            shell=False,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _raise("sandbox_interpreter")
    if result.returncode != 0 or len(result.stdout) > 1024:
        _raise("sandbox_interpreter")
    value = _strict_json(result.stdout)
    if (
        type(value) is not dict
        or set(value) != {"python_cache_tag", "python_platform"}
        or not all(type(item) is str and item for item in value.values())
    ):
        _raise("sandbox_interpreter")
    return value


def _trusted_bundle_tree(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in dirnames:
            child = current / name
            if child.is_symlink() or not child.is_dir():
                _raise("trusted_bundle_file_type")
            directories.add(child.relative_to(root).as_posix())
            if len(directories) > _MAX_TRUSTED_DIRECTORIES:
                _raise("trusted_bundle_directory_count")
        for name in filenames:
            child = current / name
            try:
                metadata = child.lstat()
            except OSError:
                _raise("trusted_bundle_file_type")
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > _MAX_TRUSTED_FILE_BYTES
            ):
                _raise("trusted_bundle_file_type")
            files.add(child.relative_to(root).as_posix())
            if len(files) > _MAX_TRUSTED_FILES + 1:
                _raise("trusted_bundle_file_count")
    return files, directories


def _read_bundle_file(root: Path, relative: str, limit: int) -> bytes:
    components = _validate_relative_path(relative).split("/")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    directory_fd = root_fd
    try:
        for component in components[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            if directory_fd != root_fd:
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
                or before.st_size > limit
            ):
                _raise("trusted_bundle_file_type")
            content = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
        if len(content) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            _raise("trusted_bundle_file_changed")
        return content
    finally:
        if directory_fd != root_fd:
            os.close(directory_fd)
        os.close(root_fd)


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
    validate_local_execution_scope(
        _fixed_run_input_for_effect(manifest.run_input), root, policy
    )
    if policy.trusted_bundle_path is not None:
        _load_trusted_bundle(
            policy.trusted_bundle_path,
            policy.trusted_bundle_manifest_sha256 or "",
            workspace=root,
            run_store=store.root.resolve(strict=True),
        )
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


def validate_local_execution_scope(
    run_input: str, workspace: Path, policy: LocalExecutionPolicy
) -> Path:
    """Read-only preflight tying a proposed local grant to its fixed Run Input."""
    root = _workspace_root(workspace)
    _validate_run_input_scope(run_input, root, policy)
    if _git_identity(root) != policy.base_revision:
        _raise("base_revision")
    if _git_status(root) != "":
        _raise("workspace_not_clean")
    return root


def _fixed_run_input_for_effect(run_input: str) -> str:
    """Unwrap the fixed assignment from a bounded review/revision handoff."""
    try:
        value = json.loads(run_input)
    except (json.JSONDecodeError, TypeError):
        return run_input
    if (
        type(value) is dict
        and type(value.get("assignment")) is str
        and type(value.get("source_artifact")) is dict
    ):
        return value["assignment"]
    return run_input


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
    fixed_run_input = _fixed_run_input_for_effect(manifest.run_input)
    validate_local_execution_scope(fixed_run_input, root, authorization.policy)
    changes = _parse_change_set(artifact, authorization.policy)
    try:
        sources = _read_sources(root, authorization.policy)
    except OSError:
        _raise("source_file")
    _validate_run_input_scope(
        fixed_run_input, root, authorization.policy, sources=sources
    )
    _validate_change_preimages(changes, sources, authorization.policy)
    trusted_bundle = None
    if authorization.policy.trusted_bundle_path is not None:
        trusted_bundle = _load_trusted_bundle(
            authorization.policy.trusted_bundle_path,
            authorization.policy.trusted_bundle_manifest_sha256 or "",
            workspace=root,
            run_store=store.root.resolve(strict=True),
        )
    bwrap = Path("/usr/bin/bwrap")
    if not bwrap.is_file() or not os.access(bwrap, os.X_OK):
        _raise("sandbox_unavailable")
    tmpfs_root = Path("/dev/shm")
    _require_tmpfs(tmpfs_root)
    if time.time_ns() >= authorization.policy.expires_at_ns:
        _raise("authorization_expired")
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
            if trusted_bundle is not None:
                trusted_root = input_root / "trusted"
                trusted_root.mkdir(mode=0o700)
                _write_private_file(
                    trusted_root / "manifest.json", trusted_bundle.manifest_bytes
                )
                for relative, content in trusted_bundle.files.items():
                    _write_input_file(trusted_root, relative, content)
            plan = {
                "changes": changes,
                "commands": [list(item) for item in authorization.policy.commands],
                "limits": authorization.policy.limits.model_dump(mode="json"),
                "sources": source_descriptors,
                "trusted_bundle_manifest_sha256": (
                    trusted_bundle.manifest_sha256 if trusted_bundle else None
                ),
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
        "trusted_bundle_manifest_sha256": (
            trusted_bundle.manifest_sha256 if trusted_bundle else None
        ),
        "workspace_sha256": authorization.workspace_sha256,
    }
    evidence_bytes = _canonical(evidence)
    if len(evidence_bytes) > _MAX_RESULT_BYTES:
        _raise("evidence_limit")
    _write_immutable(_result_path(store, run_id, authorization_id), evidence_bytes)
    return evidence


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
        or value.get("trusted_bundle_manifest_sha256")
        != authorization.policy.trusted_bundle_manifest_sha256
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
        if len(content) > policy.limits.max_file_bytes or _source_contains_secret(
            path, content.decode("utf-8")
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
                if _source_contains_secret(relative, text):
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


def _source_contains_secret(relative: str, text: str) -> bool:
    """Keep literal secret detection while allowing Python keyword passthroughs."""
    if PurePosixPath(relative).suffix != ".py":
        return _SENSITIVE.search(text) is not None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _SENSITIVE.search(text) is not None
    content = bytearray(text.encode("utf-8"))
    line_offsets = [0]
    for line in content.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.keyword)
            or node.arg is None
            or re.fullmatch(_SECRET_NAME, node.arg, re.IGNORECASE) is None
        ):
            continue
        expression = tuple(ast.walk(node.value))
        if (
            not any(
                isinstance(child, ast.Name)
                and child.id.casefold() == node.arg.casefold()
                for child in expression
            )
            or any(isinstance(child, ast.Constant) for child in expression)
            or node.end_lineno is None
        ):
            continue
        start = line_offsets[node.lineno - 1] + node.col_offset
        end = line_offsets[node.end_lineno - 1] + node.end_col_offset
        for index in range(start, end):
            if content[index] not in (0x0A, 0x0D):
                content[index] = 0x20
    return _SENSITIVE.search(content.decode("utf-8")) is not None


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
        "-S",
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
    status, stdout, stderr = _capture_bounded_output(
        process,
        timeout_seconds=limits.wall_timeout_seconds + 5,
        stdout_limit=_MAX_RESULT_BYTES,
        stderr_limit=65_536,
    )
    if status == "completed" and process.returncode != 0:
        status = "sandbox_failed"
    return {"status": status, "stdout": stdout, "stderr": stderr}


def _capture_bounded_output(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: int,
    stdout_limit: int,
    stderr_limit: int,
) -> tuple[str, bytes, bytes]:
    """Capture fixed-size output and kill the process group on overflow/timeout."""
    streams = {
        process.stdout: (bytearray(), stdout_limit),
        process.stderr: (bytearray(), stderr_limit),
    }
    if any(stream is None for stream in streams):
        _terminate_process_group(process)
        _raise("sandbox_output_pipe")
    selector = selectors.DefaultSelector()
    outputs = list(streams.items())
    try:
        for stream, _ in outputs:
            assert stream is not None
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_seconds
        status = "completed"
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status = "sandbox_timeout"
                _terminate_process_group(process)
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                output, limit = streams[stream]
                chunk = os.read(stream.fileno(), min(65_536, limit - len(output) + 1))
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                if len(output) + len(chunk) > limit:
                    status = "sandbox_output_limit"
                    _terminate_process_group(process)
                    break
                output.extend(chunk)
            if status != "completed":
                break
        if status != "completed":
            for stream, _ in outputs:
                if stream is not None and not stream.closed:
                    stream.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            process.wait()
        return (
            status,
            bytes(streams[process.stdout][0]) if status == "completed" else b"",
            bytes(streams[process.stderr][0]) if status == "completed" else b"",
        )
    except OSError:
        _terminate_process_group(process)
        _raise("sandbox_output_capture")
    finally:
        selector.close()
        for stream, _ in outputs:
            if stream is not None and not stream.closed:
                stream.close()


def _parse_sandbox_result(
    output: bytes,
    authorization: LocalExecutionAuthorization,
    artifact: WorkflowArtifact,
) -> dict[str, object]:
    value = _strict_json(output)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "local-execution-result.v1"
        or value.get("trusted_bundle_manifest_sha256")
        != authorization.policy.trusted_bundle_manifest_sha256
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


def _validate_run_input_scope(
    run_input: str,
    root: Path,
    policy: LocalExecutionPolicy,
    *,
    sources: dict[str, tuple[bytes, int]] | None = None,
) -> None:
    """Require local authority and source bytes to stay inside fixed Run Input."""
    def unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(run_input, object_pairs_hook=unique_object)
        repository = value["repository"]
        allowed = value["allowed_paths"]
        source_context = value["source_context"]
        if (
            type(value) is not dict
            or type(repository) is not dict
            or type(allowed) is not dict
            or type(source_context) is not dict
            or repository.get("path") != str(root)
            or repository.get("base_revision") != policy.base_revision
            or type(allowed.get("read")) is not list
            or type(allowed.get("write")) is not list
            or not all(
                type(path) is str
                for path in (*allowed["read"], *allowed["write"])
            )
            or not set(policy.read_paths) <= set(allowed["read"])
            or not set(policy.write_paths) <= set(allowed["write"])
            or not set(policy.read_paths) <= set(source_context)
            or any(type(source_context[path]) is not str for path in policy.read_paths)
        ):
            _raise("run_input_scope")
        for paths in (allowed["read"], allowed["write"]):
            for path in paths:
                _validate_relative_path(path)
    except (KeyError, TypeError, json.JSONDecodeError, ValueError):
        _raise("run_input_scope")
    try:
        actual_sources = sources if sources is not None else _read_sources(root, policy)
        if any(
            source_context[path] != content.decode("utf-8")
            for path, (content, _) in actual_sources.items()
        ):
            _raise("run_input_source_context")
    except (OSError, UnicodeDecodeError):
        _raise("run_input_source_context")


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
