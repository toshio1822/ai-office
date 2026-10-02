"""Immutable, provider-free durable manifests for one workflow Run.

A manifest captures the validated semantic definition and explicit business
input for one concrete Run.  It deliberately has no relationship to the
current workflow state/event persistence or to a provider execution path.
"""

from __future__ import annotations

import errno
import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from ai_office.definitions.employee import EmployeeDefinition, LoadedEmployee
from ai_office.definitions.workflow import (
    LoadedWorkflow,
    WorkflowDefinition,
    WorkflowStepDefinition,
    validate_workflow_employee_references,
)
from ai_office.tools import (
    DEFAULT_TOOL_CATALOG,
    ToolCatalog,
    ToolDefinition,
    ToolParameterDefinition,
    resolve_tool_names,
)

_MANIFEST_SCHEMA_VERSION = "workflow-run-manifest.v1"
_MANIFEST_ERROR_MESSAGE = "workflow run manifest is invalid"
_PERSISTENCE_ERROR_MESSAGE = "workflow run manifest persistence failed"
_LOAD_ERROR_MESSAGE = "workflow run manifest could not be loaded"
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DEFINITION_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PATH_TYPE = type(Path())
_MANIFEST_KEYS = frozenset(
    {
        "employee_snapshots",
        "run_id",
        "run_input",
        "schema_version",
        "tool_contracts",
        "workflow_id",
        "workflow_snapshot",
    }
)
_WORKFLOW_KEYS = frozenset({"description", "id", "name", "steps"})
_WORKFLOW_STEP_KEYS = frozenset({"employee", "id", "instructions", "name"})
_EMPLOYEE_KEYS = frozenset(
    {"allowed_tools", "id", "instructions", "model", "name", "role"}
)
_TOOL_KEYS = frozenset({"description", "name", "parameters"})
_TOOL_PARAMETER_KEYS = frozenset({"description", "name", "required", "type"})


@dataclass(frozen=True)
class WorkflowRunManifestFailureDetail:
    """Safe classification for one manifest operation failure."""

    classification: str


class WorkflowRunManifestError(ValueError):
    """Raised when a Run Manifest value is not an exact v1 contract."""

    def __init__(self, classification: str = "contract") -> None:
        super().__init__(_MANIFEST_ERROR_MESSAGE)
        self.detail = WorkflowRunManifestFailureDetail(classification)


class WorkflowRunManifestPersistenceError(WorkflowRunManifestError):
    """Raised when a manifest cannot be durably committed."""

    def __init__(self, classification: str = "persistence") -> None:
        ValueError.__init__(self, _PERSISTENCE_ERROR_MESSAGE)
        self.detail = WorkflowRunManifestFailureDetail(classification)


class WorkflowRunManifestConflictError(WorkflowRunManifestPersistenceError):
    """Raised when a target already contains different immutable content."""


class WorkflowRunManifestLoadError(WorkflowRunManifestError):
    """Raised when a target is not an exact canonical v1 manifest."""

    def __init__(self, classification: str = "load") -> None:
        ValueError.__init__(self, _LOAD_ERROR_MESSAGE)
        self.detail = WorkflowRunManifestFailureDetail(classification)


@dataclass(frozen=True)
class WorkflowStepSnapshot:
    """Detached immutable semantic snapshot of one workflow step."""

    id: str
    name: str
    employee: str
    instructions: str

    def __post_init__(self) -> None:
        _validate_workflow_step_snapshot(self)


@dataclass(frozen=True)
class WorkflowDefinitionSnapshot:
    """Detached immutable semantic snapshot of a validated workflow."""

    id: str
    name: str
    description: str
    steps: tuple[WorkflowStepSnapshot, ...]

    def __post_init__(self) -> None:
        _validate_workflow_snapshot(self)


@dataclass(frozen=True)
class EmployeeDefinitionSnapshot:
    """Detached immutable semantic snapshot of one referenced employee."""

    id: str
    name: str
    role: str
    instructions: str
    model: str
    allowed_tools: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_employee_snapshot(self)


@dataclass(frozen=True)
class ToolParameterSnapshot:
    """Detached immutable snapshot of one provider-independent parameter."""

    name: str
    description: str
    type: str
    required: bool

    def __post_init__(self) -> None:
        _validate_tool_parameter_snapshot(self)


@dataclass(frozen=True)
class ToolContractSnapshot:
    """Detached immutable provider-independent contract for one required tool."""

    name: str
    description: str
    parameters: tuple[ToolParameterSnapshot, ...]

    def __post_init__(self) -> None:
        _validate_tool_contract_snapshot(self)


