"""Immutable Run-bound business Artifacts derived from execution evidence."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import subprocess
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
_FILE_CHANGE_SET_CONTENT_TYPE = "application/vnd.ai-office.file-change-set+json"
_MAX_ARTIFACT_BYTES = 1_048_576
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
    *,
    allow_creation: bool = True,
) -> tuple[WorkflowArtifact, ...]:
    """Validate required Artifacts, optionally completing missing records."""
    _validate_store(store)
    if type(allow_creation) is not bool:
        _raise_load("contract")
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
        if any(
            item.execution_attempt_id == event.execution_attempt_id
            for item in existing_by_id.values()
        ):
            # A pre-normalization Artifact for this successful attempt remains
            # immutable. Its lineage was checked above; do not add a second
            # canonical Artifact beside that historical record.
            continue
        artifact = _artifact_from_success_event(store, manifest, event)
        existing = existing_by_id.get(artifact.artifact_id)
        if existing is not None:
            if existing != artifact:
                _raise_load("identity_conflict")
            continue
        if not allow_creation:
            _raise_load("required_artifact_missing")
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
    *,
    canonicalize_file_change_set: bool = True,
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
    if (
        canonicalize_file_change_set
        and step.artifact_content_type == _FILE_CHANGE_SET_CONTENT_TYPE
    ):
        if step.model_output_format == "unified-diff":
            content = _canonicalize_unified_diff(manifest.run_input, normalized.text)
        elif step.model_output_format is None:
            candidate = normalized.text.strip()
            if candidate.startswith("```"):
                fenced = re.fullmatch(
                    r"```json[ \t]*\r?\n(.*?)(?:\r?\n)?```",
                    candidate,
                    flags=re.DOTALL,
                )
                if fenced is None:
                    _raise_load("file_change_set_json")
                candidate = fenced.group(1)
            try:
                value = json.loads(
                    candidate,
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=_reject_nonstandard_json_constant,
                )
            except (json.JSONDecodeError, ValueError, TypeError, RecursionError):
                _raise_load("file_change_set_json")
            if type(value) is not dict:
                _raise_load("file_change_set_json")
            try:
                content = _canonical_json(value).encode("utf-8")
            except (UnicodeEncodeError, ValueError, TypeError, RecursionError):
                _raise_load("file_change_set_json")
        else:
            _raise_load("file_change_set_json")
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
        if event.event_type == "step_succeeded"
        and event.execution_attempt_id == artifact.execution_attempt_id
        and event.step_id == artifact.step_id
        and event.step_index == artifact.step_index
    )
    if len(matches) != 1:
        _raise_load("terminal_event")
    try:
        expected = _artifact_from_success_event(store, manifest, matches[0])
    except WorkflowArtifactLoadError:
        expected = None
    if artifact == expected:
        return
    if step.model_output_format == "unified-diff":
        _raise_load("artifact_lineage")
    # Existing Artifacts were historically the exact normalized provider text.
    # Continue to verify those immutable records without rewriting them; new
    # file-change-set Artifacts are canonicalized by the finalization path above.
    legacy = _artifact_from_success_event(
        store,
        manifest,
        matches[0],
        canonicalize_file_change_set=False,
    )
    if artifact != legacy:
        _raise_load("artifact_lineage")


def _canonicalize_unified_diff(run_input: str, patch: str) -> bytes:
    """Apply one pinned exact-position diff and encode the existing v1 payload."""
    try:
        input_value = json.loads(
            run_input,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
        if (
            type(input_value) is dict
            and type(input_value.get("assignment")) is str
            and type(input_value.get("source_artifact")) is dict
        ):
            input_value = json.loads(
                input_value["assignment"],
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonstandard_json_constant,
            )
        repository = input_value["repository"]
        allowed_paths = input_value["allowed_paths"]
        source_context = input_value["source_context"]
        source_sha256 = input_value["source_sha256"]
        if (
            type(input_value) is not dict
            or type(repository) is not dict
            or type(allowed_paths) is not dict
            or type(source_context) is not dict
            or type(source_sha256) is not dict
            or type(allowed_paths.get("write")) is not list
        ):
            _raise_load("file_change_set_diff")
        base_revision = repository.get("base_revision")
        write_paths = allowed_paths["write"]
        if (
            type(base_revision) is not str
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_revision)
            is None
            or not write_paths
            or any(type(path) is not str for path in write_paths)
            or len(write_paths) != len(set(write_paths))
        ):
            _raise_load("file_change_set_diff")

        sources: dict[str, bytes] = {}
        for path in write_paths:
            path_parts = Path(path).parts
            if (
                not path
                or Path(path).is_absolute()
                or "\\" in path
                or "\x00" in path
                or Path(path).as_posix() != path
                or not path_parts
                or any(part in {"", ".", ".."} for part in path_parts)
                or path_parts[0] == ".git"
            ):
                _raise_load("file_change_set_diff")
            source_text = source_context.get(path)
            expected_digest = source_sha256.get(path)
            if type(source_text) is not str or type(expected_digest) is not str:
                _raise_load("file_change_set_diff")
            source = source_text.encode("utf-8")
            if expected_digest != sha256(source).hexdigest():
                _raise_load("file_change_set_diff")
            sources[path] = source

        patch_bytes = patch.encode("utf-8")
        if not patch_bytes:
            _raise_load("file_change_set_diff")
        patch_parts = patch.split("\n")
        if patch.endswith("\n"):
            patch_parts.pop()
        patch_lines = []
        line_endings = []
        for part_index, part in enumerate(patch_parts):
            has_lf = part_index < len(patch_parts) - 1 or patch.endswith("\n")
            if has_lf and part.endswith("\r"):
                patch_lines.append(part[:-1])
                line_endings.append("\r\n")
            else:
                patch_lines.append(part)
                line_endings.append("\n" if has_lf else "")
        # Derive only hunk counts and reject unsupported surrounding text;
        # Git remains the authority for applying the patch to fixed sources.
        if (
            len(patch_lines) < 3
            or not patch_lines[0].startswith("--- ")
            or not patch_lines[1].startswith("+++ ")
            or not _normalize_unified_diff_hunks(patch_lines, sources)
            or any(
                not line.startswith(("--- ", "+++ ", "@@ ", " ", "+", "-"))
                and line != r"\ No newline at end of file"
                for line in patch_lines
            )
        ):
            _raise_load("file_change_set_diff")
        # Rebuild with only hunk counts changed, preserving all other model
        # bytes. Evidence continues to store the original response unchanged.
        git_patch_bytes = "".join(
            line + ending
            for line, ending in zip(patch_lines, line_endings, strict=True)
        ).encode("utf-8")
        if not git_patch_bytes.endswith(b"\n"):
            git_patch_bytes += b"\n"
        if len(git_patch_bytes) > 1_048_576:
            _raise_load("file_change_set_diff")
        with tempfile.TemporaryDirectory(prefix="ai-office-unified-diff-") as root:
            workspace = Path(root)
            for path, source in sources.items():
                target = workspace / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source)
                target.chmod(0o600)

            environment = {
                "HOME": root,
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": os.defpath,
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
            }

            def git_apply(*arguments: str) -> subprocess.CompletedProcess[bytes]:
                return subprocess.run(
                    ["git", "apply", *arguments, "-"],
                    cwd=workspace,
                    input=git_patch_bytes,
                    capture_output=True,
                    env=environment,
                    timeout=5,
                    check=False,
                )

            checked = git_apply("--check", "--verbose")
            diagnostics = checked.stderr.decode("utf-8", errors="replace")
            if checked.returncode != 0 or re.search(
                r"\b(?:Hunk\s+#\d+\s+succeeded|offset\s+-?\d+\s+lines?|"
                r"with fuzz\s+\d+)\b",
                diagnostics,
                flags=re.IGNORECASE,
            ):
                _raise_load("file_change_set_diff")

            summary = git_apply("--summary")
            if summary.returncode != 0 or summary.stdout:
                _raise_load("file_change_set_diff")

            numstat = git_apply("--numstat", "-z")
            if numstat.returncode != 0:
                _raise_load("file_change_set_diff")
            records = numstat.stdout.decode("utf-8").split("\x00")
            if not records or records[-1] != "":
                _raise_load("file_change_set_diff")
            changed_paths: list[str] = []
            for record in records[:-1]:
                fields = record.split("\t", 2)
                if (
                    len(fields) != 3
                    or fields[0] == "-"
                    or fields[1] == "-"
                    or fields[2] not in sources
                    or fields[2] in changed_paths
                ):
                    _raise_load("file_change_set_diff")
                changed_paths.append(fields[2])
            if not changed_paths:
                _raise_load("file_change_set_diff")

            applied = git_apply()
            if applied.returncode != 0:
                _raise_load("file_change_set_diff")
            existing_paths = {
                item.relative_to(workspace).as_posix()
                for item in workspace.rglob("*")
                if item.is_file() or item.is_symlink()
            }
            if existing_paths != set(sources):
                _raise_load("file_change_set_diff")

            changes = []
            for path in sorted(changed_paths):
                target = workspace / path
                if (
                    target.is_symlink()
                    or not target.is_file()
                ):
                    _raise_load("file_change_set_diff")
                content = target.read_bytes()
                content.decode("utf-8")
                if content == sources[path]:
                    _raise_load("file_change_set_diff")
                changes.append(
                    {
                        "path": path,
                        "before_sha256": sha256(sources[path]).hexdigest(),
                        "content_base64": base64.b64encode(content).decode("ascii"),
                    }
                )
        content = _canonical_json(
            {
                "schema_version": "file-change-set.v1",
                "base_revision": base_revision,
                "changes": changes,
            }
        ).encode("utf-8")
        if len(content) > _MAX_ARTIFACT_BYTES:
            _raise_load("file_change_set_diff")
        return content
    except WorkflowArtifactLoadError:
        raise
    # Runtime I/O and Git process errors deliberately cross this boundary.
    # The existing routing owner maps those unknown dependencies to its
    # conservative execution-uncertain stop; only input/patch failures get
    # the model-output classification.
    except (UnicodeError, ValueError, TypeError, KeyError, RecursionError):
        _raise_load("file_change_set_diff")


def _normalize_unified_diff_hunks(
    patch_lines: list[str], sources: dict[str, bytes]
) -> bool:
    """Derive hunk counts and anchor shifted hunks to unique fixed preimages."""
    marker = r"\ No newline at end of file"
    index = 0
    has_hunk = False
    source_lines: list[str] | None = None
    while index < len(patch_lines):
        line = patch_lines[index]
        if line.startswith("--- "):
            if (
                index + 2 >= len(patch_lines)
                or not patch_lines[index + 1].startswith("+++ ")
                or not patch_lines[index + 2].startswith("@@ ")
            ):
                return False
            old_path = patch_lines[index][4:].split("\t", 1)[0]
            new_path = patch_lines[index + 1][4:].split("\t", 1)[0]
            source_lines = None
            if old_path.startswith("a/") and new_path == f"b/{old_path[2:]}":
                source = sources.get(old_path[2:])
                if source is not None:
                    source_text = source.decode("utf-8")
                    source_lines = source_text.split("\n") if source_text else []
                    if source_text.endswith("\n"):
                        source_lines.pop()
                    source_lines = [
                        source_line[:-1]
                        if (
                            line_index < len(source_lines) - 1
                            or source_text.endswith("\n")
                        )
                        and source_line.endswith("\r")
                        else source_line
                        for line_index, source_line in enumerate(source_lines)
                    ]
            index += 2
            continue
        if line.startswith("@@ "):
            header = re.match(
                r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))?( @@.*)$", line
            )
            if header is None:
                return False
            old_start = int(header.group(1))
            old_count = 0
            new_count = 0
            old_hunk_lines: list[str] = []
            has_hunk = True
            body_index = index + 1
            while body_index < len(patch_lines):
                body_line = patch_lines[body_index]
                if body_line.startswith("@@ ") or (
                    body_line.startswith("--- ")
                    and body_index + 2 < len(patch_lines)
                    and patch_lines[body_index + 1].startswith("+++ ")
                    and patch_lines[body_index + 2].startswith("@@ ")
                ):
                    break
                if body_line == marker:
                    body_index += 1
                    continue
                if body_line.startswith(" "):
                    old_count += 1
                    new_count += 1
                    old_hunk_lines.append(body_line[1:])
                elif body_line.startswith("-"):
                    old_count += 1
                    old_hunk_lines.append(body_line[1:])
                elif body_line.startswith("+"):
                    new_count += 1
                else:
                    break
                body_index += 1
            new_start = int(header.group(3))
            if old_hunk_lines and source_lines is not None:
                declared_position = old_start - 1
                declared_matches = (
                    declared_position >= 0
                    and source_lines[
                        declared_position : declared_position + len(old_hunk_lines)
                    ]
                    == old_hunk_lines
                )
                if not declared_matches:
                    matches = []
                    for position in range(
                        len(source_lines) - len(old_hunk_lines) + 1
                    ):
                        if (
                            source_lines[position : position + len(old_hunk_lines)]
                            == old_hunk_lines
                        ):
                            matches.append(position)
                            if len(matches) > 1:
                                break
                    if len(matches) != 1:
                        return False
                    line_offset = matches[0] - declared_position
                    old_start = matches[0] + 1
                    new_start += line_offset
                    if new_start < 0:
                        return False
            old_header = f"@@ -{old_start}"
            if header.group(2) is not None or old_count != 1:
                old_header += f",{old_count}"
            new_header = f" +{new_start}"
            if header.group(4) is not None or new_count != 1:
                new_header += f",{new_count}"
            patch_lines[index] = old_header + new_header + header.group(5)
            index = body_index
            continue
        return False
    return has_hunk


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
