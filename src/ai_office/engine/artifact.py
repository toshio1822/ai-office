"""Immutable Run-bound business Artifacts derived from execution evidence."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifest,
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)
from ai_office.execution_evidence import (
    ExecutionAttemptEvidence,
    NormalizedExecutionResultEvidence,
    _validate_run_terminal_event,
    load_execution_attempt_evidence,
    load_normalized_result_evidence,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowRunBinding, binding_of
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
)

_ARTIFACT_SCHEMA = "workflow-artifact.v1"
_ARTIFACT_PREFIX = "artifact"
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE_PATTERN = re.compile(
    r"^[!#$%&'+.^_`|~%0-9A-Za-z-]+/[!#$%&'+.^_`|~%0-9A-Za-z-]+$"
)
_ARTIFACT_KEYS = frozenset(
    {
        "artifact_id",
        "content_base64",
        "content_length",
        "content_sha256",
        "content_type",
        "employee_id",
        "execution_attempt_evidence_sha256",
        "execution_attempt_id",
        "manifest_digest",
        "normalized_result_evidence_sha256",
        "raw_response_body_sha256",
        "raw_response_evidence_sha256",
        "run_id",
        "schema_version",
        "step_id",
        "step_index",
        "terminal_success_event_sha256",
        "workflow_id",
    }
)
_PATH_TYPE = type(Path())


class WorkflowArtifactError(ValueError):
    """Base error for safe Artifact operations."""

    def __init__(self, classification: str = "contract") -> None:
        super().__init__("workflow Artifact operation failed")
        self.classification = classification


class WorkflowArtifactPersistenceError(WorkflowArtifactError):
    """An Artifact write failed or its durable completion is ambiguous."""


class WorkflowArtifactConflictError(WorkflowArtifactPersistenceError):
    """An immutable Artifact identity already contains different data."""


class WorkflowArtifactLoadError(WorkflowArtifactError):
    """An Artifact or its authoritative lineage is invalid or unavailable."""


class WorkflowArtifactExportError(WorkflowArtifactError):
    """A local Artifact export failed or its completion is ambiguous."""


class WorkflowArtifactExportConflictError(WorkflowArtifactExportError):
    """The selected export destination already exists."""


@dataclass(frozen=True)
class WorkflowArtifact:
    """One immutable business result and its exact successful provenance."""

    schema_version: Literal["workflow-artifact.v1"]
    artifact_id: str
    run_id: str
    manifest_digest: str
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    content_type: str
    content: bytes
    content_sha256: str
    content_length: int
    execution_attempt_id: str
    execution_attempt_evidence_sha256: str
    normalized_result_evidence_sha256: str
    raw_response_evidence_sha256: str | None
    raw_response_body_sha256: str | None
    terminal_success_event_sha256: str

    def __post_init__(self) -> None:
        _validate_artifact(self)


@dataclass(frozen=True)
class WorkflowArtifactExportReceipt:
    """Safe metadata for one completed local copy."""

    artifact_id: str
    destination: Path
    content_sha256: str
    content_length: int


def workflow_artifact_path(
    store: WorkflowRunManifestStore, run_id: str, artifact_id: str
) -> Path:
    """Return the identity-derived immutable target in one Run namespace."""
    _validate_store(store)
    _validate_run_id(run_id)
    _validate_sha256(artifact_id, "artifact_id")
    return store.root / f"{run_id}.{_ARTIFACT_PREFIX}.{artifact_id}.json"


def list_run_artifacts(
    store: WorkflowRunManifestStore, run_id: str
) -> tuple[WorkflowArtifact, ...]:
    """Load and verify every durable Artifact for one exact Run."""
    manifest = _load_manifest(store, run_id)
    artifacts = _load_artifact_records(store, run_id)
    if not artifacts:
        return ()
    history = _load_run_history(store, run_id)
    for artifact in artifacts:
        _validate_artifact_lineage(store, manifest, history, artifact)
    return artifacts


def read_run_artifact(
    store: WorkflowRunManifestStore, run_id: str, artifact_id: str
) -> WorkflowArtifact:
    """Read one Artifact after verifying its current Run evidence lineage."""
    _validate_sha256(artifact_id, "artifact_id", load=True)
    for artifact in list_run_artifacts(store, run_id):
        if artifact.artifact_id == artifact_id:
            return artifact
    _raise_load("missing")


def export_run_artifact(
    store: WorkflowRunManifestStore,
    run_id: str,
    artifact_id: str,
    destination: Path,
) -> WorkflowArtifactExportReceipt:
    """Copy verified Artifact bytes locally without replacing an existing file."""
    artifact = read_run_artifact(store, run_id, artifact_id)
    _export_bytes(destination, artifact)
    return WorkflowArtifactExportReceipt(
        artifact_id=artifact.artifact_id,
        destination=destination,
        content_sha256=artifact.content_sha256,
        content_length=artifact.content_length,
    )


def _ensure_required_artifacts_for_history(
    store: WorkflowRunManifestStore,
    binding: WorkflowRunBinding,
    history: LoadedWorkflowExecutionHistory,
) -> tuple[WorkflowArtifact, ...]:
    """Complete explicitly required Artifacts before persisted success routes."""
    _validate_store(store)
    if type(binding) is not WorkflowRunBinding:
        _raise_load("binding")
    if (
        type(history) is not LoadedWorkflowExecutionHistory
        or binding_of(history.state) != binding
        or history.state.workflow_id == ""
    ):
        _raise_load("history")
    manifest = _load_manifest(store, binding.run_id)
    if (
        manifest.digest != binding.manifest_digest
        or history.state.workflow_id != manifest.workflow_id
    ):
        _raise_load("manifest_binding")
    policy_by_step = {
        step.id: (index, step)
        for index, step in enumerate(manifest.workflow_snapshot.steps, start=1)
    }
    artifacts = _load_artifact_records(store, binding.run_id)
    has_artifact_policy = any(
        step.artifact_content_type is not None for _, step in policy_by_step.values()
    )
    if not has_artifact_policy:
        if artifacts:
            _raise_load("artifact_policy")
        return ()

    existing_by_id = {artifact.artifact_id: artifact for artifact in artifacts}
    for artifact in artifacts:
        _validate_artifact_lineage(store, manifest, history, artifact)

    for event in history.events:
        if event.event_type != "step_succeeded":
            continue
        selected = policy_by_step.get(event.step_id)
        if selected is None or selected[1].artifact_content_type is None:
            continue
        artifact = _artifact_from_success_event(store, manifest, event)
        existing = existing_by_id.get(artifact.artifact_id)
        if existing is not None:
            if existing != artifact:
                _raise_load("identity_conflict")
            continue
        _persist_workflow_artifact(store, artifact)
        existing_by_id[artifact.artifact_id] = artifact

    result = tuple(sorted(existing_by_id.values(), key=lambda item: item.artifact_id))
    for artifact in result:
        _validate_artifact_lineage(store, manifest, history, artifact)
    return result


def _artifact_from_success_event(
    store: WorkflowRunManifestStore,
    manifest: WorkflowRunManifest,
    event: RuntimeStepEvent,
) -> WorkflowArtifact:
    binding = WorkflowRunBinding(manifest.run_id, manifest.digest)
    if binding_of(event) != binding:
        _raise_load("event_binding")
    if (
        type(event.step_index) is not int
        or isinstance(event.step_index, bool)
        or not 1 <= event.step_index <= len(manifest.workflow_snapshot.steps)
    ):
        _raise_load("step")
    step = manifest.workflow_snapshot.steps[event.step_index - 1]
    if (
        event.workflow_id != manifest.workflow_id
        or event.step_id != step.id
        or event.employee_id != step.employee
        or step.artifact_content_type is None
        or event.event_type != "step_succeeded"
        or event.next_status != "succeeded"
        or event.failure_category is not None
        or event.execution_attempt_id is None
    ):
        _raise_load("success_event")
    try:
        _validate_run_terminal_event(store.root, binding, event)
        attempt = load_execution_attempt_evidence(
            store.root, manifest.run_id, event.execution_attempt_id
        )
        normalized = load_normalized_result_evidence(
            store.root, manifest.run_id, event.execution_attempt_id
        )
    except Exception:
        _raise_load("execution_evidence")
    if not _matches_success_lineage(manifest, step.id, event, attempt, normalized):
        _raise_load("execution_evidence")
    assert normalized.text is not None
    content = normalized.text.encode("utf-8")
    content_digest = sha256(content).hexdigest()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    event_digest = sha256(event_bytes).hexdigest()
    identity = _artifact_identity(
        run_id=manifest.run_id,
        manifest_digest=manifest.digest,
        workflow_id=manifest.workflow_id,
        step_id=step.id,
        step_index=event.step_index,
        employee_id=step.employee,
        content_type=step.artifact_content_type,
        content_sha256=content_digest,
        content_length=len(content),
        execution_attempt_id=attempt.attempt_id,
        execution_attempt_evidence_sha256=attempt.digest,
        normalized_result_evidence_sha256=normalized.digest,
        raw_response_evidence_sha256=normalized.raw_response_evidence_sha256,
        raw_response_body_sha256=normalized.raw_response_body_sha256,
        terminal_success_event_sha256=event_digest,
    )
    return WorkflowArtifact(
        schema_version=_ARTIFACT_SCHEMA,
        artifact_id=identity,
        run_id=manifest.run_id,
        manifest_digest=manifest.digest,
        workflow_id=manifest.workflow_id,
        step_id=step.id,
        step_index=event.step_index,
        employee_id=step.employee,
        content_type=step.artifact_content_type,
        content=content,
        content_sha256=content_digest,
        content_length=len(content),
        execution_attempt_id=attempt.attempt_id,
        execution_attempt_evidence_sha256=attempt.digest,
        normalized_result_evidence_sha256=normalized.digest,
        raw_response_evidence_sha256=normalized.raw_response_evidence_sha256,
        raw_response_body_sha256=normalized.raw_response_body_sha256,
        terminal_success_event_sha256=event_digest,
    )


def _matches_success_lineage(
    manifest: WorkflowRunManifest,
    step_id: str,
    event: RuntimeStepEvent,
    attempt: ExecutionAttemptEvidence,
    normalized: NormalizedExecutionResultEvidence,
) -> bool:
    return (
        normalized.category == "success"
        and normalized.text is not None
        and event.output_text == normalized.text
        and (attempt.run_id, attempt.manifest_digest, attempt.workflow_id)
        == (manifest.run_id, manifest.digest, manifest.workflow_id)
        and (attempt.step_id, attempt.step_index, attempt.employee_id)
        == (step_id, event.step_index, event.employee_id)
        and (normalized.run_id, normalized.manifest_digest, normalized.workflow_id)
        == (manifest.run_id, manifest.digest, manifest.workflow_id)
        and (normalized.step_id, normalized.employee_id, normalized.attempt_id)
        == (step_id, event.employee_id, attempt.attempt_id)
        and event.execution_attempt_evidence_sha256 == attempt.digest
        and event.normalized_result_evidence_sha256 == normalized.digest
        and event.raw_response_evidence_sha256
        == normalized.raw_response_evidence_sha256
        and event.raw_response_body_sha256 == normalized.raw_response_body_sha256
    )


def _validate_artifact_lineage(
    store: WorkflowRunManifestStore,
    manifest: WorkflowRunManifest,
    history: LoadedWorkflowExecutionHistory,
    artifact: WorkflowArtifact,
) -> None:
    if (
        artifact.run_id != manifest.run_id
        or artifact.manifest_digest != manifest.digest
        or artifact.workflow_id != manifest.workflow_id
        or not 1 <= artifact.step_index <= len(manifest.workflow_snapshot.steps)
    ):
        _raise_load("artifact_binding")
    step = manifest.workflow_snapshot.steps[artifact.step_index - 1]
    if (
        step.id != artifact.step_id
        or step.employee != artifact.employee_id
        or step.artifact_content_type != artifact.content_type
        or step.artifact_content_type is None
    ):
        _raise_load("artifact_policy")
    matches = tuple(
        event
        for event in history.events
        if event.execution_attempt_id == artifact.execution_attempt_id
        and event.step_id == artifact.step_id
        and event.step_index == artifact.step_index
    )
    if len(matches) != 1:
        _raise_load("terminal_event")
    expected = _artifact_from_success_event(store, manifest, matches[0])
    if artifact != expected:
        _raise_load("artifact_lineage")


def _load_manifest(
    store: WorkflowRunManifestStore, run_id: str
) -> WorkflowRunManifest:
    _validate_store(store)
    _validate_run_id(run_id, load=True)
    try:
        return load_workflow_run_manifest(store, run_id)
    except Exception:
        _raise_load("manifest")


def _load_run_history(
    store: WorkflowRunManifestStore, run_id: str
) -> LoadedWorkflowExecutionHistory:
    try:
        return load_workflow_execution_history(store.execution_targets(run_id))
    except Exception:
        _raise_load("history")


def _load_artifact_records(
    store: WorkflowRunManifestStore, run_id: str
) -> tuple[WorkflowArtifact, ...]:
    _validate_store(store)
    _validate_run_id(run_id, load=True)
    marker = f"{run_id}.{_ARTIFACT_PREFIX}."
    try:
        paths = sorted(store.root.glob(f"{marker}*.json"), key=lambda item: item.name)
    except Exception:
        _raise_load("listing")
    artifacts: list[WorkflowArtifact] = []
    for path in paths:
        artifact_id = path.name.removeprefix(marker).removesuffix(".json")
        _validate_sha256(artifact_id, "artifact_id", load=True)
        if path.is_symlink() or not path.is_file():
            _raise_load("target")
        try:
            contents = path.read_bytes()
            value = json.loads(
                contents.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except Exception:
            _raise_load("parse")
        artifact = _parse_artifact(value)
        if artifact.artifact_id != artifact_id:
            _raise_load("identity")
        if workflow_artifact_canonical_bytes(artifact) != contents:
            _raise_load("noncanonical")
        artifacts.append(artifact)
    return tuple(artifacts)


def _parse_artifact(value: object) -> WorkflowArtifact:
    if type(value) is not dict or frozenset(value) != _ARTIFACT_KEYS:
        _raise_load("keys")
    try:
        content_base64 = value["content_base64"]
        if type(content_base64) is not str:
            _raise_load("content")
        content = base64.b64decode(content_base64.encode("ascii"), validate=True)
        artifact = WorkflowArtifact(
            schema_version=value["schema_version"],
            artifact_id=value["artifact_id"],
            run_id=value["run_id"],
            manifest_digest=value["manifest_digest"],
            workflow_id=value["workflow_id"],
            step_id=value["step_id"],
            step_index=value["step_index"],
            employee_id=value["employee_id"],
            content_type=value["content_type"],
            content=content,
            content_sha256=value["content_sha256"],
            content_length=value["content_length"],
            execution_attempt_id=value["execution_attempt_id"],
            execution_attempt_evidence_sha256=value[
                "execution_attempt_evidence_sha256"
            ],
            normalized_result_evidence_sha256=value[
                "normalized_result_evidence_sha256"
            ],
            raw_response_evidence_sha256=value["raw_response_evidence_sha256"],
            raw_response_body_sha256=value["raw_response_body_sha256"],
            terminal_success_event_sha256=value["terminal_success_event_sha256"],
        )
    except WorkflowArtifactError:
        raise
    except (UnicodeEncodeError, ValueError, binascii.Error, TypeError):
        _raise_load("record")
    if content_base64 != base64.b64encode(content).decode("ascii"):
        _raise_load("content")
    return artifact


def _validate_artifact(value: object) -> None:
    if type(value) is not WorkflowArtifact:
        _raise_load("record_type")
    assert isinstance(value, WorkflowArtifact)
    if value.schema_version != _ARTIFACT_SCHEMA:
        _raise_load("schema_version")
    _validate_sha256(value.artifact_id, "artifact_id", load=True)
    _validate_run_id(value.run_id, load=True)
    for name in (
        "manifest_digest",
        "content_sha256",
        "execution_attempt_id",
        "execution_attempt_evidence_sha256",
        "normalized_result_evidence_sha256",
        "terminal_success_event_sha256",
    ):
        _validate_sha256(getattr(value, name), name, load=True)
    for name in ("raw_response_evidence_sha256", "raw_response_body_sha256"):
        digest = getattr(value, name)
        if digest is not None:
            _validate_sha256(digest, name, load=True)
    if (value.raw_response_evidence_sha256 is None) != (
        value.raw_response_body_sha256 is None
    ):
        _raise_load("raw_response")
    for item in (value.workflow_id, value.step_id, value.employee_id):
        if type(item) is not str or not item or any(char.isspace() for char in item):
            _raise_load("identity")
    if (
        type(value.step_index) is not int
        or isinstance(value.step_index, bool)
        or value.step_index < 1
        or type(value.content_type) is not str
        or _CONTENT_TYPE_PATTERN.fullmatch(value.content_type) is None
        or type(value.content) is not bytes
        or type(value.content_length) is not int
        or isinstance(value.content_length, bool)
        or value.content_length < 0
        or len(value.content) != value.content_length
        or sha256(value.content).hexdigest() != value.content_sha256
    ):
        _raise_load("content_integrity")
    try:
        value.content.decode("utf-8")
    except UnicodeDecodeError:
        _raise_load("content_encoding")
    identity = _artifact_identity(
        run_id=value.run_id,
        manifest_digest=value.manifest_digest,
        workflow_id=value.workflow_id,
        step_id=value.step_id,
        step_index=value.step_index,
        employee_id=value.employee_id,
        content_type=value.content_type,
        content_sha256=value.content_sha256,
        content_length=value.content_length,
        execution_attempt_id=value.execution_attempt_id,
        execution_attempt_evidence_sha256=value.execution_attempt_evidence_sha256,
        normalized_result_evidence_sha256=value.normalized_result_evidence_sha256,
        raw_response_evidence_sha256=value.raw_response_evidence_sha256,
        raw_response_body_sha256=value.raw_response_body_sha256,
        terminal_success_event_sha256=value.terminal_success_event_sha256,
    )
    if identity != value.artifact_id:
        _raise_load("identity")


def workflow_artifact_canonical_bytes(artifact: WorkflowArtifact) -> bytes:
    """Return the one canonical UTF-8 representation of an Artifact record."""
    _validate_artifact(artifact)
    return _canonical_json(_artifact_dict(artifact)).encode("utf-8")


def _artifact_dict(artifact: WorkflowArtifact) -> dict[str, object]:
    return {
        "artifact_id": artifact.artifact_id,
        "content_base64": base64.b64encode(artifact.content).decode("ascii"),
        "content_length": artifact.content_length,
        "content_sha256": artifact.content_sha256,
        "content_type": artifact.content_type,
        "employee_id": artifact.employee_id,
        "execution_attempt_evidence_sha256": artifact.execution_attempt_evidence_sha256,
        "execution_attempt_id": artifact.execution_attempt_id,
        "manifest_digest": artifact.manifest_digest,
        "normalized_result_evidence_sha256": artifact.normalized_result_evidence_sha256,
        "raw_response_body_sha256": artifact.raw_response_body_sha256,
        "raw_response_evidence_sha256": artifact.raw_response_evidence_sha256,
        "run_id": artifact.run_id,
        "schema_version": artifact.schema_version,
        "step_id": artifact.step_id,
        "step_index": artifact.step_index,
        "terminal_success_event_sha256": artifact.terminal_success_event_sha256,
        "workflow_id": artifact.workflow_id,
    }


def _artifact_identity(**fields: object) -> str:
    return sha256(_canonical_json(fields).encode("utf-8")).hexdigest()


def _persist_workflow_artifact(
    store: WorkflowRunManifestStore, artifact: WorkflowArtifact
) -> None:
    path = workflow_artifact_path(store, artifact.run_id, artifact.artifact_id)
    contents = workflow_artifact_canonical_bytes(artifact)
    temporary_path: Path | None = None
    try:
        if path.is_symlink() or path.is_dir() or (path.exists() and not path.is_file()):
            _raise_persistence("target")
        if path.exists():
            if path.read_bytes() != contents:
                _raise_conflict()
            with path.open("rb") as existing:
                os.fsync(existing.fileno())
            _fsync_directory(store.root)
            return

        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=store.root
        )
        temporary_path = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            written = handle.write(contents)
            if type(written) is not int or written != len(contents):
                raise OSError("short Artifact write")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != contents:
                _raise_conflict()
        os.unlink(temporary_path)
        temporary_path = None
        _fsync_directory(store.root)
    except WorkflowArtifactPersistenceError:
        raise
    except Exception:
        _raise_persistence("ambiguous")
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _export_bytes(destination: Path, artifact: WorkflowArtifact) -> None:
    if type(destination) is not _PATH_TYPE:
        _raise_export("destination")
    try:
        if not destination.parent.is_dir() or destination.parent.is_symlink():
            _raise_export("destination")
        if destination.is_symlink() or destination.exists():
            _raise_export_conflict()
    except WorkflowArtifactExportError:
        raise
    except Exception:
        _raise_export("preflight")

    temporary_path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        temporary_path = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            written = handle.write(artifact.content)
            if type(written) is not int or written != artifact.content_length:
                raise OSError("short Artifact export")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, destination)
        except FileExistsError:
            _raise_export_conflict()
        os.unlink(temporary_path)
        temporary_path = None
        _fsync_directory(destination.parent)
    except WorkflowArtifactExportError:
        raise
    except Exception:
        _raise_export("ambiguous")
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _validate_store(store: object) -> None:
    if type(store) is not WorkflowRunManifestStore:
        _raise_load("store")
    try:
        if store.root.is_symlink() or not store.root.is_dir():
            _raise_load("store")
    except WorkflowArtifactError:
        raise
    except Exception:
        _raise_load("store")


def _validate_run_id(value: object, *, load: bool = False) -> None:
    if type(value) is not str or _RUN_ID_PATTERN.fullmatch(value) is None:
        (_raise_load if load else _raise_persistence)("run_id")


def _validate_sha256(value: object, name: str, *, load: bool = False) -> None:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        (_raise_load if load else _raise_persistence)(name)


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate Artifact key")
        value[key] = item
    return value


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    del value
    raise ValueError("nonstandard JSON constant")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(os.fspath(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _raise_load(classification: str) -> NoReturn:
    raise WorkflowArtifactLoadError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise WorkflowArtifactPersistenceError(classification) from None


def _raise_conflict() -> NoReturn:
    raise WorkflowArtifactConflictError("identity_conflict") from None


def _raise_export(classification: str) -> NoReturn:
    raise WorkflowArtifactExportError(classification) from None


def _raise_export_conflict() -> NoReturn:
    raise WorkflowArtifactExportConflictError("destination_exists") from None


__all__ = [
    "WorkflowArtifact",
    "WorkflowArtifactConflictError",
    "WorkflowArtifactError",
    "WorkflowArtifactExportConflictError",
    "WorkflowArtifactExportError",
    "WorkflowArtifactExportReceipt",
    "WorkflowArtifactLoadError",
    "WorkflowArtifactPersistenceError",
    "export_run_artifact",
    "list_run_artifacts",
    "read_run_artifact",
    "workflow_artifact_canonical_bytes",
    "workflow_artifact_path",
]