@dataclass(frozen=True)
class WorkflowRunManifest:
    """The immutable semantic identity of one concrete workflow Run.

    ``employee_snapshots`` and ``tool_contracts`` are canonically ordered by
    identity because they represent referenced sets.  Workflow step order and
    each employee's allowed-tool order remain semantic and are preserved.
    """

    schema_version: Literal["workflow-run-manifest.v1"]
    run_id: str
    workflow_id: str
    run_input: str
    workflow_snapshot: WorkflowDefinitionSnapshot
    employee_snapshots: tuple[EmployeeDefinitionSnapshot, ...]
    tool_contracts: tuple[ToolContractSnapshot, ...]

    def __post_init__(self) -> None:
        _validate_manifest(self)
        object.__setattr__(
            self,
            "employee_snapshots",
            tuple(sorted(self.employee_snapshots, key=lambda item: item.id)),
        )
        object.__setattr__(
            self,
            "tool_contracts",
            tuple(sorted(self.tool_contracts, key=lambda item: item.name)),
        )

    @property
    def digest(self) -> str:
        """Return SHA-256 over this manifest's exact canonical UTF-8 bytes."""
        return workflow_run_manifest_digest(self)


@dataclass(frozen=True)
class WorkflowRunManifestStore:
    """Authoritative durable namespace for Workflow Run Manifests.

    The root is the store namespace, not a per-Run target.  A Run target is
    always derived from its ``run_id`` inside this store, so callers cannot
    select a second filename or path for the same Run identity.
    """

    root: Path

    def __post_init__(self) -> None:
        _validate_storage_root(self.root)

    def manifest_path(self, run_id: str) -> Path:
        """Return the identity-derived Manifest target for one Run."""
        return _manifest_path(self, run_id)

    def state_path(self, run_id: str) -> Path:
        """Return the identity-derived execution-state target for one Run."""
        _validate_run_identity(run_id)
        return self.root / f"{run_id}.state.json"

    def events_path(self, run_id: str) -> Path:
        """Return the identity-derived runtime-event target for one Run."""
        _validate_run_identity(run_id)
        return self.root / f"{run_id}.events.jsonl"

    def execution_paths(self, run_id: str) -> tuple[Path, Path]:
        """Return the only state/event pair belonging to one Run identity."""
        return self.state_path(run_id), self.events_path(run_id)

    def execution_targets(self, run_id: str):
        """Return persistence targets derived from the authoritative Run root."""
        from ai_office.runtime.run_binding import WorkflowRunBinding
        from ai_office.storage.workflow_execution_persistence import (
            WorkflowExecutionPersistenceTargets,
        )

        manifest = load_workflow_run_manifest(self, run_id)
        state_path, events_path = self.execution_paths(run_id)
        return WorkflowExecutionPersistenceTargets(
            state_path,
            events_path,
            binding=WorkflowRunBinding(
                run_id=manifest.run_id,
                manifest_digest=manifest.digest,
            ),
        )


def build_workflow_run_manifest(
    run_id: str,
    run_input: str,
    workflow: LoadedWorkflow,
    employees: Sequence[LoadedEmployee],
    *,
    tool_catalog: ToolCatalog = DEFAULT_TOOL_CATALOG,
) -> WorkflowRunManifest:
    """Build one detached manifest from validated definition inputs.

    The function reads only validated local definition values.  It does not
    read source paths into the record, call a provider, resolve a network
    service, or persist anything.  Tool names are resolved through the
    existing provider-independent catalog contract before the manifest can be
    returned.
    """
    _validate_run_identity(run_id)
    if type(run_input) is not str:
        _raise_manifest("run_input")
    if type(workflow) is not LoadedWorkflow:
        _raise_manifest("workflow")
    if type(tool_catalog) is not ToolCatalog:
        _raise_manifest("tool_catalog")
    if not isinstance(employees, Sequence) or isinstance(employees, (str, bytes)):
        _raise_manifest("employees")
    if any(type(employee) is not LoadedEmployee for employee in employees):
        _raise_manifest("employees")

    # Keep the existing workflow-reference validator as the authoritative
    # lower-level employee-reference check, including its safe error type.
    validate_workflow_employee_references([workflow], list(employees))
    employee_by_id: dict[str, LoadedEmployee] = {}
    for employee in employees:
        employee_id = employee.definition.id
        if employee_id in employee_by_id:
            _raise_manifest("duplicate_employee")
        employee_by_id[employee_id] = employee

    workflow_definition = workflow.definition
    if type(workflow_definition) is not WorkflowDefinition:
        _raise_manifest("workflow_definition")
    workflow_snapshot = WorkflowDefinitionSnapshot(
        id=workflow_definition.id,
        name=workflow_definition.name,
        description=workflow_definition.description,
        steps=tuple(
            _workflow_step_snapshot(step) for step in workflow_definition.steps
        ),
    )

    referenced_employee_ids = {step.employee for step in workflow_definition.steps}
    employee_snapshots = tuple(
        _employee_snapshot(employee_by_id[employee_id])
        for employee_id in sorted(referenced_employee_ids)
    )
    required_tool_names = tuple(
        sorted(
            {
                tool_name
                for employee in employee_snapshots
                for tool_name in employee.allowed_tools
            }
        )
    )
    # This performs the catalog's existing duplicate-name and exact-name
    # validation even when the workflow requires no tools.
    resolved_tools = resolve_tool_names(tool_catalog, required_tool_names)
    tool_contracts = tuple(_tool_contract_snapshot(tool) for tool in resolved_tools)

    return WorkflowRunManifest(
        schema_version=_MANIFEST_SCHEMA_VERSION,
        run_id=run_id,
        workflow_id=workflow_snapshot.id,
        run_input=run_input,
        workflow_snapshot=workflow_snapshot,
        employee_snapshots=employee_snapshots,
        tool_contracts=tool_contracts,
    )


def create_workflow_run_manifest(
    store: WorkflowRunManifestStore,
    run_id: str,
    run_input: str,
    workflow: LoadedWorkflow,
    employees: Sequence[LoadedEmployee],
    *,
    tool_catalog: ToolCatalog = DEFAULT_TOOL_CATALOG,
) -> WorkflowRunManifest:
    """Build and durably create one Run Manifest before any execution.

    Validation and snapshot construction complete before the persistence target
    is opened.  The returned manifest is the exact value whose canonical bytes
    were committed; no provider, tool, network, or paid operation is involved.
    """
    _validate_store(store)
    manifest = build_workflow_run_manifest(
        run_id,
        run_input,
        workflow,
        employees,
        tool_catalog=tool_catalog,
    )
    _persist_workflow_run_manifest(_manifest_path(store, run_id), manifest)
    return manifest


def serialize_workflow_run_manifest_canonical(
    manifest: WorkflowRunManifest,
) -> str:
    """Serialize one manifest as compact deterministic JSON without a newline."""
    _validate_manifest(manifest)
    value = _manifest_dict(manifest)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        _raise_manifest("serialization")


def workflow_run_manifest_canonical_bytes(
    manifest: WorkflowRunManifest,
) -> bytes:
    """Return the exact canonical manifest representation as UTF-8 bytes."""
    try:
        return serialize_workflow_run_manifest_canonical(manifest).encode("utf-8")
    except WorkflowRunManifestError:
        raise
    except UnicodeError:
        _raise_manifest("encoding")
    except Exception:
        _raise_manifest("encoding")


def workflow_run_manifest_digest(manifest: WorkflowRunManifest) -> str:
    """Return SHA-256 over exact canonical manifest UTF-8 bytes."""
    return sha256(workflow_run_manifest_canonical_bytes(manifest)).hexdigest()


def persist_workflow_run_manifest(
    store: WorkflowRunManifestStore,
    manifest: WorkflowRunManifest,
) -> None:
    """Persist one manifest through the store's identity-derived target."""
    _validate_store(store)
    if type(manifest) is not WorkflowRunManifest:
        _raise_persistence("manifest")
    _persist_workflow_run_manifest(_manifest_path(store, manifest.run_id), manifest)


def _persist_workflow_run_manifest(path: Path, manifest: WorkflowRunManifest) -> None:
    """Create or idempotently persist one exact immutable manifest.

    Creation uses exclusive file creation and never replaces an existing
    target.  After creation begins, failures are reported as ambiguous and the
    target is retained; a later strict load can accept it only if its bytes
    are complete, canonical, and semantically valid.
    """
    contents = workflow_run_manifest_canonical_bytes(manifest)
    _validate_persistence_path(path)

    try:
        handle = path.open("xb")
    except FileExistsError:
        _persist_existing_manifest(path, contents)
        return
    except OSError as error:
        if error.errno == errno.EEXIST:
            _persist_existing_manifest(path, contents)
            return
        _raise_persistence("create")
    except Exception:
        _raise_persistence("create")

    _persist_new_manifest(handle, path.parent, contents)


def load_workflow_run_manifest(
    store: WorkflowRunManifestStore,
    run_id: str,
) -> WorkflowRunManifest:
    """Load one immutable Run Manifest through its authoritative store."""
    _validate_store(store)
    _validate_run_identity(run_id)
    return _load_workflow_run_manifest_at_path(
        _manifest_path(store, run_id), expected_run_id=run_id
    )


def workflow_definition_from_run_manifest(
    manifest: WorkflowRunManifest,
) -> WorkflowDefinition:
    """Reconstruct the pinned workflow meaning without reading live YAML."""
    _validate_manifest(manifest)
    snapshot = manifest.workflow_snapshot
    return WorkflowDefinition.model_validate(
        {
            "id": snapshot.id,
            "name": snapshot.name,
            "description": snapshot.description,
            "steps": [
                {
                    "id": step.id,
                    "name": step.name,
                    "employee": step.employee,
                    "instructions": step.instructions,
                }
                for step in snapshot.steps
            ],
        }
    )


def employee_definitions_from_run_manifest(
    manifest: WorkflowRunManifest,
) -> tuple[EmployeeDefinition, ...]:
    """Reconstruct exactly the employee meanings pinned by one Manifest."""
    _validate_manifest(manifest)
    return tuple(
        EmployeeDefinition.model_validate(
            {
                "id": employee.id,
                "name": employee.name,
                "role": employee.role,
                "instructions": employee.instructions,
                "model": employee.model,
                "allowed_tools": list(employee.allowed_tools),
            }
        )
        for employee in manifest.employee_snapshots
    )


def loaded_employees_from_run_manifest(
    manifest: WorkflowRunManifest,
) -> tuple[LoadedEmployee, ...]:
    """Provide detached employee wrappers for existing planning seams."""
    definitions = employee_definitions_from_run_manifest(manifest)
    source = Path(f"<workflow-run:{manifest.run_id}>")
    return tuple(
        LoadedEmployee(source_path=source, definition=item) for item in definitions
    )


def loaded_workflow_from_run_manifest(
    manifest: WorkflowRunManifest,
) -> LoadedWorkflow:
    """Provide a detached workflow wrapper for existing planning seams."""
    return LoadedWorkflow(
        source_path=Path(f"<workflow-run:{manifest.run_id}>"),
        definition=workflow_definition_from_run_manifest(manifest),
    )


def tool_catalog_from_run_manifest(manifest: WorkflowRunManifest) -> ToolCatalog:
    """Reconstruct the provider-independent tool contracts pinned by a Run."""
    _validate_manifest(manifest)
    return ToolCatalog(
        tools=tuple(
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                parameters=tuple(
                    ToolParameterDefinition(
                        name=parameter.name,
                        description=parameter.description,
                        type=parameter.type,
                        required=parameter.required,
                    )
                    for parameter in tool.parameters
                ),
            )
            for tool in manifest.tool_contracts
        )
    )


def _load_workflow_run_manifest_at_path(
    path: Path,
    *,
    expected_run_id: str,
) -> WorkflowRunManifest:
    """Load and strictly validate one exact canonical immutable manifest."""
    _validate_load_path(path)
    try:
        contents = path.read_bytes()
    except Exception:
        _raise_load("target")
    if type(contents) is not bytes:
        _raise_load("target")

    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKeyError,
        _NonStandardJSONConstantError,
    ):
        _raise_load("parse")
    except Exception:
        _raise_load("parse")

    manifest = _parse_manifest(value)
    try:
        canonical = workflow_run_manifest_canonical_bytes(manifest)
    except WorkflowRunManifestError:
        _raise_load("manifest")
    except Exception:
        _raise_load("manifest")
    if canonical != contents:
        _raise_load("noncanonical")
    if manifest.run_id != expected_run_id:
        _raise_load("identity")
    return manifest


def _workflow_step_snapshot(step: WorkflowStepDefinition) -> WorkflowStepSnapshot:
    if type(step) is not WorkflowStepDefinition:
        _raise_manifest("workflow_step")
    try:
        return WorkflowStepSnapshot(
            id=step.id,
            name=step.name,
            employee=step.employee,
            instructions=step.instructions,
        )
    except WorkflowRunManifestError:
        raise
    except Exception:
        _raise_manifest("workflow_step")


def _employee_snapshot(employee: LoadedEmployee) -> EmployeeDefinitionSnapshot:
    definition = employee.definition
    if type(definition) is not EmployeeDefinition:
        _raise_manifest("employee_definition")
    try:
        return EmployeeDefinitionSnapshot(
            id=definition.id,
            name=definition.name,
            role=definition.role,
            instructions=definition.instructions,
            model=definition.model,
            allowed_tools=tuple(definition.allowed_tools),
        )
    except WorkflowRunManifestError:
        raise
    except Exception:
        _raise_manifest("employee_definition")


def _tool_contract_snapshot(tool: ToolDefinition) -> ToolContractSnapshot:
    if type(tool) is not ToolDefinition:
        _raise_manifest("tool_definition")
    try:
        parameters = tool.parameters
        if type(parameters) is not tuple or any(
            type(parameter) is not ToolParameterDefinition for parameter in parameters
        ):
            _raise_manifest("tool_definition")
        return ToolContractSnapshot(
            name=tool.name,
            description=tool.description,
            parameters=tuple(
                ToolParameterSnapshot(
                    name=parameter.name,
                    description=parameter.description,
                    type=parameter.type,
                    required=parameter.required,
                )
                for parameter in parameters
            ),
        )
    except WorkflowRunManifestError:
        raise
    except Exception:
        _raise_manifest("tool_definition")


def _manifest_dict(manifest: WorkflowRunManifest) -> dict[str, object]:
    workflow = manifest.workflow_snapshot
    return {
        "employee_snapshots": [
            {
                "allowed_tools": list(employee.allowed_tools),
                "id": employee.id,
                "instructions": employee.instructions,
                "model": employee.model,
                "name": employee.name,
                "role": employee.role,
            }
            for employee in manifest.employee_snapshots
        ],
        "run_id": manifest.run_id,
        "run_input": manifest.run_input,
        "schema_version": manifest.schema_version,
        "tool_contracts": [
            {
                "description": tool.description,
                "name": tool.name,
                "parameters": [
                    {
                        "description": parameter.description,
                        "name": parameter.name,
                        "required": parameter.required,
                        "type": parameter.type,
                    }
                    for parameter in tool.parameters
                ],
            }
            for tool in manifest.tool_contracts
        ],
        "workflow_id": manifest.workflow_id,
        "workflow_snapshot": {
            "description": workflow.description,
            "id": workflow.id,
            "name": workflow.name,
            "steps": [
                {
                    "employee": step.employee,
                    "id": step.id,
                    "instructions": step.instructions,
                    "name": step.name,
                }
                for step in workflow.steps
            ],
        },
    }


def _validate_manifest(manifest: object) -> None:
    if type(manifest) is not WorkflowRunManifest:
        _raise_manifest("manifest_type")
    assert isinstance(manifest, WorkflowRunManifest)
    if (
        type(manifest.schema_version) is not str
        or manifest.schema_version != _MANIFEST_SCHEMA_VERSION
    ):
        _raise_manifest("schema_version")
    _validate_run_identity(manifest.run_id)
    if type(manifest.run_input) is not str:
        _raise_manifest("run_input")
    _validate_definition_id(manifest.workflow_id)
    if type(manifest.workflow_snapshot) is not WorkflowDefinitionSnapshot:
        _raise_manifest("workflow_snapshot")
    _validate_workflow_snapshot(manifest.workflow_snapshot)
    if manifest.workflow_id != manifest.workflow_snapshot.id:
        _raise_manifest("workflow_identity")
    if type(manifest.employee_snapshots) is not tuple:
        _raise_manifest("employee_snapshots")
    if any(
        type(item) is not EmployeeDefinitionSnapshot
        for item in manifest.employee_snapshots
    ):
        _raise_manifest("employee_snapshots")
    for item in manifest.employee_snapshots:
        _validate_employee_snapshot(item)
    employee_ids = tuple(item.id for item in manifest.employee_snapshots)
    if len(employee_ids) != len(set(employee_ids)):
        _raise_manifest("duplicate_employee")
    referenced_ids = {step.employee for step in manifest.workflow_snapshot.steps}
    if set(employee_ids) != referenced_ids:
        _raise_manifest("employee_scope")
    if type(manifest.tool_contracts) is not tuple:
        _raise_manifest("tool_contracts")
    if any(type(item) is not ToolContractSnapshot for item in manifest.tool_contracts):
        _raise_manifest("tool_contracts")
    for item in manifest.tool_contracts:
        _validate_tool_contract_snapshot(item)
    tool_names = tuple(item.name for item in manifest.tool_contracts)
    if len(tool_names) != len(set(tool_names)):
        _raise_manifest("duplicate_tool")
    required_tools = {
        tool_name
        for employee in manifest.employee_snapshots
        for tool_name in employee.allowed_tools
    }
    if set(tool_names) != required_tools:
        _raise_manifest("tool_scope")


def _validate_workflow_step_snapshot(snapshot: object) -> None:
    if type(snapshot) is not WorkflowStepSnapshot:
        _raise_manifest("workflow_step")
    assert isinstance(snapshot, WorkflowStepSnapshot)
    _validate_definition_id(snapshot.id)
    _validate_nonblank_text(snapshot.name, "workflow_step_name")
    _validate_definition_id(snapshot.employee)
    _validate_nonblank_text(snapshot.instructions, "workflow_step_instructions")


def _validate_workflow_snapshot(snapshot: object) -> None:
    if type(snapshot) is not WorkflowDefinitionSnapshot:
        _raise_manifest("workflow_snapshot")
    assert isinstance(snapshot, WorkflowDefinitionSnapshot)
    _validate_definition_id(snapshot.id)
    _validate_nonblank_text(snapshot.name, "workflow_name")
    _validate_nonblank_text(snapshot.description, "workflow_description")
    if type(snapshot.steps) is not tuple or not snapshot.steps:
        _raise_manifest("workflow_steps")
    if any(type(step) is not WorkflowStepSnapshot for step in snapshot.steps):
        _raise_manifest("workflow_steps")
    for step in snapshot.steps:
        _validate_workflow_step_snapshot(step)
    step_ids = tuple(step.id for step in snapshot.steps)
    if len(step_ids) != len(set(step_ids)):
        _raise_manifest("duplicate_step")


def _validate_employee_snapshot(snapshot: object) -> None:
    if type(snapshot) is not EmployeeDefinitionSnapshot:
        _raise_manifest("employee_snapshot")
    assert isinstance(snapshot, EmployeeDefinitionSnapshot)
    _validate_definition_id(snapshot.id)
    _validate_nonblank_text(snapshot.name, "employee_name")
    _validate_nonblank_text(snapshot.role, "employee_role")
    _validate_nonblank_text(snapshot.instructions, "employee_instructions")
    _validate_nonblank_text(snapshot.model, "employee_model")
    if type(snapshot.allowed_tools) is not tuple:
        _raise_manifest("employee_tools")
    for tool_name in snapshot.allowed_tools:
        _validate_nonblank_text(tool_name, "employee_tool")
    if len(snapshot.allowed_tools) != len(set(snapshot.allowed_tools)):
        _raise_manifest("duplicate_employee_tool")


def _validate_tool_parameter_snapshot(snapshot: object) -> None:
    if type(snapshot) is not ToolParameterSnapshot:
        _raise_manifest("tool_parameter")
    assert isinstance(snapshot, ToolParameterSnapshot)
    _validate_text(snapshot.name, "tool_parameter_name")
    _validate_text(snapshot.description, "tool_parameter_description")
    _validate_text(snapshot.type, "tool_parameter_type")
    if type(snapshot.required) is not bool:
        _raise_manifest("tool_parameter_required")


def _validate_tool_contract_snapshot(snapshot: object) -> None:
    if type(snapshot) is not ToolContractSnapshot:
        _raise_manifest("tool_contract")
    assert isinstance(snapshot, ToolContractSnapshot)
    _validate_nonblank_text(snapshot.name, "tool_name")
    _validate_text(snapshot.description, "tool_description")
    if type(snapshot.parameters) is not tuple:
        _raise_manifest("tool_parameters")
    if any(
        type(parameter) is not ToolParameterSnapshot
        for parameter in snapshot.parameters
    ):
        _raise_manifest("tool_parameters")
    for parameter in snapshot.parameters:
        _validate_tool_parameter_snapshot(parameter)
    parameter_names = tuple(parameter.name for parameter in snapshot.parameters)
    if len(parameter_names) != len(set(parameter_names)):
        _raise_manifest("duplicate_tool_parameter")


def _validate_run_identity(value: object) -> None:
    if type(value) is not str or _RUN_ID_PATTERN.fullmatch(value) is None:
        _raise_manifest("run_id")


def _validate_definition_id(value: object) -> None:
    if type(value) is not str or _DEFINITION_ID_PATTERN.fullmatch(value) is None:
        _raise_manifest("definition_id")


def _validate_nonblank_text(value: object, classification: str) -> None:
    _validate_text(value, classification)
    assert isinstance(value, str)
    if not value.strip():
        _raise_manifest(classification)


def _validate_text(value: object, classification: str) -> None:
    if type(value) is not str:
        _raise_manifest(classification)


def _validate_persistence_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_persistence("path_type")
    assert isinstance(path, Path)
    try:
        if not path.parent.exists() or not path.parent.is_dir():
            _raise_persistence("parent")
        if path.is_symlink() or path.is_dir() or (path.exists() and not path.is_file()):
            _raise_persistence("target")
    except WorkflowRunManifestPersistenceError:
        raise
    except Exception:
        _raise_persistence("target")


def _validate_storage_root(root: object) -> None:
    if type(root) is not _PATH_TYPE:
        _raise_manifest("storage_root")
    assert isinstance(root, Path)
    try:
        if root.is_symlink() or not root.exists() or not root.is_dir():
            _raise_manifest("storage_root")
    except WorkflowRunManifestError:
        raise
    except Exception:
        _raise_manifest("storage_root")


def _validate_store(store: object) -> None:
    if type(store) is not WorkflowRunManifestStore:
        _raise_manifest("store")
    assert isinstance(store, WorkflowRunManifestStore)
    _validate_storage_root(store.root)


def _manifest_path(store: WorkflowRunManifestStore, run_id: str) -> Path:
    _validate_store(store)
    _validate_run_identity(run_id)
    return store.root / f"{run_id}.manifest.json"


def _validate_load_path(path: object) -> None:
    if type(path) is not _PATH_TYPE:
        _raise_load("path_type")
    assert isinstance(path, Path)
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_load("target")
    except WorkflowRunManifestLoadError:
        raise
    except Exception:
        _raise_load("target")


def _persist_new_manifest(handle: object, directory: Path, contents: bytes) -> None:
    try:
        with _manifest_handle_scope(handle) as active_handle:
            written = active_handle.write(contents)  # type: ignore[attr-defined]
            if type(written) is not int or written != len(contents):
                raise OSError("short manifest write")
            active_handle.flush()  # type: ignore[attr-defined]
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_manifest_directory(directory)
    except Exception:
        _raise_persistence("ambiguous")


def _persist_existing_manifest(path: Path, contents: bytes) -> None:
    try:
        if (
            path.is_symlink()
            or not path.exists()
            or path.is_dir()
            or not path.is_file()
        ):
            _raise_persistence("target")
        existing = path.read_bytes()
        if type(existing) is not bytes:
            _raise_persistence("target")
    except WorkflowRunManifestPersistenceError:
        raise
    except Exception:
        _raise_persistence("target")

    if existing != contents:
        _raise_conflict()

    try:
        handle = path.open("rb")
    except Exception:
        _raise_persistence("ambiguous")

    try:
        with _manifest_handle_scope(handle) as active_handle:
            os.fsync(active_handle.fileno())  # type: ignore[attr-defined]
    except Exception:
        _raise_persistence("ambiguous")

    try:
        _fsync_manifest_directory(path.parent)
    except Exception:
        _raise_persistence("ambiguous")


@contextmanager
def _manifest_handle_scope(handle: object) -> Iterator[object]:
    enter = getattr(handle, "__enter__", None)
    exit_ = getattr(handle, "__exit__", None)
    if callable(enter) and callable(exit_):
        with handle as active_handle:  # type: ignore[union-attr]
            yield active_handle
        return

    try:
        yield handle
    finally:
        close = getattr(handle, "close", None)
        if not callable(close):
            raise OSError("manifest handle cannot close")
        close()


def _fsync_manifest_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if directory_flag:
        flags |= directory_flag
    descriptor = os.open(os.fspath(directory), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _parse_manifest(value: object) -> WorkflowRunManifest:
    if type(value) is not dict or frozenset(value) != _MANIFEST_KEYS:
        _raise_load("keys")
    try:
        workflow_value = value["workflow_snapshot"]
        if (
            type(workflow_value) is not dict
            or frozenset(workflow_value) != _WORKFLOW_KEYS
        ):
            _raise_load("workflow_snapshot")
        step_values = workflow_value["steps"]
        if type(step_values) is not list:
            _raise_load("workflow_steps")
        steps = tuple(_parse_workflow_step(item) for item in step_values)

        employee_values = value["employee_snapshots"]
        if type(employee_values) is not list:
            _raise_load("employee_snapshots")
        employees = tuple(_parse_employee(item) for item in employee_values)

        tool_values = value["tool_contracts"]
        if type(tool_values) is not list:
            _raise_load("tool_contracts")
        tools = tuple(_parse_tool(item) for item in tool_values)

        return WorkflowRunManifest(
            schema_version=value["schema_version"],
            run_id=value["run_id"],
            workflow_id=value["workflow_id"],
            run_input=value["run_input"],
            workflow_snapshot=WorkflowDefinitionSnapshot(
                id=workflow_value["id"],
                name=workflow_value["name"],
                description=workflow_value["description"],
                steps=steps,
            ),
            employee_snapshots=employees,
            tool_contracts=tools,
        )
    except WorkflowRunManifestLoadError:
        raise
    except WorkflowRunManifestError:
        _raise_load("manifest")
    except Exception:
        _raise_load("manifest")


def _parse_workflow_step(value: object) -> WorkflowStepSnapshot:
    if type(value) is not dict or frozenset(value) != _WORKFLOW_STEP_KEYS:
        _raise_load("workflow_step")
    try:
        return WorkflowStepSnapshot(
            id=value["id"],
            name=value["name"],
            employee=value["employee"],
            instructions=value["instructions"],
        )
    except Exception:
        _raise_load("workflow_step")


def _parse_employee(value: object) -> EmployeeDefinitionSnapshot:
    if type(value) is not dict or frozenset(value) != _EMPLOYEE_KEYS:
        _raise_load("employee")
    tools = value["allowed_tools"]
    if type(tools) is not list:
        _raise_load("employee_tools")
    try:
        return EmployeeDefinitionSnapshot(
            id=value["id"],
            name=value["name"],
            role=value["role"],
            instructions=value["instructions"],
            model=value["model"],
            allowed_tools=tuple(tools),
        )
    except Exception:
        _raise_load("employee")


def _parse_tool(value: object) -> ToolContractSnapshot:
    if type(value) is not dict or frozenset(value) != _TOOL_KEYS:
        _raise_load("tool")
    parameters = value["parameters"]
    if type(parameters) is not list:
        _raise_load("tool_parameters")
    parsed_parameters = tuple(_parse_tool_parameter(item) for item in parameters)
    try:
        return ToolContractSnapshot(
            name=value["name"],
            description=value["description"],
            parameters=parsed_parameters,
        )
    except Exception:
        _raise_load("tool")


def _parse_tool_parameter(value: object) -> ToolParameterSnapshot:
    if type(value) is not dict or frozenset(value) != _TOOL_PARAMETER_KEYS:
        _raise_load("tool_parameter")
    try:
        return ToolParameterSnapshot(
            name=value["name"],
            description=value["description"],
            type=value["type"],
            required=value["required"],
        )
    except Exception:
        _raise_load("tool_parameter")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


class _DuplicateKeyError(ValueError):
    pass


class _NonStandardJSONConstantError(ValueError):
    pass


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    del value
    raise _NonStandardJSONConstantError


def _raise_manifest(classification: str) -> NoReturn:
    raise WorkflowRunManifestError(classification) from None


def _raise_persistence(classification: str) -> NoReturn:
    raise WorkflowRunManifestPersistenceError(classification) from None


def _raise_conflict() -> NoReturn:
    raise WorkflowRunManifestConflictError("conflict") from None


def _raise_load(classification: str) -> NoReturn:
    raise WorkflowRunManifestLoadError(classification) from None


__all__ = [
    "EmployeeDefinitionSnapshot",
    "ToolContractSnapshot",
    "ToolParameterSnapshot",
    "WorkflowDefinitionSnapshot",
    "WorkflowRunManifest",
    "WorkflowRunManifestConflictError",
    "WorkflowRunManifestError",
    "WorkflowRunManifestFailureDetail",
    "WorkflowRunManifestLoadError",
    "WorkflowRunManifestPersistenceError",
    "WorkflowRunManifestStore",
    "WorkflowStepSnapshot",
    "build_workflow_run_manifest",
    "create_workflow_run_manifest",
    "employee_definitions_from_run_manifest",
    "loaded_employees_from_run_manifest",
    "loaded_workflow_from_run_manifest",
    "load_workflow_run_manifest",
    "persist_workflow_run_manifest",
    "serialize_workflow_run_manifest_canonical",
    "tool_catalog_from_run_manifest",
    "workflow_definition_from_run_manifest",
    "workflow_run_manifest_canonical_bytes",
    "workflow_run_manifest_digest",
]
