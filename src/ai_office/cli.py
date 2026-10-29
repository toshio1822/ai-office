"""Command-line interface for AI Office."""

import base64
import difflib
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote, urlsplit

import typer

from ai_office.definitions.employee import (
    EmployeeDefinition,
    EmployeeLoadError,
    load_employees,
)
from ai_office.definitions.style_profile import (
    StyleProfileLoadError,
    add_style_profile_to_assignment,
    load_style_profile,
)
from ai_office.definitions.workflow import (
    LoadedWorkflow,
    WorkflowLoadError,
    load_workflows,
    validate_workflow_employee_references,
)
from ai_office.engine import (
    ApprovedWorkflowBootstrapContext,
    InitialStepPreparationApproval,
    NextStepPreparationApproval,
    WorkflowRunManifestError,
    WorkflowRunManifestPersistenceError,
    WorkflowRunManifestStore,
    approve_business_step,
    build_immediate_predecessor_upstream_inputs,
    build_persisted_continuation_runtime_facts,
    build_workflow_run_manifest,
    find_business_approval_evidence,
    list_run_approval_evidence,
    load_workflow_run_manifest,
    loaded_employees_from_run_manifest,
    loaded_workflow_from_run_manifest,
    persist_workflow_run_manifest,
    route_approved_workflow_continuation_cycle,
    route_approved_workflow_fresh_start,
    route_persisted_execution_outcome_reentry,
    tool_catalog_from_run_manifest,
)
from ai_office.engine.artifact import (
    WorkflowArtifact,
    WorkflowArtifactError,
    export_run_artifact,
    list_run_artifacts,
    read_run_artifact,
)
from ai_office.engine.local_execution import (
    FILE_CHANGE_SET_CONTENT_TYPE,
    LocalExecutionError,
    LocalExecutionPolicy,
    _parse_change_set,
    execute_local_change,
    issue_local_execution_authorization,
    load_local_execution_authorization,
    load_local_execution_evidence,
    validate_local_execution_scope,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_job_authorization import (
    inspect_workflow_job_authorization,
    load_workflow_job_authorization_by_identity,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.engineering_evidence import (
    EngineeringEvidenceError,
    collect_engineering_evidence,
)
from ai_office.execution_destination import (
    ExecutionDestinationError,
    load_execution_destination_registry,
)
from ai_office.execution_evidence import (
    execution_normalized_result_evidence_path,
    execution_raw_response_evidence_path,
    inspect_run_execution_evidence,
    load_execution_attempt_evidence,
    load_normalized_result_evidence,
    load_raw_response_evidence,
)
from ai_office.execution_target import (
    ModelExecutionTarget,
    ModelExecutionTargetError,
    execution_target_fingerprint,
    execution_target_for_name,
)
from ai_office.github_snapshot import (
    GitHubSnapshotError,
    collect_github_change_snapshot,
)
from ai_office.invocation import (
    EMPTY_RUNTIME_FACTS,
    ModelInvocationExecutionApproval,
    ModelInvocationRequest,
    RuntimeFactsSnapshot,
    approve_model_invocation_execution,
    build_model_invocation_execution_fingerprint,
    build_model_invocation_request,
    build_model_invocation_task_input,
    runtime_facts_snapshot_digest,
    serialize_runtime_facts_snapshot_canonical,
)
from ai_office.planning.execution_plan import (
    ExecutionPlan,
    WorkflowSelectionError,
    build_execution_plan,
    find_workflow_by_id,
)
from ai_office.planning.step_execution_request import (
    EmployeeSelectionError,
    StepExecutionRequest,
    StepSelectionError,
    build_step_execution_request,
    find_employee_by_id,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesFunctionTool,
    OpenAIResponsesPayload,
    OpenAIResponsesRequest,
    build_openai_responses_http_request_from_invocation,
    build_openai_responses_payload_dict_from_invocation,
    build_openai_responses_payload_from_invocation,
    build_openai_responses_request,
    build_openai_responses_tools,
    load_api_key_for_execution_target,
    load_openai_api_key_from_environment,
    send_openai_responses_http_request,
    serialize_openai_responses_payload_dict_pretty,
)
from ai_office.providers.openai.responses_dict_payload import JsonValue
from ai_office.runtime import WorkflowRunBinding, binding_of
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_history_with_source_digests,
)
from ai_office.tools import (
    DEFAULT_TOOL_CATALOG,
    ToolCatalog,
    ToolCatalogError,
    ToolDefinition,
    resolve_tool_names,
)

MAX_RUN_INPUT_FILE_BYTES = 1_048_576

app = typer.Typer(
    name="ai-office",
    help="人間が定義したワークフローを扱う AI 業務基盤。",
    no_args_is_help=True,
)
employees_app = typer.Typer(help="社員定義を読み込み、検証する。")
workflows_app = typer.Typer(help="ワークフロー定義を読み込み、検証する。")
app.add_typer(employees_app, name="employees")
app.add_typer(workflows_app, name="workflows")


@app.callback()
def main() -> None:
    """AI Office のコマンド群。"""


def _load_employees_or_exit(directory: Path):
    try:
        return load_employees(directory)
    except EmployeeLoadError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None


@employees_app.command("list")
def list_employees(
    directory: Path = typer.Option(Path("employees"), "--directory"),
) -> None:
    """List all validated employee definitions."""
    employees = _load_employees_or_exit(directory)
    if not employees:
        typer.echo("No employee definitions found.")
        return

    for employee in employees:
        definition = employee.definition
        typer.echo(f"{definition.id}\t{definition.name}\t{definition.role}")


@employees_app.command("validate")
def validate_employees(
    directory: Path = typer.Option(Path("employees"), "--directory"),
) -> None:
    """Validate all employee definitions."""
    employees = _load_employees_or_exit(directory)
    typer.echo(f"Validated {len(employees)} employee definition(s).")


def _load_validated_definitions_or_exit(directory: Path, employees_directory: Path):
    try:
        workflows = load_workflows(directory)
        employees = load_employees(employees_directory)
        validate_workflow_employee_references(workflows, employees)
        return workflows, employees
    except (EmployeeLoadError, WorkflowLoadError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None


def _load_workflows_or_exit(directory: Path, employees_directory: Path):
    workflows, _ = _load_validated_definitions_or_exit(directory, employees_directory)
    return workflows


@workflows_app.command("list")
def list_workflows(
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """List all validated workflow definitions."""
    workflows = _load_workflows_or_exit(directory, employees_directory)
    if not workflows:
        typer.echo("No workflow definitions found.")
        return

    for workflow in workflows:
        definition = workflow.definition
        typer.echo(f"{definition.id}\t{definition.name}\t{len(definition.steps)}")


@workflows_app.command("validate")
def validate_workflows(
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Validate all workflow definitions and employee references."""
    workflows = _load_workflows_or_exit(directory, employees_directory)
    step_count = sum(len(workflow.definition.steps) for workflow in workflows)
    typer.echo(
        f"Validated {len(workflows)} workflow definition(s) with {step_count} step(s)."
    )


def _display_execution_plan(plan: ExecutionPlan) -> None:
    """Display a human-readable execution plan without modifying its values."""
    typer.echo(f"Workflow: {plan.workflow_id}")
    typer.echo(f"Name: {plan.workflow_name}")
    typer.echo(f"Steps: {len(plan.steps)}")

    for step in plan.steps:
        typer.echo()
        typer.echo(f"{step.index}. {step.step_id}")
        typer.echo(f"   Name: {step.step_name}")
        typer.echo(f"   Employee: {step.employee_id}")
        typer.echo(f"   Model: {step.model}")
        typer.echo("   Instructions:")
        for line in step.instructions.splitlines():
            typer.echo(f"     {line}")


def _display_indented_value(value: str) -> None:
    """Display a value line by line without modifying its contents."""
    for line in value.splitlines():
        typer.echo(f"  {line}")


def _display_indented_value_with_terminal_newlines(value: str) -> None:
    """Display a value while preserving terminal newline lines for OpenAI output."""
    for line in value.split("\n"):
        typer.echo(f"  {line}")


def _display_step_execution_request(request: StepExecutionRequest) -> None:
    """Display one structured step execution request without running it."""
    typer.echo(f"Workflow: {request.workflow_id}")
    typer.echo(f"Name: {request.workflow_name}")
    typer.echo(f"Step: {request.step_index}. {request.step_id}")
    typer.echo(f"Step name: {request.step_name}")
    typer.echo(f"Employee: {request.employee_id}")
    typer.echo(f"Employee name: {request.employee_name}")
    typer.echo("Role:")
    _display_indented_value(request.employee_role)
    typer.echo(f"Model: {request.model}")
    tools = ", ".join(request.allowed_tools) if request.allowed_tools else "none"
    typer.echo(f"Allowed tools: {tools}")
    typer.echo("Employee instructions:")
    _display_indented_value(request.employee_instructions)
    typer.echo("Step instructions:")
    _display_indented_value(request.step_instructions)


def _display_model_invocation_request(request: ModelInvocationRequest) -> None:
    """Display a provider-independent invocation request without running it."""
    typer.echo(f"Model: {request.model}")
    typer.echo("Allowed tools:")
    if request.allowed_tools:
        for tool in request.allowed_tools:
            typer.echo(f"  {tool}")
    else:
        typer.echo("  none")
    typer.echo("System instructions:")
    _display_indented_value(request.system_instructions)
    typer.echo("Task instructions:")
    _display_indented_value(request.task_instructions)


def _display_openai_responses_request(request: OpenAIResponsesRequest) -> None:
    """Display one OpenAI pre-runtime request without creating a wire payload."""
    typer.echo("Provider: openai")
    typer.echo(f"Model: {request.model}")
    typer.echo("Allowed tool names:")
    if request.allowed_tool_names:
        for tool_name in request.allowed_tool_names:
            typer.echo(f"  {tool_name}")
    else:
        typer.echo("  none")
    typer.echo("Instructions:")
    _display_indented_value_with_terminal_newlines(request.instructions)
    typer.echo("Input:")
    _display_indented_value_with_terminal_newlines(request.input)


def _display_resolved_tools(tools: tuple[ToolDefinition, ...]) -> None:
    """Display resolved static tool definitions without creating provider schemas."""
    typer.echo("Resolved tools:")
    if not tools:
        typer.echo("  none")
        return

    for tool in tools:
        typer.echo(f"  {tool.name}")
        typer.echo(f"    Description: {tool.description}")
        typer.echo("    Parameters:")
        if not tool.parameters:
            typer.echo("      none")
            continue
        for parameter in tool.parameters:
            typer.echo(f"      {parameter.name}")
            typer.echo(f"        Type: {parameter.type}")
            required = "yes" if parameter.required else "no"
            typer.echo(f"        Required: {required}")
            typer.echo(f"        Description: {parameter.description}")


def _display_openai_responses_tools(
    tools: tuple[OpenAIResponsesFunctionTool, ...],
) -> None:
    """Display static OpenAI tool schema models without producing a payload."""
    typer.echo("Provider: openai")
    typer.echo("Tools:")
    if not tools:
        typer.echo("  none")
        return
    for tool in tools:
        typer.echo(f"  Type: {tool.type}")
        typer.echo(f"  Name: {tool.name}")
        typer.echo(f"  Description: {tool.description}")
        typer.echo(f"  Strict: {'yes' if tool.strict else 'no'}")
        typer.echo("  Parameters:")
        typer.echo(f"    Type: {tool.parameters.type}")
        additional = "yes" if tool.parameters.additional_properties else "no"
        typer.echo(f"    Additional properties: {additional}")
        typer.echo("    Properties:")
        if tool.parameters.properties:
            for property_definition in tool.parameters.properties:
                typer.echo(f"      {property_definition.name}")
                typer.echo(f"        Type: {property_definition.type}")
                typer.echo(f"        Description: {property_definition.description}")
        else:
            typer.echo("      none")
        typer.echo("    Required:")
        if tool.parameters.required:
            for required_name in tool.parameters.required:
                typer.echo(f"      {required_name}")
        else:
            typer.echo("      none")


def _display_openai_responses_payload(payload: OpenAIResponsesPayload) -> None:
    """Display a static payload model without creating a wire-format payload."""
    typer.echo("Provider: openai")
    typer.echo("Payload:")
    typer.echo(f"  Model: {payload.model}")
    typer.echo("  Instructions:")
    _display_payload_text(payload.instructions)
    typer.echo("  Input:")
    _display_payload_text(payload.input)
    typer.echo("  Tools:")
    if not payload.tools:
        typer.echo("    none")
        return
    for tool in payload.tools:
        typer.echo(f"    Type: {tool.type}")
        typer.echo(f"    Name: {tool.name}")
        typer.echo(f"    Description: {tool.description}")
        typer.echo(f"    Strict: {'yes' if tool.strict else 'no'}")
        typer.echo("    Parameters:")
        typer.echo(f"      Type: {tool.parameters.type}")
        additional = "yes" if tool.parameters.additional_properties else "no"
        typer.echo(f"      Additional properties: {additional}")
        typer.echo("      Properties:")
        if tool.parameters.properties:
            for property_definition in tool.parameters.properties:
                typer.echo(f"        {property_definition.name}")
                typer.echo(f"          Type: {property_definition.type}")
                typer.echo(f"          Description: {property_definition.description}")
        else:
            typer.echo("        none")
        typer.echo("      Required:")
        if tool.parameters.required:
            for required_name in tool.parameters.required:
                typer.echo(f"        {required_name}")
        else:
            typer.echo("        none")


def _display_dictionary_payload_text(value: str, indent: str) -> None:
    """Display a dictionary string value without changing its line structure."""
    if value == "":
        typer.echo(f"{indent}<empty>")
        return
    for line in value.split("\n"):
        typer.echo(f"{indent}{line}")


def _display_openai_responses_dictionary_payload(
    payload: dict[str, JsonValue],
) -> None:
    """Display a dictionary payload structurally without serializing it as JSON."""
    typer.echo("Provider: openai")
    typer.echo("Dictionary payload:")
    typer.echo(f"  model: {payload['model']}")
    typer.echo("  instructions:")
    _display_dictionary_payload_text(payload["instructions"], "    ")
    typer.echo("  input:")
    _display_dictionary_payload_text(payload["input"], "    ")

    tools = payload["tools"]
    if not tools:
        typer.echo("  tools: []")
        return

    typer.echo("  tools:")
    for tool in tools:
        typer.echo(f"    - type: {tool['type']}")
        typer.echo(f"      name: {tool['name']}")
        typer.echo(f"      description: {tool['description']}")
        typer.echo("      parameters:")
        parameters = tool["parameters"]
        typer.echo(f"        type: {parameters['type']}")
        properties = parameters["properties"]
        if not properties:
            typer.echo("        properties: {}")
        else:
            typer.echo("        properties:")
            for property_name, property_value in properties.items():
                typer.echo(f"          {property_name}:")
                typer.echo(f"            type: {property_value['type']}")
                typer.echo(f"            description: {property_value['description']}")
        required = parameters["required"]
        if not required:
            typer.echo("        required: []")
        else:
            typer.echo("        required:")
            for required_name in required:
                typer.echo(f"          - {required_name}")
        additional_properties = parameters["additionalProperties"]
        typer.echo(
            "        additionalProperties: "
            f"{'true' if additional_properties else 'false'}"
        )
        typer.echo(f"      strict: {'true' if tool['strict'] else 'false'}")


def _display_payload_text(value: str) -> None:
    """Display a payload string while preserving every newline line."""
    if value == "":
        typer.echo("    <empty>")
        return
    for line in value.split("\n"):
        typer.echo(f"    {line}")


@dataclass(frozen=True)
class _WorkflowStepPreview:
    """The exact public request values shown before one step execution."""

    step_request: StepExecutionRequest
    invocation_request: ModelInvocationRequest
    employee: EmployeeDefinition
    resolved_tools: tuple[ToolDefinition, ...]
    request_fingerprint: str
    execution_target: ModelExecutionTarget
    business_approval_required: bool
    run_binding: WorkflowRunBinding | None = None
    manifest_store: WorkflowRunManifestStore | None = None


def _workflow_cli_error(message: str, *, code: int = 2) -> NoReturn:
    """Report one safe workflow-command error and stop without a traceback."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=code)


def _load_workflow_command_inputs(
    directory: Path, employees_directory: Path
) -> tuple[list[object], list[object]]:
    """Load and validate all definitions for a real workflow command."""
    try:
        workflows = load_workflows(directory)
        employees = load_employees(employees_directory)
        validate_workflow_employee_references(workflows, employees)
    except (EmployeeLoadError, WorkflowLoadError):
        _workflow_cli_error("workflow definitions are invalid")
    except Exception:
        _workflow_cli_error("workflow definitions could not be loaded")
    return workflows, employees


def _prepare_run_store(root: Path) -> WorkflowRunManifestStore:
    """Create the one authoritative Run namespace before Run creation."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        return WorkflowRunManifestStore(root)
    except (OSError, WorkflowRunManifestError):
        _workflow_cli_error("Run storage namespace is invalid")


def _load_run_store_or_exit(root: Path) -> WorkflowRunManifestStore:
    """Open an existing authoritative Run namespace without creating it."""
    try:
        return WorkflowRunManifestStore(root)
    except (OSError, WorkflowRunManifestError):
        _workflow_cli_error("Run storage namespace is invalid or unavailable")


def _load_run_manifest_or_exit(store: WorkflowRunManifestStore, run_id: str) -> object:
    """Load one exact immutable Manifest without consulting live definitions."""
    try:
        return load_workflow_run_manifest(store, run_id)
    except (WorkflowRunManifestError, OSError):
        _workflow_cli_error("Run Manifest is invalid or unavailable")


def _run_binding_for_manifest(manifest: object) -> WorkflowRunBinding:
    """Build the direct binding carried by every Run-owned execution value."""
    try:
        return WorkflowRunBinding(
            manifest.run_id,
            manifest.digest,
        )
    except (AttributeError, TypeError, ValueError):
        _workflow_cli_error("Run Manifest binding is invalid")


def _pinned_run_inputs(
    manifest: object,
) -> tuple[list[object], list[object], object]:
    """Reconstruct only the validated semantic models frozen by a Manifest."""
    try:
        workflow = loaded_workflow_from_run_manifest(manifest)
        employees = loaded_employees_from_run_manifest(manifest)
        catalog = tool_catalog_from_run_manifest(manifest)
    except Exception:
        _workflow_cli_error("Run Manifest semantic snapshot is invalid")
    return [workflow], list(employees), catalog


def _build_start_manifest_preview(
    workflow_id: str,
    run_id: str | None,
    run_input: str | None,
    workflows: list[object],
    employees: list[object],
    step_model_values: list[str] | None = None,
    execution_target: ModelExecutionTarget | None = None,
    execution_targets: tuple[ModelExecutionTarget, ...] | None = None,
) -> tuple[object, WorkflowRunBinding, object, list[object], object]:
    """Freeze fresh Run meaning in memory before preview or durable creation."""
    if run_id is None or run_input is None:
        _workflow_cli_error("--run-id and --run-input are required")
    try:
        workflow = _apply_step_model_overrides(
            find_workflow_by_id(workflows, workflow_id), step_model_values
        )
        manifest = build_workflow_run_manifest(
            run_id,
            run_input,
            workflow,
            employees,
            tool_catalog=DEFAULT_TOOL_CATALOG,
            execution_target=(execution_target if execution_targets is None else None),
            execution_targets=execution_targets,
        )
        binding = _run_binding_for_manifest(manifest)
        pinned_workflows, pinned_employees, catalog = _pinned_run_inputs(manifest)
        return manifest, binding, pinned_workflows, pinned_employees, catalog
    except (WorkflowSelectionError, WorkflowRunManifestError):
        _workflow_cli_error("fresh Run definition or input is invalid")


def _resolve_run_input(run_input: str | None, run_input_file: Path | None) -> str:
    """Select one exact Run input without exposing file contents in argv."""
    if (run_input is None) == (run_input_file is None):
        _workflow_cli_error("provide exactly one of --run-input or --run-input-file")
    if run_input_file is None:
        assert run_input is not None
        return run_input
    try:
        if not run_input_file.is_file():
            _workflow_cli_error("Run input file is unavailable or invalid UTF-8")
        with run_input_file.open("rb") as source:
            content = source.read(MAX_RUN_INPUT_FILE_BYTES + 1)
        if len(content) > MAX_RUN_INPUT_FILE_BYTES:
            _workflow_cli_error("Run input file exceeds the size limit")
        return content.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        _workflow_cli_error("Run input file is unavailable or invalid UTF-8")


def _persist_start_manifest_or_exit(
    store: WorkflowRunManifestStore, manifest: object
) -> None:
    """Durably commit the exact preview meaning before credentials/provider work."""
    try:
        persist_workflow_run_manifest(store, manifest)
    except (WorkflowRunManifestError, WorkflowRunManifestPersistenceError):
        _workflow_cli_error("Run Manifest could not be committed")


def _build_workflow_step_preview(
    workflows: list[object],
    employees: list[object],
    workflow_id: str,
    step_index: int,
    upstream_inputs: tuple[object, ...] = (),
    execution_target: ModelExecutionTarget | None = None,
    runtime_facts: RuntimeFactsSnapshot = EMPTY_RUNTIME_FACTS,
    *,
    run_binding: WorkflowRunBinding | None = None,
    run_input: str | None = None,
    tool_catalog: ToolCatalog = DEFAULT_TOOL_CATALOG,
) -> tuple[object, _WorkflowStepPreview]:
    """Construct one exact step request through the existing public seams."""
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(
            plan,
            step_index,
            employees,
            run_id=None if run_binding is None else run_binding.run_id,
            manifest_digest=(
                None if run_binding is None else run_binding.manifest_digest
            ),
        )
        selected_employee = find_employee_by_id(employees, step_request.employee_id)
        invocation_request = build_model_invocation_request(
            step_request,
            upstream_inputs=upstream_inputs,  # type: ignore[arg-type]
            runtime_facts=runtime_facts,
            run_input=run_input,
        )
        resolved_tools = resolve_tool_names(
            tool_catalog, invocation_request.allowed_tools
        )
        target = (
            execution_target
            if execution_target is not None
            else execution_target_for_name("openai")
        )
        fingerprint = build_model_invocation_execution_fingerprint(
            invocation_request, resolved_tools, target
        )
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
        ToolCatalogError,
    ):
        _workflow_cli_error("workflow step preview is invalid")
    except Exception:
        _workflow_cli_error("workflow step preview could not be built")

    return workflow, _WorkflowStepPreview(
        step_request=step_request,
        invocation_request=invocation_request,
        employee=selected_employee.definition,
        resolved_tools=resolved_tools,
        request_fingerprint=fingerprint,
        execution_target=target,
        business_approval_required=workflow.definition.steps[
            step_index - 1
        ].business_approval_required,
        run_binding=run_binding,
    )


def _resolve_execution_target(
    name: str, registry_path: Path | None = None
) -> ModelExecutionTarget:
    """Resolve a built-in or administrator-registered target before credentials."""
    try:
        if name in {"openai", "omniroute"}:
            if registry_path is not None:
                raise ExecutionDestinationError("built-in target needs no registry")
            return execution_target_for_name(name)
        if registry_path is None:
            raise ExecutionDestinationError(
                "execution destination registry is required"
            )
        return load_execution_destination_registry(registry_path).resolve(name).target
    except (ExecutionDestinationError, ModelExecutionTargetError, TypeError):
        _workflow_cli_error("execution target is invalid")


def _resolve_workflow_execution_targets(
    workflow: LoadedWorkflow,
    fallback_name: str,
    registry_path: Path | None,
) -> tuple[ModelExecutionTarget, ...]:
    """Resolve Step, Workflow-default, then legacy CLI destination choices."""
    targets: list[ModelExecutionTarget] = []
    for step in workflow.definition.steps:
        selected_name = (
            step.execution_destination
            or workflow.definition.execution_destination
            or fallback_name
        )
        selected_registry = (
            None if selected_name in {"openai", "omniroute"} else registry_path
        )
        targets.append(_resolve_execution_target(selected_name, selected_registry))
    return tuple(targets)


def _validate_manifest_execution_destinations(
    manifest: object, registry_path: Path | None
) -> None:
    """Fail closed if any pinned Step target or its model policy has drifted."""
    try:
        for index, step in enumerate(manifest.workflow_snapshot.steps, start=1):
            target = manifest.execution_destination_for_step(index).target
            _validate_pinned_models_for_target(target, registry_path, [step.model])
    except (AttributeError, WorkflowRunManifestError):
        _workflow_cli_error("Run Manifest execution destinations are invalid")


def _parse_step_model_overrides(values: list[str] | None) -> dict[str, str]:
    """Parse repeated STEP_ID=MODEL choices without selecting a destination."""
    overrides: dict[str, str] = {}
    for value in values or []:
        step_id, separator, model = value.partition("=")
        if not separator or not step_id or not model.strip() or step_id in overrides:
            _workflow_cli_error("--step-model must be unique STEP_ID=MODEL values")
        overrides[step_id] = model
    return overrides


def _apply_step_model_overrides(
    workflow: LoadedWorkflow, values: list[str] | None
) -> LoadedWorkflow:
    """Return a definition whose explicit per-Step choices override defaults."""
    overrides = _parse_step_model_overrides(values)
    step_ids = {step.id for step in workflow.definition.steps}
    if set(overrides) - step_ids:
        _workflow_cli_error("--step-model names an unknown Workflow Step")
    if not overrides:
        return workflow
    steps = [
        step.model_copy(update={"model": overrides.get(step.id, step.model)})
        for step in workflow.definition.steps
    ]
    return LoadedWorkflow(
        source_path=workflow.source_path,
        definition=workflow.definition.model_copy(update={"steps": steps}),
    )


def _reject_legacy_execution_model(model: str | None) -> None:
    if model is not None:
        _workflow_cli_error(
            "--execution-model no longer overrides an entire Run; use "
            "--step-model STEP_ID=MODEL (otherwise Workflow Step defaults apply)"
        )


def _validate_pinned_models_for_target(
    target: ModelExecutionTarget,
    registry_path: Path | None,
    models: list[str],
) -> None:
    """Require every manifest-pinned model to remain permitted."""
    if target.provider in {"openai", "omniroute"}:
        return
    if registry_path is None:
        _workflow_cli_error("execution target is invalid")
    try:
        destination = load_execution_destination_registry(registry_path).resolve(
            target.provider
        )
        for model in models:
            destination.select_model(model)
        if destination.target != target:
            _workflow_cli_error("approved execution destination is no longer permitted")
    except ExecutionDestinationError:
        _workflow_cli_error("pinned execution model is no longer permitted")


def _load_api_key_for_target(target: ModelExecutionTarget) -> OpenAIApiKey:
    """Keep direct-OpenAI compatibility while selecting target credentials."""
    if target.provider == "openai":
        return load_openai_api_key_from_environment()
    return load_api_key_for_execution_target(target)


def _select_workflow_or_exit(workflows: list[object], workflow_id: str) -> object:
    """Select the requested loaded workflow without inspecting execution state."""
    try:
        return find_workflow_by_id(workflows, workflow_id)
    except WorkflowSelectionError:
        _workflow_cli_error("workflow selection is invalid")


def _resolved_tools_json(
    tools: tuple[ToolDefinition, ...],
) -> list[dict[str, object]]:
    """Copy static tool definitions into the safe preview representation."""
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": [
                {
                    "name": parameter.name,
                    "description": parameter.description,
                    "type": parameter.type,
                    "required": parameter.required,
                }
                for parameter in tool.parameters
            ],
        }
        for tool in tools
    ]


def _emit_json(value: dict[str, object]) -> None:
    """Emit exactly one deterministic, secret-free JSON line."""
    typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _unique_json_object(items: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate keys in operator-supplied JSON policy files."""
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


@workflows_app.command("snapshot-github-change")
def snapshot_github_change(
    repository: str = typer.Option(..., "--repository"),
    issue_number: int = typer.Option(..., "--issue"),
    pull_number: int = typer.Option(..., "--pull"),
    expected_head_sha: str = typer.Option(..., "--expected-head-sha"),
    output: Path = typer.Option(..., "--output"),
    observed_at: str | None = typer.Option(None, "--observed-at"),
    token_environment_variable: str = typer.Option(
        "GITHUB_TOKEN", "--token-environment-variable"
    ),
) -> None:
    """Collect one revision-bound, read-only GitHub snapshot for workflow input."""
    if not token_environment_variable.isidentifier():
        _workflow_cli_error("GitHub token environment variable name is invalid")
    observation_time = observed_at or datetime.now(UTC).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    try:
        snapshot = collect_github_change_snapshot(
            repository=repository,
            issue_number=issue_number,
            pull_number=pull_number,
            expected_head_sha=expected_head_sha,
            observed_at=observation_time,
            token=os.environ.get(token_environment_variable),
        )
        with output.open("x", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(snapshot.markdown)
    except GitHubSnapshotError as error:
        _workflow_cli_error(str(error))
    except FileExistsError:
        _workflow_cli_error("GitHub snapshot output already exists")
    except OSError:
        _workflow_cli_error("GitHub snapshot output could not be written")

    _emit_json(
        {
            "base_sha": snapshot.base_sha,
            "collection_status": snapshot.collection_status,
            "head_sha": snapshot.head_sha,
            "issue_number": issue_number,
            "operation": "snapshot-github-change",
            "output": str(output),
            "pull_number": pull_number,
            "repository": repository,
            "sha256": snapshot.sha256,
            "unavailable_or_omitted_fields": list(snapshot.unavailable_fields),
        }
    )
    if snapshot.collection_status != "complete":
        raise typer.Exit(code=2)


@workflows_app.command("collect-engineering-evidence")
def collect_engineering_evidence_workflow(
    request_file: Path = typer.Option(..., "--request"),
    output: Path = typer.Option(..., "--output"),
    allowed_web_host: list[str] = typer.Option([], "--allowed-web-host"),
    token_environment_variable: str = typer.Option(
        "GITHUB_TOKEN", "--token-environment-variable"
    ),
) -> None:
    """Collect explicitly listed GitHub and public Web evidence using GET only."""
    if not token_environment_variable.isidentifier():
        _workflow_cli_error("GitHub token environment variable name is invalid")
    directory_fd = None
    try:
        directory_fd = os.open(
            output.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        if os.fstat(directory_fd).st_mode & 0o077:
            _workflow_cli_error(
                "engineering evidence output directory must be private "
                "(0700 or stricter)"
            )
        try:
            os.stat(output.name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _workflow_cli_error("engineering evidence output already exists")

        request_bytes = request_file.read_bytes()
        if len(request_bytes) > 100_000:
            raise EngineeringEvidenceError("request exceeds the safe size limit")
        request = json.loads(request_bytes)
        package, package_sha256, status, unavailable = collect_engineering_evidence(
            request,
            allowed_web_hosts=allowed_web_host,
            github_token=os.environ.get(token_environment_variable),
        )
        file_fd = os.open(
            output.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(file_fd, "w", encoding="utf-8") as stream:
            stream.write(package)
    except (EngineeringEvidenceError, json.JSONDecodeError, UnicodeDecodeError):
        _workflow_cli_error("engineering evidence request is invalid")
    except FileExistsError:
        _workflow_cli_error("engineering evidence output already exists")
    except OSError:
        _workflow_cli_error("engineering evidence could not be read or written")
    finally:
        if directory_fd is not None:
            os.close(directory_fd)

    _emit_json(
        {
            "collection_status": status,
            "operation": "collect-engineering-evidence",
            "output": str(output),
            "sha256": package_sha256,
            "source_count": len(json.loads(package)["sources"]),
            "unavailable_or_partial_source_count": len(unavailable),
        }
    )
    if status != "complete":
        raise typer.Exit(code=2)


def _step_preview_json(
    operation: str, preview: _WorkflowStepPreview
) -> dict[str, object]:
    request = preview.step_request
    invocation = preview.invocation_request
    value: dict[str, object] = {
        "allowed_tools": list(invocation.allowed_tools),
        "business_approval_required": preview.business_approval_required,
        "execution_target": preview.execution_target.descriptor(),
        "employee_id": request.employee_id,
        "mode": "preview",
        "model": invocation.model,
        "operation": operation,
        "request_fingerprint": preview.request_fingerprint,
        "resolved_tools": _resolved_tools_json(preview.resolved_tools),
        "status": "step_ready",
        "step_id": request.step_id,
        "step_index": request.step_index,
        "system_instructions": invocation.system_instructions,
        "task_instructions": invocation.task_instructions,
        "workflow_id": request.workflow_id,
    }
    if invocation.upstream_inputs != ():
        value["task_input"] = build_model_invocation_task_input(invocation)
        value["upstream_inputs"] = [
            {
                "workflow_id": upstream.workflow_id,
                "step_id": upstream.step_id,
                "step_index": upstream.step_index,
                "employee_id": upstream.employee_id,
                "output_text": upstream.output_text,
                "sha256": _upstream_output_digest(upstream),
            }
            for upstream in invocation.upstream_inputs
        ]
    if invocation.runtime_facts != EMPTY_RUNTIME_FACTS:
        runtime_facts = json.loads(
            serialize_runtime_facts_snapshot_canonical(invocation.runtime_facts)
        )
        runtime_facts["snapshot_sha256"] = runtime_facts_snapshot_digest(
            invocation.runtime_facts
        )
        value["runtime_facts"] = runtime_facts
    if invocation.run_id is not None:
        value["run_id"] = invocation.run_id
        value["manifest_digest"] = invocation.manifest_digest
        value["run_input"] = invocation.run_input
    return value


def _upstream_output_digest(upstream: object) -> str:
    """Digest exactly one canonical provenance-and-output object for preview."""
    from hashlib import sha256

    value = {
        "employee_id": upstream.employee_id,
        "output_text": upstream.output_text,
        "step_id": upstream.step_id,
        "step_index": upstream.step_index,
        "workflow_id": upstream.workflow_id,
    }
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _result_json(
    operation: str,
    mode: str,
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    *,
    run_input: str | None = None,
) -> dict[str, object]:
    """Build safe result metadata without copying provider result contents."""
    binding = binding_of(result)
    binding_value = (
        {}
        if binding is None
        else {
            "manifest_digest": binding.manifest_digest,
            "run_id": binding.run_id,
            "run_input": run_input,
        }
    )
    if type(result) is WorkflowProgressionDecision:
        return {
            "current_employee_id": result.current_employee_id,
            "current_step_id": result.current_step_id,
            "current_step_index": result.current_step_index,
            "failure_category": None,
            "mode": mode,
            "next_employee_id": result.next_employee_id,
            "next_step_id": result.next_step_id,
            "next_step_index": result.next_step_index,
            "operation": operation,
            "reason": result.reason,
            "status": result.decision,
            "workflow_id": result.workflow_id,
            **binding_value,
        }
    if type(result) is PersistedExecutionOutcome:
        return {
            "current_employee_id": result.current_employee_id,
            "current_step_id": result.current_step_id,
            "current_step_index": result.current_step_index,
            "failure_category": result.failure_category,
            "mode": mode,
            "next_employee_id": None,
            "next_step_id": None,
            "next_step_index": None,
            "operation": operation,
            "reason": None,
            "status": result.outcome,
            "workflow_id": result.workflow_id,
            **binding_value,
        }
    _workflow_cli_error("workflow result is incompatible")


@workflows_app.command("approval-evidence")
def approval_evidence_workflow(
    run_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """Read the strict, durable approval evidence for one Run."""
    store = _load_run_store_or_exit(run_store)
    manifest = _load_run_manifest_or_exit(store, run_id)
    try:
        evidence = list_run_approval_evidence(store, run_id)
    except Exception:
        _workflow_cli_error("Run approval evidence is invalid or unavailable")
    _emit_json(
        {
            "approvals": [asdict(item) for item in evidence],
            "manifest_digest": manifest.digest,
            "operation": "approval-evidence",
            "run_id": manifest.run_id,
        }
    )


@workflows_app.command("execution-evidence")
def execution_evidence_workflow(
    run_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
    show_content: bool = typer.Option(False, "--show-content"),
    attempt_id: str | None = typer.Option(None, "--attempt-id"),
) -> None:
    """Inspect execution evidence; opt in to viewing one attempt's content."""
    store = _load_run_store_or_exit(run_store)
    manifest = _load_run_manifest_or_exit(store, run_id)
    try:
        evidence = inspect_run_execution_evidence(store.root, run_id)
    except Exception:
        _workflow_cli_error("Run execution evidence is invalid or unavailable")
    if show_content or attempt_id is not None:
        if not show_content or attempt_id is None:
            _workflow_cli_error("--show-content requires exactly one --attempt-id")
        try:
            attempt = load_execution_attempt_evidence(store.root, run_id, attempt_id)
            raw_path = execution_raw_response_evidence_path(
                store.root, run_id, attempt_id
            )
            result_path = execution_normalized_result_evidence_path(
                store.root, run_id, attempt_id
            )
            raw = (
                load_raw_response_evidence(store.root, run_id, attempt_id)
                if raw_path.exists() or raw_path.is_symlink()
                else None
            )
            normalized = (
                load_normalized_result_evidence(store.root, run_id, attempt_id)
                if result_path.exists() or result_path.is_symlink()
                else None
            )
        except Exception:
            _workflow_cli_error("Attempt content is unavailable or invalid")
        request_body = None
        if attempt.request_body_base64 is not None:
            request_body = base64.b64decode(attempt.request_body_base64).decode("utf-8")
        response_text = None
        response_encoding = None
        if raw is not None:
            response_bytes = base64.b64decode(raw.body_base64)
            try:
                response_text = response_bytes.decode("utf-8")
                response_encoding = "utf-8"
            except UnicodeDecodeError:
                response_encoding = "base64"
        _emit_json(
            {
                "attempt_id": attempt.attempt_id,
                "job_id": attempt.job_id,
                "normalized_result": {
                    "category": None if normalized is None else normalized.category,
                    "failure_category": (
                        None if normalized is None else normalized.failure_category
                    ),
                    "evidence_sha256": (
                        None if normalized is None else normalized.digest
                    ),
                    "text": None if normalized is None else normalized.text,
                    "unavailable": normalized is None,
                },
                "operation": "execution-evidence-content",
                "provenance": {
                    "employee_id": attempt.employee_id,
                    "execution_target_fingerprint": (
                        attempt.execution_target_fingerprint
                    ),
                    "provider": attempt.provider,
                    "step_id": attempt.step_id,
                    "step_index": attempt.step_index,
                    "workflow_id": attempt.workflow_id,
                },
                "raw_response": {
                    "body_base64": (
                        raw.body_base64
                        if raw is not None and response_text is None
                        else None
                    ),
                    "body_encoding": response_encoding,
                    "body_text": response_text,
                    "body_length": None if raw is None else raw.body_length,
                    "body_sha256": None if raw is None else raw.body_sha256,
                    "evidence_sha256": None if raw is None else raw.digest,
                    "safe_headers": (
                        [] if raw is None else [list(item) for item in raw.safe_headers]
                    ),
                    "status_code": None if raw is None else raw.status_code,
                    "unavailable": raw is None,
                },
                "request": {
                    "body_base64": attempt.request_body_base64,
                    "body_length": attempt.request_body_length,
                    "body_sha256": attempt.request_body_sha256,
                    "body_text": request_body,
                    "evidence_sha256": attempt.digest,
                    "unavailable": attempt.request_body_base64 is None,
                },
                "run_id": run_id,
                "warning": (
                    "Explicit content view may reveal sensitive information; "
                    "stored evidence is private to this local account."
                ),
            }
        )
        return
    _emit_json(
        {
            "attempts": [asdict(item) for item in evidence],
            "manifest_digest": manifest.digest,
            "operation": "execution-evidence",
            "run_id": manifest.run_id,
        }
    )


@workflows_app.command("job-authorization")
def job_authorization_workflow(
    run_id: str,
    job_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """Inspect Job grant, allowance, revocation, and dispatch evidence read-only."""
    store = _load_run_store_or_exit(run_store)
    try:
        authorization = load_workflow_job_authorization_by_identity(
            store, run_id, job_id
        )
        inspection = inspect_workflow_job_authorization(authorization)
    except Exception:
        _workflow_cli_error("Job authorization evidence is invalid or unavailable")
    _emit_json(
        {
            **asdict(inspection),
            "operation": "job-authorization",
            "run_id": run_id,
        }
    )


@workflows_app.command("bounded-job")
def bounded_document_job_workflow(
    job_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """Inspect a fixed bounded document Job and its immutable stop record."""
    from ai_office.engine.workflow_bounded_job import inspect_bounded_document_job

    store = _load_run_store_or_exit(run_store)
    try:
        inspection = inspect_bounded_document_job(store, job_id)
    except Exception:
        _workflow_cli_error("Bounded document Job evidence is invalid or unavailable")
    _emit_json({**inspection, "operation": "bounded-job"})


@workflows_app.command("bounded-job-run")
def run_bounded_document_job_workflow(
    job_id: str,
    initial_run_id: str = typer.Option(..., "--run-id"),
    assignment_file: Path = typer.Option(..., "--assignment-file"),
    style_profile_id: str | None = typer.Option(
        None,
        "--style-profile",
        help="Apply a reviewed style profile by ID to this Job's Run Input.",
    ),
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
    execution_target: str = typer.Option("openai", "--execution-target"),
    execution_destinations: Path = typer.Option(..., "--execution-destinations"),
    step_model: list[str] = typer.Option(
        [],
        "--step-model",
        help="Override one Workflow Step as STEP_ID=MODEL; repeat for other Steps.",
    ),
    legacy_execution_model: str | None = typer.Option(
        None, "--execution-model", hidden=True
    ),
    workflow_id: str = typer.Option(..., "--workflow"),
    max_revisions: int = typer.Option(1, "--max-revisions", min=0),
    max_retries: int = typer.Option(0, "--max-retries", min=0, max=2),
    retry_interval_seconds: int = typer.Option(
        30, "--retry-interval-seconds", min=1, max=3600
    ),
    local_effect_policy_file: Path | None = typer.Option(None, "--local-effect-policy"),
    local_effect_workspace: Path | None = typer.Option(
        None, "--local-effect-workspace"
    ),
    approve_local_effect: bool = typer.Option(False, "--approve-local-effect"),
) -> None:
    """Issue and start a finite configured Workflow review Job after confirmation.

    Shared OS identity with OpenClaw is permitted; `approved_by` labels and
    this confirmation prompt are not an identity or isolation boundary.
    """
    from ai_office.engine.workflow_bounded_job import (
        _CURRENT_RETRYABLE_HTTP_STATUSES,
        BoundedJobActionSlot,
        WorkflowJobAuthorizationError,
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

    _reject_legacy_execution_model(legacy_execution_model)
    try:
        if os.geteuid() == 0 or os.geteuid() != os.getuid() or not sys.stdin.isatty():
            raise WorkflowJobAuthorizationError("operator_context")
        if run_store.is_symlink() or (
            run_store.exists() and (not run_store.is_dir() or any(run_store.iterdir()))
        ):
            raise WorkflowJobAuthorizationError("pilot_store_not_empty")
        if run_store.parent.is_symlink():
            raise WorkflowJobAuthorizationError("pilot_store_parent")
        assignment = _resolve_run_input(None, assignment_file)
        selected_style_profile = None
        if style_profile_id is not None:
            try:
                selected_style_profile = load_style_profile(
                    style_profile_id, directory.parent / "style_profiles"
                )
                assignment = add_style_profile_to_assignment(
                    assignment, selected_style_profile
                )
            except StyleProfileLoadError:
                _workflow_cli_error("selected style profile is invalid or unavailable")
            if len(assignment.encode("utf-8")) > MAX_RUN_INPUT_FILE_BYTES:
                _workflow_cli_error(
                    "style profile makes the Run Input exceed the size limit"
                )
        workflows, employees = _load_workflow_command_inputs(
            directory, employees_directory
        )
        workflow_by_id = {item.definition.id: item for item in workflows}
        if workflow_id not in workflow_by_id:
            raise WorkflowJobAuthorizationError("job_workflow_missing")
        source_workflow = _apply_step_model_overrides(
            workflow_by_id[workflow_id], step_model
        )
        workflow_by_id[workflow_id] = source_workflow
        execution_targets = _resolve_workflow_execution_targets(
            source_workflow, execution_target, execution_destinations
        )
        route_models = tuple(
            (target, step.model)
            for target, step in zip(
                execution_targets, source_workflow.definition.steps, strict=True
            )
        )
        for selected_target, selected_model in route_models:
            _validate_pinned_models_for_target(
                selected_target, execution_destinations, [selected_model]
            )
        target_by_step = {
            step.id: target
            for step, target in zip(
                source_workflow.definition.steps, execution_targets, strict=True
            )
        }
        target = execution_targets[0]
        destination_fingerprint = execution_target_fingerprint(target)
        employees_by_id = {item.definition.id: item for item in employees}
        plan = bounded_review_action_plan(source_workflow, max_revisions)
        steps_by_id = {step.id: step for step in source_workflow.definition.steps}
        local_effect_required = any(
            input_source in {"assignment", "revision_input"}
            and steps_by_id[step_id].artifact_content_type
            == FILE_CHANGE_SET_CONTENT_TYPE
            for step_id, _, input_source in plan
        )
        local_effect_policy = None
        if local_effect_required:
            if not (
                local_effect_policy_file is not None
                and local_effect_workspace is not None
                and approve_local_effect
            ):
                _workflow_cli_error(
                    "file-change Workflow requires --local-effect-policy, "
                    "--local-effect-workspace, and --approve-local-effect"
                )
            policy_bytes = local_effect_policy_file.read_bytes()
            if len(policy_bytes) > 65_536:
                _workflow_cli_error("local-effect policy is too large")
            policy_value = json.loads(
                policy_bytes, object_pairs_hook=_unique_json_object
            )
            if not isinstance(policy_value, dict):
                _workflow_cli_error("local-effect policy must be an object")
            policy_value["expires_at_ns"] = time.time_ns() + 2 * 60 * 60 * 1_000_000_000
            local_effect_policy = LocalExecutionPolicy.model_validate_json(
                json.dumps(policy_value, ensure_ascii=False, sort_keys=True).encode()
            )
            validate_local_execution_scope(
                assignment, local_effect_workspace, local_effect_policy
            )
        elif (
            local_effect_policy_file is not None
            or local_effect_workspace is not None
            or approve_local_effect
        ):
            _workflow_cli_error(
                "local-effect options apply only to a Workflow configured to "
                "produce file-change Artifacts"
            )
        required_employees = tuple(
            dict.fromkeys(steps_by_id[item[0]].employee for item in plan)
        )
        if any(name not in employees_by_id for name in required_employees):
            raise WorkflowJobAuthorizationError("job_employee_missing")
        source_manifest = build_workflow_run_manifest(
            f"source-{sha256(job_id.encode()).hexdigest()[:16]}",
            assignment,
            source_workflow,
            tuple(employees_by_id[name] for name in required_employees),
            execution_targets=execution_targets,
        )
        if source_manifest.run_id == initial_run_id:
            raise WorkflowJobAuthorizationError("job_source_identity")
        initial_view = bounded_job_execution_view(source_workflow, plan[0][0])
        initial_manifest = build_workflow_run_manifest(
            initial_run_id,
            assignment,
            initial_view,
            (employees_by_id[initial_view.definition.steps[0].employee],),
            execution_targets=(target_by_step[initial_view.definition.steps[0].id],),
        )

        def snapshot_digest(value: object) -> str:
            snapshot = asdict(value)
            encoded = json.dumps(
                snapshot,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            return sha256(encoded).hexdigest()

        action_slots = []
        for ordinal, (step_id, step_index, input_source) in enumerate(plan):
            employee_id = steps_by_id[step_id].employee
            view = bounded_job_execution_view(source_workflow, step_id)
            manifest = (
                initial_manifest
                if ordinal == 0
                else build_workflow_run_manifest(
                    f"snapshot-{sha256(job_id.encode()).hexdigest()[:12]}-{ordinal}",
                    assignment,
                    view,
                    (employees_by_id[employee_id],),
                    execution_targets=(target_by_step[step_id],),
                )
            )
            employee_snapshot = next(
                item for item in manifest.employee_snapshots if item.id == employee_id
            )
            action_slots.append(
                BoundedJobActionSlot(
                    ordinal=ordinal,
                    workflow_id=workflow_id,
                    step_id=step_id,
                    step_index=step_index,
                    employee_id=employee_id,
                    workflow_snapshot_sha256=snapshot_digest(
                        manifest.workflow_snapshot
                    ),
                    employee_snapshot_sha256=snapshot_digest(employee_snapshot),
                    provider=target_by_step[step_id].provider,
                    execution_target_fingerprint=execution_target_fingerprint(
                        target_by_step[step_id]
                    ),
                    model=steps_by_id[step_id].model,
                    input_source=input_source,
                )
            )
        if run_store.exists() and (
            not run_store.is_dir() or run_store.is_symlink() or any(run_store.iterdir())
        ):
            raise WorkflowJobAuthorizationError("pilot_store_not_empty")
        assignment_digest = sha256(assignment.encode("utf-8")).hexdigest()
    except Exception:
        _workflow_cli_error(
            "bounded Job definition or execution destination is invalid"
        )

    review_action_index = next(
        index
        for index, action in enumerate(action_slots)
        if action.input_source == "review_input"
    )
    reviewer_count = sum(
        1 for action in action_slots if action.input_source == "review_input"
    )
    guaranteed_initial_actions = review_action_index + reviewer_count
    review_limit = 1 + max_revisions
    max_normal_actions = min(
        len(action_slots),
        review_action_index + reviewer_count * (max_revisions + 1) + max_revisions,
    )
    actions_per_destination: dict[str, int] = {}
    destinations_by_fingerprint: dict[str, ModelExecutionTarget] = {}
    for slot in action_slots:
        fingerprint = slot.execution_target_fingerprint
        actions_per_destination[fingerprint] = (
            actions_per_destination.get(fingerprint, 0) + 1
        )
        destinations_by_fingerprint[fingerprint] = target_by_step[slot.step_id]
    destination_limits = [
        {
            "provider": selected_target.provider,
            "protocol": selected_target.protocol,
            "endpoint": selected_target.endpoint,
            "destination_fingerprint": fingerprint,
            "credential_environment_variable": (
                selected_target.credential_environment_variable
            ),
            "planned_actions": actions_per_destination[fingerprint],
            "maximum_attempts": actions_per_destination[fingerprint] + max_retries,
        }
        for fingerprint, selected_target in sorted(destinations_by_fingerprint.items())
    ]
    _emit_json(
        {
            "operation": "bounded-job-launch-confirmation",
            "job_id": job_id,
            "run_id": initial_run_id,
            "assignment_sha256": assignment_digest,
            "workflow_id": workflow_id,
            "workflow_snapshot_sha256": snapshot_digest(
                source_manifest.workflow_snapshot
            ),
            "planned_steps": [
                {
                    "step_id": slot.step_id,
                    "employee_id": slot.employee_id,
                    "model": slot.model,
                    "provider": slot.provider,
                    "destination_fingerprint": slot.execution_target_fingerprint,
                    "endpoint": target_by_step[slot.step_id].endpoint,
                    "protocol": target_by_step[slot.step_id].protocol,
                    "credential_environment_variable": (
                        target_by_step[slot.step_id].credential_environment_variable
                    ),
                    "input_source": slot.input_source,
                }
                for slot in action_slots
            ],
            "provider": target.provider,
            "protocol": target.protocol,
            "endpoint": target.endpoint,
            "destination_fingerprint": destination_fingerprint,
            "destinations": destination_limits,
            "step_models": [
                {"step_id": step.id, "model": step.model}
                for step in source_workflow.definition.steps
            ],
            "action_slots": len(action_slots),
            "guaranteed_initial_actions": guaranteed_initial_actions,
            "max_normal_actions": max_normal_actions,
            "reviewer_count": reviewer_count,
            "max_retries": max_retries,
            "retry_interval_seconds": retry_interval_seconds,
            "retryable_http_statuses": list(_CURRENT_RETRYABLE_HTTP_STATUSES),
            "provider_attempt_limit": max_normal_actions + max_retries,
            "revision_limit": max_revisions,
            "review_limit": review_limit,
            "credential_environment_variable": (target.credential_environment_variable),
            "request_headers": [
                {"name": header.name, "value": header.value}
                for header in target.request_headers
            ],
            "approval_labels_are_not_identity_proofs": True,
            **(
                {
                    "style_profile": {
                        "id": selected_style_profile.definition.id,
                        "version": selected_style_profile.definition.version,
                        "content_sha256": selected_style_profile.content_sha256,
                    }
                }
                if selected_style_profile is not None
                else {}
            ),
        }
    )
    confirmation = typer.prompt(
        "After reviewing this launch summary, type the Job ID to "
        "issue the Business grant for this exact bounded Job",
        default="",
        show_default=False,
    )
    if confirmation != job_id:
        _workflow_cli_error("Business grant was not confirmed", code=1)
    execution_confirmation = typer.prompt(
        "Type the same Job ID to issue the separate Execution grant and start "
        "this Job once",
        default="",
        show_default=False,
    )
    if execution_confirmation != job_id:
        _workflow_cli_error("Execution grant was not confirmed", code=1)
    if local_effect_policy is not None:
        _emit_json(
            {
                "operation": "bounded-job-local-effect-confirmation",
                "job_id": job_id,
                "workspace": str(local_effect_workspace),
                "base_revision": local_effect_policy.base_revision,
                "read_paths": list(local_effect_policy.read_paths),
                "write_paths": list(local_effect_policy.write_paths),
                "commands": [list(command) for command in local_effect_policy.commands],
                "limits": local_effect_policy.limits.model_dump(mode="json"),
                "max_candidates": max_revisions + 1,
                "approval_is_separate_from_provider_grants": True,
            }
        )
        local_confirmation = typer.prompt(
            "Type the Job ID to separately approve this finite local-effect policy",
            default="",
            show_default=False,
        )
        if local_confirmation != job_id:
            _workflow_cli_error("local-effect grant was not confirmed", code=1)

    try:
        # Re-read the administrator policy only as a veto. The approved
        # manifest targets remain authoritative; the registry cannot select a
        # replacement target or model after confirmation.
        for selected_target, selected_model in route_models:
            _validate_pinned_models_for_target(
                selected_target, execution_destinations, [selected_model]
            )
        now_ns = time.time_ns()
        run_store.mkdir(mode=0o700, parents=True, exist_ok=True)
        if run_store.stat().st_mode & 0o077:
            raise WorkflowJobAuthorizationError("pilot_store_permissions")
        store = WorkflowRunManifestStore(run_store)
        for slot in action_slots:
            view = bounded_job_execution_view(source_workflow, slot.step_id)
            manifest = build_workflow_run_manifest(
                f"snapshot-{sha256(job_id.encode()).hexdigest()[:12]}-{slot.ordinal}",
                assignment,
                view,
                tuple(employees_by_id[step.employee] for step in view.definition.steps),
                execution_targets=(target_by_step[slot.step_id],),
            )
            step_snapshot = manifest.workflow_snapshot.steps[0]
            if (
                manifest.execution_destination_for_step(1).fingerprint
                != slot.execution_target_fingerprint
                or manifest.execution_destination_for_step(1).provider != slot.provider
                or step_snapshot.id != slot.step_id
                or step_snapshot.employee != slot.employee_id
                or step_snapshot.model != slot.model
            ):
                raise WorkflowJobAuthorizationError("job_manifest_approval_binding")
        if (
            initial_manifest.execution_destination_for_step(1).fingerprint
            != action_slots[0].execution_target_fingerprint
            or initial_manifest.execution_destination_for_step(1).provider
            != action_slots[0].provider
            or initial_manifest.workflow_snapshot.steps[0].employee
            != action_slots[0].employee_id
            or initial_manifest.workflow_snapshot.steps[0].model
            != action_slots[0].model
        ):
            raise WorkflowJobAuthorizationError("job_manifest_approval_binding")
        persist_workflow_run_manifest(store, source_manifest)
        persist_workflow_run_manifest(store, initial_manifest)
        operator_label = getpass.getuser()
        grant_nonce = uuid.uuid4().hex
        scope = issue_bounded_workflow_job_scope(
            store,
            job_id=job_id,
            initial_run_id=initial_run_id,
            assignment_sha256=assignment_digest,
            initial_input_sha256=assignment_digest,
            actions=tuple(action_slots),
            max_provider_attempts=len(action_slots) + max_retries,
            max_revisions=max_revisions,
            max_retries=max_retries,
            retry_interval_seconds=retry_interval_seconds,
            source_manifest_run_id=source_manifest.run_id,
            source_manifest_digest=source_manifest.digest,
            expires_at_ns=now_ns + 2 * 60 * 60 * 1_000_000_000,
            business_approved_by=operator_label,
            business_grant_id=f"business-{grant_nonce}",
            execution_approved_by=operator_label,
            execution_grant_id=f"execution-{grant_nonce}",
            now_ns=now_ns,
        )
    except Exception:
        _workflow_cli_error("bounded Job could not be durably prepared")

    # Resolve the credential only after exact confirmation and durable scope
    # issuance. The secret value is never emitted or persisted.
    try:
        api_keys = {
            selected_target.provider: _load_api_key_for_target(selected_target)
            for selected_target in execution_targets
        }
    except Exception:
        _workflow_cli_error("a pinned destination credential is unavailable")

    def local_effect_handler(run_id: str, artifact_id: str, ordinal: int) -> str:
        if local_effect_policy is None or local_effect_workspace is None:
            raise LocalExecutionError("approval")
        approval = issue_local_execution_authorization(
            store,
            run_id=run_id,
            artifact_id=artifact_id,
            workspace=local_effect_workspace,
            policy=local_effect_policy,
            approved_by=operator_label,
            approval_id=(
                "bounded-local-"
                + sha256(f"{job_id}:{ordinal}".encode()).hexdigest()[:32]
            ),
        )
        evidence = execute_local_change(
            store,
            run_id=run_id,
            artifact_id=artifact_id,
            authorization_id=approval.authorization_id,
            workspace=local_effect_workspace,
        )
        if evidence.get("status") != "verified":
            raise LocalExecutionError("local_effect_failed")
        return approval.authorization_id

    try:
        outcome = run_bounded_document_job(
            store,
            scope,
            initial_run_id=scope.initial_run_id,
            assignment=initial_manifest.run_input,
            workflows=workflow_by_id,
            employees=employees_by_id,
            api_key=api_keys,
            transport=send_openai_responses_http_request,
            execution_destinations=execution_destinations,
            local_effect_handler=(
                local_effect_handler if local_effect_policy is not None else None
            ),
        )
    except Exception:
        _workflow_cli_error("bounded Job stopped; inspect its durable evidence")
    _emit_json(
        {
            "operation": "bounded-job-run",
            "job_id": scope.job_id,
            "run_id": scope.initial_run_id,
            "status": outcome.status,
            "stop_reason": outcome.stop_reason,
            "final_artifact_run_id": outcome.final_artifact_run_id,
            "final_artifact_id": outcome.final_artifact_id,
            "review_artifact_run_id": outcome.review_artifact_run_id,
            "review_artifact_id": outcome.review_artifact_id,
            "revisions_used": outcome.revisions_used,
        }
    )


def _artifact_metadata_json(artifact: WorkflowArtifact) -> dict[str, object]:
    return {
        "artifact_id": artifact.artifact_id,
        "content_length": artifact.content_length,
        "content_sha256": artifact.content_sha256,
        "content_type": artifact.content_type,
        "consistent_with_execution_evidence": True,
        "employee_id": artifact.employee_id,
        "manifest_digest": artifact.manifest_digest,
        "normalized_result_evidence_sha256": (
            artifact.normalized_result_evidence_sha256
        ),
        "provenance": {
            "execution_attempt_evidence_sha256": (
                artifact.execution_attempt_evidence_sha256
            ),
            "execution_attempt_id": artifact.execution_attempt_id,
            "normalized_result_evidence_sha256": (
                artifact.normalized_result_evidence_sha256
            ),
            "raw_response_body_sha256": artifact.raw_response_body_sha256,
            "raw_response_evidence_sha256": artifact.raw_response_evidence_sha256,
            "terminal_success_event_sha256": (artifact.terminal_success_event_sha256),
        },
        "run_id": artifact.run_id,
        "step_id": artifact.step_id,
        "step_index": artifact.step_index,
        "workflow_id": artifact.workflow_id,
    }


class _DraftPRDeliveryError(ValueError):
    """Safe stop classification for the local Git/gh Draft PR command."""

    def __init__(self, classification: str) -> None:
        super().__init__(classification)
        self.classification = classification


def _draft_pr_canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _draft_pr_git(
    repository: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
    env_updates: dict[str, str] | None = None,
    timeout: int = 30,
) -> bytes:
    """Run standard Git with hooks and external diff/textconv disabled."""
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/bin/false",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TRACE": "0",
            "GIT_TRACE_CURL": "0",
            "GIT_TRACE_PACKET": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    if env_updates:
        environment.update(env_updates)
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "commit.gpgsign=false",
                *arguments,
            ],
            input=input_bytes,
            capture_output=True,
            check=False,
            env=environment,
            shell=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise _DraftPRDeliveryError("git_unavailable_or_timed_out") from None
    if result.returncode != 0:
        raise _DraftPRDeliveryError("git_command_failed")
    return result.stdout


def _draft_pr_gh(*arguments: str, timeout: int = 30) -> bytes:
    """Run GitHub CLI without prompting and never expose captured diagnostics."""
    environment = os.environ.copy()
    environment["GH_PROMPT_DISABLED"] = "1"
    try:
        result = subprocess.run(
            ["gh", *arguments],
            capture_output=True,
            check=False,
            env=environment,
            shell=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise _DraftPRDeliveryError("gh_unavailable_or_timed_out") from None
    if result.returncode != 0:
        raise _DraftPRDeliveryError("gh_command_failed")
    return result.stdout


def _draft_pr_repo_from_url(value: str) -> str:
    """Accept only credential-free github.com remotes for the target repo."""
    scp_remote = r"git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?"
    if re.fullmatch(scp_remote, value):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "ssh"}
            or parsed.hostname != "github.com"
            or parsed.username not in {None, "git"}
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise _DraftPRDeliveryError("remote_is_not_target_github_repository")
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
        raise _DraftPRDeliveryError("remote_is_not_target_github_repository")
    return path


def _draft_pr_repository_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise _DraftPRDeliveryError("invalid_repository_name")
    return value


def _draft_pr_branch_name(value: str, *, allow_heads_prefix: bool = False) -> str:
    if allow_heads_prefix and value.startswith("refs/heads/"):
        value = value.removeprefix("refs/heads/")
    if (
        not value
        or value.startswith(("-", "refs/"))
        or "\n" in value
        or "\r" in value
        or "\x00" in value
    ):
        raise _DraftPRDeliveryError("invalid_branch_name")
    return value


def _draft_pr_ls_remote(repository: Path, remote_url: str, ref: str) -> str | None:
    output = _draft_pr_git(repository, "ls-remote", remote_url, ref).decode(
        "ascii", "strict"
    )
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if not rows:
        return None
    if (
        len(rows) != 1
        or len(rows[0]) != 2
        or rows[0][1] != ref
        or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", rows[0][0])
    ):
        raise _DraftPRDeliveryError("remote_ref_response_invalid")
    return rows[0][0]


def _draft_pr_head_is_from_repository(
    record: dict[str, object], repository: str
) -> bool:
    head_repository = record.get("headRepository")
    return (
        type(head_repository) is dict
        and head_repository.get("nameWithOwner") == repository
    )


def _draft_pr_read_pr_list(repository: str, head: str) -> list[dict[str, object]]:
    try:
        value = json.loads(
            _draft_pr_gh(
                "pr",
                "list",
                "--repo",
                repository,
                "--head",
                head,
                "--state",
                "all",
                "--limit",
                "100",
                "--json",
                "number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,headRepository",
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _DraftPRDeliveryError("gh_pull_request_list_invalid") from None
    if type(value) is not list or any(type(item) is not dict for item in value):
        raise _DraftPRDeliveryError("gh_pull_request_list_invalid")
    # `gh pr list --head` accepts a branch name, not OWNER:BRANCH.
    return [
        item for item in value if _draft_pr_head_is_from_repository(item, repository)
    ]


def _draft_pr_read_pr(repository: str, url: str) -> dict[str, object]:
    try:
        value = json.loads(
            _draft_pr_gh(
                "pr",
                "view",
                url,
                "--repo",
                repository,
                "--json",
                "number,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,headRepository,body,files",
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _DraftPRDeliveryError("gh_pull_request_view_invalid") from None
    if type(value) is not dict:
        raise _DraftPRDeliveryError("gh_pull_request_view_invalid")
    return value


def _draft_pr_pr_matches(
    record: dict[str, object],
    *,
    repository: str,
    base_ref: str,
    base_sha: str,
    branch: str,
    head_sha: str,
    body_markers: tuple[str, ...],
    paths: set[str],
) -> bool:
    files = record.get("files")
    actual_paths = (
        {item.get("path") for item in files if type(item) is dict}
        if type(files) is list
        else set()
    )
    url = record.get("url")
    expected_prefix = f"https://github.com/{repository}/pull/"
    body = record.get("body")
    return bool(
        record.get("state") == "OPEN"
        and record.get("isDraft") is True
        and record.get("baseRefName") == base_ref
        and record.get("baseRefOid") == base_sha
        and record.get("headRefName") == branch
        and record.get("headRefOid") == head_sha
        and _draft_pr_head_is_from_repository(record, repository)
        and type(url) is str
        and url.startswith(expected_prefix)
        and url[len(expected_prefix) :].isdigit()
        and type(body) is str
        and all(marker in body for marker in body_markers)
        and actual_paths == paths
    )


def _draft_pr_verify_remote_commit(
    repository: Path,
    remote_url: str,
    remote_ref: str,
    *,
    expected_sha: str,
    base_sha: str,
    tree_sha: str,
    message: str,
    paths: set[str],
) -> None:
    if _draft_pr_ls_remote(repository, remote_url, remote_ref) != expected_sha:
        raise _DraftPRDeliveryError("remote_head_mismatch")
    _draft_pr_git(
        repository,
        "fetch",
        "--no-tags",
        "--no-write-fetch-head",
        remote_url,
        remote_ref,
        timeout=120,
    )
    parent = (
        _draft_pr_git(repository, "show", "-s", "--format=%P", expected_sha)
        .decode("ascii", "strict")
        .strip()
    )
    tree = (
        _draft_pr_git(repository, "show", "-s", "--format=%T", expected_sha)
        .decode("ascii", "strict")
        .strip()
    )
    actual_message = (
        _draft_pr_git(repository, "show", "-s", "--format=%B", expected_sha)
        .decode("utf-8", "strict")
        .rstrip("\n")
    )
    if parent != base_sha or tree != tree_sha or actual_message != message:
        raise _DraftPRDeliveryError("remote_commit_mismatch")
    rows = (
        _draft_pr_git(
            repository,
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "--no-renames",
            "-r",
            base_sha,
            expected_sha,
        )
        .decode("utf-8", "strict")
        .splitlines()
    )
    actual: dict[str, str] = {}
    for row in rows:
        parts = row.split("\t", 1)
        if len(parts) != 2:
            raise _DraftPRDeliveryError("remote_commit_diff_invalid")
        actual[parts[1]] = parts[0]
    if set(actual) != paths or any(status != "M" for status in actual.values()):
        raise _DraftPRDeliveryError("remote_commit_diff_mismatch")


def _draft_pr_confirmed_result(
    repository_path: Path,
    remote_url: str,
    repo_name: str,
    base_ref: str,
    branch: str,
    attempt: dict[str, object],
    paths: set[str],
) -> dict[str, object]:
    branch_ref = f"refs/heads/{branch}"
    base_ref_full = f"refs/heads/{base_ref}"
    base_sha = attempt.get("base_sha")
    commit_sha = attempt.get("commit_sha")
    tree_sha = attempt.get("tree_sha")
    commit_message = attempt.get("commit_message")
    markers = attempt.get("body_markers")
    if (
        type(base_sha) is not str
        or type(commit_sha) is not str
        or type(tree_sha) is not str
        or type(commit_message) is not str
        or type(markers) is not list
        or any(type(item) is not str for item in markers)
        or _draft_pr_ls_remote(repository_path, remote_url, base_ref_full) != base_sha
    ):
        raise _DraftPRDeliveryError("prior_delivery_not_confirmed")
    _draft_pr_verify_remote_commit(
        repository_path,
        remote_url,
        branch_ref,
        expected_sha=commit_sha,
        base_sha=base_sha,
        tree_sha=tree_sha,
        message=commit_message,
        paths=paths,
    )
    pull_requests = _draft_pr_read_pr_list(repo_name, branch)
    if len(pull_requests) != 1 or type(pull_requests[0].get("url")) is not str:
        raise _DraftPRDeliveryError("prior_draft_pr_not_confirmed")
    url = pull_requests[0]["url"]
    record = _draft_pr_read_pr(repo_name, url)
    if not _draft_pr_pr_matches(
        record,
        repository=repo_name,
        base_ref=base_ref,
        base_sha=base_sha,
        branch=branch,
        head_sha=commit_sha,
        body_markers=tuple(markers),
        paths=paths,
    ):
        raise _DraftPRDeliveryError("prior_draft_pr_not_confirmed")
    return {
        "base_ref": base_ref,
        "base_sha": base_sha,
        "branch": branch,
        "commit_sha": commit_sha,
        "diff_sha256": attempt["diff_sha256"],
        "job_id": attempt["job_id"],
        "artifact_id": attempt["artifact_id"],
        "artifact_sha256": attempt["artifact_sha256"],
        "pr_url": url,
        "repository": repo_name,
        "reconciled_read_only": True,
        "status": "completed",
        "tree_sha": tree_sha,
    }


def _draft_pr_write_once(path: Path, value: dict[str, object]) -> None:
    payload = _draft_pr_canonical(value)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise _DraftPRDeliveryError("delivery_record_already_exists") from None
    except OSError:
        raise _DraftPRDeliveryError("delivery_record_unavailable") from None
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise _DraftPRDeliveryError("delivery_record_write_failed") from None
    finally:
        os.close(descriptor)


def _draft_pr_read_record(path: Path) -> dict[str, object] | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        info = path.lstat()
        if (
            not path.is_file()
            or path.is_symlink()
            or info.st_nlink != 1
            or info.st_size > 65_536
        ):
            raise _DraftPRDeliveryError("delivery_record_invalid")
        payload = path.read_bytes()
        value = json.loads(payload)
    except _DraftPRDeliveryError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise _DraftPRDeliveryError("delivery_record_invalid") from None
    if type(value) is not dict or payload != _draft_pr_canonical(value):
        raise _DraftPRDeliveryError("delivery_record_invalid")
    return value


def _draft_pr_load_candidate(
    store: WorkflowRunManifestStore,
    job_id: str,
    git_repository: Path,
    repo_name: str,
    base_ref: str,
) -> dict[str, object]:
    from ai_office.engine.workflow_bounded_job import (
        WorkflowJobAuthorizationError,
        inspect_bounded_document_job,
        load_bounded_workflow_job_scope,
    )

    try:
        scope = load_bounded_workflow_job_scope(store, job_id)
        inspection = inspect_bounded_document_job(
            store, job_id, include_local_effect=True
        )
        status = inspection.get("status")
        final_run_id = inspection.get("final_artifact_run_id")
        artifact_id = inspection.get("final_artifact_id")
        review_run_id = inspection.get("review_artifact_run_id")
        review_artifact_id = inspection.get("review_artifact_id")
        local_effect = inspection.get("final_local_effect")
        if (
            status not in {"candidate", "reviewed"}
            or type(final_run_id) is not str
            or type(artifact_id) is not str
            or type(review_run_id) is not str
            or type(review_artifact_id) is not str
            or type(local_effect) is not dict
            or local_effect.get("scope_sha256") != scope.digest
        ):
            raise _DraftPRDeliveryError("job_not_ready_for_delivery")
        artifact = read_run_artifact(store, final_run_id, artifact_id)
        review_artifact = read_run_artifact(store, review_run_id, review_artifact_id)
        manifest = load_workflow_run_manifest(store, final_run_id)
        source_manifest = load_workflow_run_manifest(
            store, scope.source_manifest_run_id
        )
        authorization_id = local_effect.get("authorization_id")
        if type(authorization_id) is not str:
            raise _DraftPRDeliveryError("verification_evidence_missing")
        authorization = load_local_execution_authorization(
            store, final_run_id, authorization_id
        )
        evidence = load_local_execution_evidence(store, final_run_id, authorization_id)
        assignment = json.loads(inspection["assignment"])
        repository = assignment.get("repository") if type(assignment) is dict else None
        if (
            type(repository) is not dict
            or repository.get("full_name") != repo_name
            or type(repository.get("id")) is not int
            or isinstance(repository.get("id"), bool)
            or repository.get("base_revision") != authorization.policy.base_revision
            or _draft_pr_branch_name(
                repository.get("base_ref", ""), allow_heads_prefix=True
            )
            != base_ref
            or type(repository.get("path")) is not str
            or Path(repository["path"]).resolve(strict=True) != git_repository
            or artifact.content_type != FILE_CHANGE_SET_CONTENT_TYPE
            or artifact.run_id != final_run_id
            or artifact.artifact_id != artifact_id
            or artifact.manifest_digest != manifest.digest
            or artifact.workflow_id != manifest.workflow_id
            or review_artifact.run_id != review_run_id
            or review_artifact.artifact_id != review_artifact_id
            or authorization.run_id != artifact.run_id
            or authorization.manifest_digest != artifact.manifest_digest
            or authorization.artifact_id != artifact.artifact_id
            or authorization.artifact_sha256 != artifact.content_sha256
            or evidence.get("status") != "verified"
            or evidence.get("run_id") != artifact.run_id
            or evidence.get("manifest_digest") != artifact.manifest_digest
            or evidence.get("artifact_id") != artifact.artifact_id
            or evidence.get("artifact_sha256") != artifact.content_sha256
            or evidence.get("authorization_id") != authorization_id
            or evidence.get("base_revision") != authorization.policy.base_revision
            or evidence.get("workspace_sha256") != authorization.workspace_sha256
            or local_effect.get("run_id") != artifact.run_id
            or local_effect.get("artifact_id") != artifact.artifact_id
            or local_effect.get("artifact_sha256") != artifact.content_sha256
            or local_effect.get("evidence_sha256")
            != sha256(_draft_pr_canonical(evidence)).hexdigest()
        ):
            raise _DraftPRDeliveryError("source_evidence_mismatch")
        if (
            source_manifest.run_id != scope.source_manifest_run_id
            or source_manifest.digest != scope.source_manifest_digest
        ):
            raise _DraftPRDeliveryError("source_manifest_mismatch")
        changes = _parse_change_set(artifact, authorization.policy)
        if not changes:
            raise _DraftPRDeliveryError("change_set_empty")
        paths = {item["path"] for item in changes}
        if len(paths) != len(changes):
            raise _DraftPRDeliveryError("change_set_duplicate_path")
    except _DraftPRDeliveryError:
        raise
    except (OSError, TypeError, ValueError, KeyError, WorkflowJobAuthorizationError):
        raise _DraftPRDeliveryError("source_evidence_invalid") from None

    if (
        store.root.resolve() == git_repository
        or git_repository.is_relative_to(store.root.resolve())
        or store.root.resolve().is_relative_to(git_repository)
    ):
        raise _DraftPRDeliveryError("run_store_overlaps_repository")
    base_sha = authorization.policy.base_revision
    try:
        _draft_pr_git(git_repository, "cat-file", "-e", f"{base_sha}^{{commit}}")
        before_by_path: dict[str, bytes] = {}
        tree_modes: dict[str, str] = {}
        for change in changes:
            path = change["path"]
            if "\n" in path or "\r" in path:
                raise _DraftPRDeliveryError("change_path_invalid")
            entry = _draft_pr_git(
                git_repository, "ls-tree", "-z", "--full-tree", base_sha, "--", path
            )
            rows = [item for item in entry.split(b"\x00") if item]
            if len(rows) != 1:
                raise _DraftPRDeliveryError("change_preimage_missing")
            header, raw_path = rows[0].split(b"\t", 1)
            mode, object_type, object_id = header.split(b" ")
            if (
                raw_path.decode("utf-8") != path
                or object_type != b"blob"
                or mode not in {b"100644", b"100755"}
            ):
                raise _DraftPRDeliveryError("change_preimage_invalid")
            before = _draft_pr_git(
                git_repository, "cat-file", "blob", object_id.decode("ascii")
            )
            if sha256(before).hexdigest() != change["before_sha256"]:
                raise _DraftPRDeliveryError("change_preimage_mismatch")
            before_by_path[path] = before
            tree_modes[path] = mode.decode("ascii")
        expected_diff = "".join(
            line
            for change in sorted(changes, key=lambda item: item["path"])
            for line in difflib.unified_diff(
                before_by_path[change["path"]]
                .decode("utf-8")
                .splitlines(keepends=True),
                base64.b64decode(change["content_base64"], validate=True)
                .decode("utf-8")
                .splitlines(keepends=True),
                fromfile=f"a/{change['path']}",
                tofile=f"b/{change['path']}",
            )
        )
    except _DraftPRDeliveryError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, subprocess.SubprocessError):
        raise _DraftPRDeliveryError("candidate_tree_invalid") from None
    if (
        evidence.get("diff") != expected_diff
        or evidence.get("diff_sha256")
        != sha256(expected_diff.encode("utf-8")).hexdigest()
    ):
        raise _DraftPRDeliveryError("verification_diff_mismatch")
    return {
        "artifact": artifact,
        "authorization": authorization,
        "base_sha": base_sha,
        "changes": changes,
        "evidence": evidence,
        "evidence_sha256": sha256(_draft_pr_canonical(evidence)).hexdigest(),
        "expected_diff": expected_diff,
        "job_status": status,
        "manifest": manifest,
        "paths": paths,
        "review_artifact": review_artifact,
        "review_run_id": review_run_id,
        "repository_id": repository["id"],
        "scope": scope,
        "tree_modes": tree_modes,
    }


def _draft_pr_build_tree(
    repository: Path,
    *,
    base_sha: str,
    changes: list[dict[str, str]],
    tree_modes: dict[str, str],
) -> str:
    with tempfile.TemporaryDirectory(prefix="ai-office-draft-pr-index-") as directory:
        index = Path(directory) / "index"
        environment = {"GIT_INDEX_FILE": str(index)}
        _draft_pr_git(repository, "read-tree", base_sha, env_updates=environment)
        for change in changes:
            content = base64.b64decode(change["content_base64"], validate=True)
            blob = (
                _draft_pr_git(
                    repository,
                    "hash-object",
                    "-w",
                    "--no-filters",
                    "--stdin",
                    input_bytes=content,
                )
                .decode("ascii", "strict")
                .strip()
            )
            _draft_pr_git(
                repository,
                "update-index",
                "--add",
                "--cacheinfo",
                tree_modes[change["path"]],
                blob,
                change["path"],
                env_updates=environment,
            )
        tree_sha = (
            _draft_pr_git(repository, "write-tree", env_updates=environment)
            .decode("ascii", "strict")
            .strip()
        )
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree_sha):
        raise _DraftPRDeliveryError("candidate_tree_invalid")
    return tree_sha


def _draft_pr_create_commit(
    repository: Path,
    *,
    base_sha: str,
    tree_sha: str,
    message: str,
) -> str:
    commit_sha = (
        _draft_pr_git(
            repository,
            "commit-tree",
            tree_sha,
            "-p",
            base_sha,
            "-m",
            message,
        )
        .decode("ascii", "strict")
        .strip()
    )
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit_sha):
        raise _DraftPRDeliveryError("candidate_commit_invalid")
    if (
        _draft_pr_git(repository, "show", "-s", "--format=%P", commit_sha)
        .decode("ascii", "strict")
        .strip()
        != base_sha
    ):
        raise _DraftPRDeliveryError("candidate_commit_parent_mismatch")
    if (
        _draft_pr_git(repository, "show", "-s", "--format=%T", commit_sha)
        .decode("ascii", "strict")
        .strip()
        != tree_sha
    ):
        raise _DraftPRDeliveryError("candidate_commit_tree_mismatch")
    if (
        _draft_pr_git(repository, "show", "-s", "--format=%B", commit_sha)
        .decode("utf-8", "strict")
        .rstrip("\n")
        != message
    ):
        raise _DraftPRDeliveryError("candidate_commit_message_mismatch")
    return commit_sha


def _draft_pr_api_protection(repo_name: str, base_ref: str) -> bool | None:
    endpoint = f"repos/{repo_name}/branches/{quote(base_ref, safe='')}"
    try:
        value = json.loads(_draft_pr_gh("api", endpoint))
    except _DraftPRDeliveryError:
        return None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    protected = value.get("protected") if type(value) is dict else None
    return protected if type(protected) is bool else None


def _draft_pr_pr_body(
    *,
    repo_name: str,
    job_id: str,
    job_status: str,
    artifact: WorkflowArtifact,
    review_artifact: WorkflowArtifact,
    base_ref: str,
    base_sha: str,
    branch: str,
    commit_sha: str,
    tree_sha: str,
    evidence_sha256: str,
    diff_sha256: str,
    paths: set[str],
    protected: bool | None,
) -> tuple[str, tuple[str, ...]]:
    markers = (
        f"AI-Office-Job: {job_id}",
        f"AI-Office-Artifact: {artifact.artifact_id}",
        f"AI-Office-Artifact-SHA256: {artifact.content_sha256}",
        f"AI-Office-Verification-Evidence-SHA256: {evidence_sha256}",
        f"AI-Office-Diff-SHA256: {diff_sha256}",
    )
    protection = (
        "protected=true"
        if protected is True
        else "protected=false"
        if protected is False
        else "unknown (not assumed protected)"
    )
    body = "\n".join(
        [
            "Draft delivery for Issue #805 using ai-office's standard Git and "
            "GitHub CLI path.",
            "",
            f"- Job: `{job_id}` ({job_status})",
            (
                f"- Final Artifact: `{artifact.run_id}/{artifact.artifact_id}` "
                f"({artifact.content_sha256})"
            ),
            (
                "- Final all-PASS review Artifact: "
                f"`{review_artifact.run_id}/{review_artifact.artifact_id}` "
                f"({review_artifact.content_sha256})"
            ),
            f"- Local verification Evidence SHA-256: `{evidence_sha256}`",
            f"- Target: `{repo_name}` / `{base_ref}` at `{base_sha}`",
            f"- New head: `{branch}` at `{commit_sha}` (tree `{tree_sha}`)",
            f"- Verified diff SHA-256: `{diff_sha256}`",
            f"- Changed paths: {', '.join(f'`{path}`' for path in sorted(paths))}",
            f"- Base branch protection observed before delivery: `{protection}`",
            "",
            "The PR remains Draft. No Ready, merge, or Issue-closing action "
            "was requested.",
            "",
            *markers,
        ]
    )
    return body, markers


@workflows_app.command("draft-pr")
def deliver_verified_artifact_as_draft_pr(
    job_id: str,
    repository: str = typer.Option(..., "--repository"),
    base_ref_option: str = typer.Option(..., "--base-ref"),
    branch_option: str = typer.Option(..., "--branch"),
    git_repository_option: Path = typer.Option(..., "--git-repository"),
    remote_name: str = typer.Option("origin", "--remote"),
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Commit, push and open a Draft PR for one verified bounded-Job Artifact.

    Uses the Run's final all-PASS Artifact and existing local-verification
    Evidence. `--yes` is an explicit operator-intent flag, not a security
    boundary; the local GitHub credential remains available to this process.
    """
    try:
        repo_name = _draft_pr_repository_name(repository)
        base_ref = _draft_pr_branch_name(base_ref_option, allow_heads_prefix=True)
        branch = _draft_pr_branch_name(branch_option)
        if (
            not remote_name
            or remote_name.startswith("-")
            or any(char.isspace() for char in remote_name)
        ):
            raise _DraftPRDeliveryError("invalid_remote_name")
        git_repository = git_repository_option.expanduser().resolve(strict=True)
        store = _load_run_store_or_exit(run_store)
        git_root = Path(
            _draft_pr_git(git_repository, "rev-parse", "--show-toplevel")
            .decode("utf-8", "strict")
            .strip()
        ).resolve(strict=True)
        if git_root != git_repository:
            raise _DraftPRDeliveryError("git_repository_must_be_worktree_root")
        source = _draft_pr_load_candidate(store, job_id, git_root, repo_name, base_ref)
        artifact = source["artifact"]
        review_artifact = source["review_artifact"]
        evidence = source["evidence"]
        paths = source["paths"]
        base_sha = source["base_sha"]
        tree_sha = _draft_pr_build_tree(
            git_root,
            base_sha=base_sha,
            changes=source["changes"],
            tree_modes=source["tree_modes"],
        )
        initial_worktree_status = _draft_pr_git(
            git_root, "status", "--porcelain=v1", "--untracked-files=all"
        )

        _draft_pr_gh("auth", "status", "--hostname", "github.com")
        try:
            repo_view = json.loads(_draft_pr_gh("api", f"repos/{repo_name}"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _DraftPRDeliveryError("gh_repository_identity_invalid") from None
        if (
            type(repo_view) is not dict
            or repo_view.get("full_name") != repo_name
            or type(repo_view.get("id")) is not int
            or repo_view.get("id") != source["repository_id"]
        ):
            raise _DraftPRDeliveryError("gh_repository_identity_mismatch")
        default_branch = repo_view.get("default_branch")
        if type(default_branch) is not str:
            raise _DraftPRDeliveryError("gh_default_branch_unavailable")
        if branch in {base_ref, default_branch}:
            raise _DraftPRDeliveryError("target_branch_must_be_new_and_non_default")
        remote_url = (
            _draft_pr_git(git_root, "remote", "get-url", "--push", remote_name)
            .decode("utf-8", "strict")
            .strip()
        )
        if _draft_pr_repo_from_url(remote_url) != repo_name:
            raise _DraftPRDeliveryError("git_remote_identity_mismatch")
        _draft_pr_git(git_root, "cat-file", "-e", f"{base_sha}^{{commit}}")
        _draft_pr_git(git_root, "check-ref-format", "--branch", base_ref)
        base_ref_full = f"refs/heads/{base_ref}"
        branch_ref = f"refs/heads/{branch}"
        if _draft_pr_ls_remote(git_root, remote_url, base_ref_full) != base_sha:
            raise _DraftPRDeliveryError("remote_base_moved_or_mismatched")
        _draft_pr_git(git_root, "check-ref-format", "--branch", branch)
        protection = _draft_pr_api_protection(repo_name, base_ref)

        plan = {
            "artifact_id": artifact.artifact_id,
            "artifact_run_id": artifact.run_id,
            "artifact_sha256": artifact.content_sha256,
            "base_ref": base_ref,
            "base_sha": base_sha,
            "branch": branch,
            "diff_sha256": evidence["diff_sha256"],
            "evidence_sha256": source["evidence_sha256"],
            "job_id": job_id,
            "job_status": source["job_status"],
            "repository": repo_name,
            "review_artifact_id": review_artifact.artifact_id,
            "review_artifact_run_id": review_artifact.run_id,
            "review_artifact_sha256": review_artifact.content_sha256,
            "source_manifest_digest": source["manifest"].digest,
            "tree_sha": tree_sha,
        }
        record_prefix = f"{job_id}.draft-pr-delivery"
        attempt_path = store.root / f"{record_prefix}.attempt.json"
        result_path = store.root / f"{record_prefix}.result.json"
        stop_path = store.root / f"{record_prefix}.stop.json"
        attempt = _draft_pr_read_record(attempt_path)
        prior_result = _draft_pr_read_record(result_path)
        if prior_result is not None and attempt is None:
            raise _DraftPRDeliveryError("delivery_record_invalid")
        if attempt is not None:
            if any(attempt.get(key) != value for key, value in plan.items()):
                raise _DraftPRDeliveryError("prior_delivery_target_mismatch")
            if attempt.get("schema_version") != "ai-office-draft-pr-attempt.v1":
                raise _DraftPRDeliveryError("delivery_record_invalid")
            if prior_result is not None:
                if (
                    prior_result.get("attempt_sha256")
                    != sha256(_draft_pr_canonical(attempt)).hexdigest()
                ):
                    raise _DraftPRDeliveryError("delivery_record_invalid")
                confirmed = _draft_pr_confirmed_result(
                    git_root, remote_url, repo_name, base_ref, branch, attempt, paths
                )
                if any(
                    prior_result.get(key) != value
                    for key, value in confirmed.items()
                    if key not in {"reconciled_read_only"}
                ):
                    raise _DraftPRDeliveryError("stored_delivery_result_mismatch")
                _emit_json({"operation": "draft-pr", **confirmed})
                return
            try:
                confirmed = _draft_pr_confirmed_result(
                    git_root, remote_url, repo_name, base_ref, branch, attempt, paths
                )
            except _DraftPRDeliveryError as error:
                if not stop_path.exists():
                    _draft_pr_write_once(
                        stop_path,
                        {
                            "classification": error.classification,
                            "job_id": job_id,
                            "schema_version": "ai-office-draft-pr-stop.v1",
                            "stage": "read_only_reconciliation",
                        },
                    )
                raise _DraftPRDeliveryError("prior_delivery_not_confirmed") from None
            _draft_pr_write_once(
                result_path,
                {
                    **confirmed,
                    "attempt_sha256": sha256(_draft_pr_canonical(attempt)).hexdigest(),
                    "schema_version": "ai-office-draft-pr-result.v1",
                },
            )
            _emit_json({"operation": "draft-pr", **confirmed})
            return

        if stop_path.exists():
            raise _DraftPRDeliveryError("prior_delivery_stopped_no_retry")
        if _draft_pr_ls_remote(git_root, remote_url, branch_ref) is not None:
            raise _DraftPRDeliveryError("target_branch_already_exists")
        local_target = (
            _draft_pr_git(
                git_root,
                "for-each-ref",
                "--format=%(refname)",
                branch_ref,
            )
            .decode("utf-8", "strict")
            .strip()
        )
        if local_target:
            raise _DraftPRDeliveryError("local_target_branch_already_exists")
        existing_pull_requests = _draft_pr_read_pr_list(repo_name, branch)
        if existing_pull_requests:
            raise _DraftPRDeliveryError("target_pull_request_already_exists")

        protection_label = (
            "enabled"
            if protection is True
            else (
                "disabled (protected=false; GitHub will not enforce the local "
                "CLI safeguard)"
            )
            if protection is False
            else "unknown (do not assume GitHub-side protection)"
        )
        typer.echo("Draft PR delivery plan")
        typer.echo(f"Job: {job_id} ({source['job_status']})")
        typer.echo(f"Repository: {repo_name}")
        typer.echo(f"Base: {base_ref} @ {base_sha}")
        typer.echo(f"New branch: {branch}")
        typer.echo(
            f"Artifact: {artifact.run_id}/{artifact.artifact_id} "
            f"({artifact.content_sha256})"
        )
        typer.echo(f"Verification Evidence SHA-256: {source['evidence_sha256']}")
        typer.echo(f"Changed paths: {', '.join(sorted(paths))}")
        typer.echo(f"Base branch protection: {protection_label}")
        typer.echo("Effects: create local Git commit, push new branch, create Draft PR")
        typer.echo("Verified diff:")
        typer.echo(source["expected_diff"], nl=False)
        if not str(source["expected_diff"]).endswith("\n"):
            typer.echo()
        if yes:
            typer.echo("Operator intent: --yes")
        else:
            if not sys.stdin.isatty():
                raise _DraftPRDeliveryError("explicit_confirmation_required")
            if not typer.confirm(
                "Commit, push the new branch and create a Draft PR?", default=False
            ):
                raise _DraftPRDeliveryError("operator_declined")

        refreshed = _draft_pr_load_candidate(
            store, job_id, git_root, repo_name, base_ref
        )
        refreshed_artifact = refreshed["artifact"]
        if (
            refreshed_artifact.run_id != artifact.run_id
            or refreshed_artifact.artifact_id != artifact.artifact_id
            or refreshed_artifact.content_sha256 != artifact.content_sha256
            or refreshed["evidence_sha256"] != source["evidence_sha256"]
            or refreshed["review_artifact"].artifact_id != review_artifact.artifact_id
            or refreshed["job_status"] != source["job_status"]
        ):
            raise _DraftPRDeliveryError("candidate_changed_after_confirmation")
        if (
            _draft_pr_ls_remote(git_root, remote_url, base_ref_full) != base_sha
            or _draft_pr_ls_remote(git_root, remote_url, branch_ref) is not None
            or _draft_pr_git(
                git_root,
                "for-each-ref",
                "--format=%(refname)",
                branch_ref,
            )
            .decode("utf-8", "strict")
            .strip()
            or _draft_pr_read_pr_list(repo_name, branch)
        ):
            raise _DraftPRDeliveryError("target_changed_after_confirmation")
        if (
            _draft_pr_git(git_root, "status", "--porcelain=v1", "--untracked-files=all")
            != initial_worktree_status
        ):
            raise _DraftPRDeliveryError("source_worktree_changed")

        body_markers = (
            f"AI-Office-Job: {job_id}",
            f"AI-Office-Artifact: {artifact.artifact_id}",
            f"AI-Office-Artifact-SHA256: {artifact.content_sha256}",
            f"AI-Office-Verification-Evidence-SHA256: {source['evidence_sha256']}",
            f"AI-Office-Diff-SHA256: {evidence['diff_sha256']}",
        )
        commit_message = "\n".join(
            [
                f"Deliver verified AI Office Artifact {artifact.artifact_id[:12]}",
                "",
                *body_markers,
                f"AI-Office-Base-SHA: {base_sha}",
                f"AI-Office-Tree-SHA: {tree_sha}",
            ]
        )
        commit_sha = _draft_pr_create_commit(
            git_root,
            base_sha=base_sha,
            tree_sha=tree_sha,
            message=commit_message,
        )
        changed_rows = (
            _draft_pr_git(
                git_root,
                "diff-tree",
                "--no-commit-id",
                "--name-status",
                "--no-renames",
                "-r",
                base_sha,
                commit_sha,
            )
            .decode("utf-8", "strict")
            .splitlines()
        )
        changed = {}
        for row in changed_rows:
            parts = row.split("\t", 1)
            if len(parts) != 2:
                raise _DraftPRDeliveryError("candidate_commit_diff_invalid")
            changed[parts[1]] = parts[0]
        if set(changed) != paths or any(status != "M" for status in changed.values()):
            raise _DraftPRDeliveryError("candidate_commit_diff_mismatch")
        for item in source["changes"]:
            entry = _draft_pr_git(
                git_root, "ls-tree", "-z", "--full-tree", commit_sha, "--", item["path"]
            )
            rows = [line for line in entry.split(b"\x00") if line]
            if len(rows) != 1:
                raise _DraftPRDeliveryError("candidate_commit_content_mismatch")
            header, raw_path = rows[0].split(b"\t", 1)
            mode, kind, object_id = header.split(b" ")
            content = _draft_pr_git(
                git_root, "cat-file", "blob", object_id.decode("ascii")
            )
            if (
                raw_path.decode("utf-8") != item["path"]
                or kind != b"blob"
                or mode.decode("ascii") != source["tree_modes"][item["path"]]
                or content != base64.b64decode(item["content_base64"], validate=True)
            ):
                raise _DraftPRDeliveryError("candidate_commit_content_mismatch")

        pr_body, body_markers = _draft_pr_pr_body(
            repo_name=repo_name,
            job_id=job_id,
            job_status=source["job_status"],
            artifact=artifact,
            review_artifact=review_artifact,
            base_ref=base_ref,
            base_sha=base_sha,
            branch=branch,
            commit_sha=commit_sha,
            tree_sha=tree_sha,
            evidence_sha256=source["evidence_sha256"],
            diff_sha256=evidence["diff_sha256"],
            paths=paths,
            protected=protection,
        )
        attempt = {
            **plan,
            "body_markers": list(body_markers),
            "commit_message": commit_message,
            "commit_sha": commit_sha,
            "created_at_ns": time.time_ns(),
            "schema_version": "ai-office-draft-pr-attempt.v1",
            "tree_sha": tree_sha,
        }
        _draft_pr_write_once(attempt_path, attempt)

        def stop_after_effect(stage: str, classification: str) -> None:
            try:
                _draft_pr_write_once(
                    stop_path,
                    {
                        "classification": classification,
                        "job_id": job_id,
                        "schema_version": "ai-office-draft-pr-stop.v1",
                        "stage": stage,
                    },
                )
            except _DraftPRDeliveryError:
                pass

        try:
            _draft_pr_git(
                git_root,
                "push",
                "--no-verify",
                "--no-follow-tags",
                remote_url,
                f"{commit_sha}:{branch_ref}",
                timeout=120,
            )
        except _DraftPRDeliveryError:
            try:
                confirmed = _draft_pr_confirmed_result(
                    git_root, remote_url, repo_name, base_ref, branch, attempt, paths
                )
            except _DraftPRDeliveryError as error:
                stop_after_effect("push", error.classification)
                raise _DraftPRDeliveryError("push_result_unknown_no_retry") from None
            result_value = {
                **confirmed,
                "attempt_sha256": sha256(_draft_pr_canonical(attempt)).hexdigest(),
                "schema_version": "ai-office-draft-pr-result.v1",
            }
            _draft_pr_write_once(result_path, result_value)
            _emit_json({"operation": "draft-pr", **confirmed})
            return

        if (
            _draft_pr_ls_remote(git_root, remote_url, branch_ref) != commit_sha
            or _draft_pr_ls_remote(git_root, remote_url, base_ref_full) != base_sha
        ):
            stop_after_effect("push_verify", "remote_ref_or_base_mismatch")
            raise _DraftPRDeliveryError("pushed_ref_or_base_mismatch")

        with tempfile.TemporaryDirectory(
            prefix="ai-office-draft-pr-body-"
        ) as directory:
            body_file = Path(directory) / "body.md"
            body_file.write_text(pr_body, encoding="utf-8")
            os.chmod(body_file, 0o600)
            try:
                create_output = (
                    _draft_pr_gh(
                        "pr",
                        "create",
                        "--repo",
                        repo_name,
                        "--base",
                        base_ref,
                        "--head",
                        branch,
                        "--title",
                        "AI Office: deliver verified Artifact "
                        f"{artifact.artifact_id[:12]}",
                        "--body-file",
                        str(body_file),
                        "--draft",
                        timeout=120,
                    )
                    .decode("utf-8", "strict")
                    .strip()
                )
            except _DraftPRDeliveryError:
                try:
                    confirmed = _draft_pr_confirmed_result(
                        git_root,
                        remote_url,
                        repo_name,
                        base_ref,
                        branch,
                        attempt,
                        paths,
                    )
                except _DraftPRDeliveryError as error:
                    stop_after_effect("draft_pr", error.classification)
                    raise _DraftPRDeliveryError(
                        "draft_pr_result_unknown_no_retry"
                    ) from None
                result_value = {
                    **confirmed,
                    "attempt_sha256": sha256(_draft_pr_canonical(attempt)).hexdigest(),
                    "schema_version": "ai-office-draft-pr-result.v1",
                }
                _draft_pr_write_once(result_path, result_value)
                _emit_json({"operation": "draft-pr", **confirmed})
                return

        expected_url_prefix = f"https://github.com/{repo_name}/pull/"
        if (
            not create_output.startswith(expected_url_prefix)
            or not create_output[len(expected_url_prefix) :].isdigit()
        ):
            stop_after_effect("draft_pr_verify", "pull_request_url_invalid")
            raise _DraftPRDeliveryError("draft_pr_url_invalid")
        confirmed = _draft_pr_confirmed_result(
            git_root, remote_url, repo_name, base_ref, branch, attempt, paths
        )
        if (
            confirmed["pr_url"] != create_output
            or _draft_pr_git(
                git_root, "status", "--porcelain=v1", "--untracked-files=all"
            )
            != initial_worktree_status
        ):
            stop_after_effect("draft_pr_verify", "pull_request_or_source_mismatch")
            raise _DraftPRDeliveryError("draft_pr_verification_failed")
        confirmed["reconciled_read_only"] = False
        result_value = {
            **confirmed,
            "attempt_sha256": sha256(_draft_pr_canonical(attempt)).hexdigest(),
            "schema_version": "ai-office-draft-pr-result.v1",
        }
        _draft_pr_write_once(result_path, result_value)
        _emit_json({"operation": "draft-pr", **confirmed})
    except _DraftPRDeliveryError as error:
        _workflow_cli_error(
            f"Draft PR delivery stopped safely ({error.classification})"
        )
    except Exception:
        _workflow_cli_error("Draft PR delivery stopped safely (unexpected_local_error)")


@workflows_app.command("artifacts")
def list_workflow_artifacts(
    run_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """List verified Artifact metadata for one Run without printing contents."""
    store = _load_run_store_or_exit(run_store)
    manifest = _load_run_manifest_or_exit(store, run_id)
    try:
        artifacts = list_run_artifacts(store, run_id)
    except (WorkflowArtifactError, OSError):
        _workflow_cli_error("Run Artifacts are invalid or unavailable")
    _emit_json(
        {
            "artifacts": [_artifact_metadata_json(item) for item in artifacts],
            "manifest_digest": manifest.digest,
            "operation": "artifacts",
            "run_id": manifest.run_id,
        }
    )


@workflows_app.command("artifact")
def read_workflow_artifact(
    run_id: str,
    artifact_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """Read one verified Artifact and its exact UTF-8 business content."""
    store = _load_run_store_or_exit(run_store)
    try:
        artifact = read_run_artifact(store, run_id, artifact_id)
        content = artifact.content.decode("utf-8")
    except (WorkflowArtifactError, OSError, UnicodeDecodeError):
        _workflow_cli_error("Run Artifact is invalid or unavailable")
    _emit_json(
        {
            "artifact": {
                **_artifact_metadata_json(artifact),
                "content": content,
            },
            "operation": "artifact",
        }
    )


@workflows_app.command("artifact-export")
def export_workflow_artifact(
    run_id: str,
    artifact_id: str,
    destination: Path = typer.Option(..., "--output"),
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """Export exact verified Artifact bytes to a new local file."""
    store = _load_run_store_or_exit(run_store)
    try:
        receipt = export_run_artifact(store, run_id, artifact_id, destination)
    except (WorkflowArtifactError, OSError):
        _workflow_cli_error("Run Artifact export could not be completed")
    _emit_json(
        {
            "artifact_id": receipt.artifact_id,
            "content_length": receipt.content_length,
            "content_sha256": receipt.content_sha256,
            "destination": str(receipt.destination),
            "operation": "artifact-export",
            "run_id": run_id,
        }
    )


@workflows_app.command("local-effect-authorize")
def authorize_local_effect(
    run_id: str,
    artifact_id: str,
    policy_file: Path = typer.Option(..., "--policy"),
    workspace: Path = typer.Option(..., "--workspace"),
    approved_by: str = typer.Option(..., "--approved-by"),
    approval_id: str = typer.Option(..., "--approval-id"),
    expires_in_seconds: int = typer.Option(900, "--expires-in-seconds"),
    approve_local_effect: bool = typer.Option(False, "--approve-local-effect"),
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """Explicitly approve one exact, bounded local candidate verification."""
    if not approve_local_effect:
        _workflow_cli_error("--approve-local-effect is required")
    if not 1 <= expires_in_seconds <= 900:
        _workflow_cli_error("local-effect expiry must be between 1 and 900 seconds")
    try:
        raw = policy_file.read_bytes()
        if len(raw) > 65_536:
            _workflow_cli_error("local-effect policy is too large")
        policy_value = json.loads(raw, object_pairs_hook=_unique_json_object)
        if not isinstance(policy_value, dict):
            _workflow_cli_error("local-effect policy must be an object")
        policy_value["expires_at_ns"] = time.time_ns() + (
            expires_in_seconds * 1_000_000_000
        )
        policy = LocalExecutionPolicy.model_validate_json(
            json.dumps(policy_value, ensure_ascii=False, sort_keys=True).encode()
        )
        store = _load_run_store_or_exit(run_store)
        authorization = issue_local_execution_authorization(
            store,
            run_id=run_id,
            artifact_id=artifact_id,
            workspace=workspace,
            policy=policy,
            approved_by=approved_by,
            approval_id=approval_id,
        )
    except (OSError, TypeError, ValueError):
        _workflow_cli_error("local-effect approval could not be persisted")
    _emit_json(
        {
            "approval_id": authorization.approval_id,
            "approved_by": authorization.approved_by,
            "artifact_id": authorization.artifact_id,
            "artifact_sha256": authorization.artifact_sha256,
            "authorization_id": authorization.authorization_id,
            "base_revision": authorization.policy.base_revision,
            "commands": authorization.policy.commands,
            "expires_at_ns": authorization.policy.expires_at_ns,
            "manifest_digest": authorization.manifest_digest,
            "read_paths": authorization.policy.read_paths,
            "run_id": authorization.run_id,
            "write_paths": authorization.policy.write_paths,
            "workspace_sha256": authorization.workspace_sha256,
        }
    )


@workflows_app.command("local-effect-run")
def run_local_effect(
    run_id: str,
    artifact_id: str,
    authorization_id: str = typer.Option(..., "--authorization-id"),
    workspace: Path = typer.Option(..., "--workspace"),
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """Verify one explicitly authorized candidate in the required sandbox."""
    store = _load_run_store_or_exit(run_store)
    try:
        evidence = execute_local_change(
            store,
            run_id=run_id,
            artifact_id=artifact_id,
            authorization_id=authorization_id,
            workspace=workspace,
        )
    except (OSError, ValueError):
        _workflow_cli_error("local-effect attempt was unavailable or already claimed")
    _emit_json(evidence)
    if evidence.get("status") != "verified":
        raise typer.Exit(code=1)


def _has_approval_fields(
    approve_business: bool,
    business_approved_by: str | None,
    business_approval_id: str | None,
    approve_execution: bool,
    execution_approved_by: str | None,
    execution_approval_id: str | None,
    expected_step_id: str | None,
    expected_step_index: int | None,
    expected_employee_id: str | None,
    expected_request_fingerprint: str | None,
) -> bool:
    """Return whether any approval or execution-binding option was supplied."""
    return any(
        (
            approve_business,
            business_approved_by is not None,
            business_approval_id is not None,
            approve_execution,
            execution_approved_by is not None,
            execution_approval_id is not None,
            expected_step_id is not None,
            expected_step_index is not None,
            expected_employee_id is not None,
            expected_request_fingerprint is not None,
        )
    )


def _require_execution_options(
    preview: _WorkflowStepPreview,
    approve_business: bool,
    business_approved_by: str | None,
    business_approval_id: str | None,
    approve_execution: bool,
    execution_approved_by: str | None,
    execution_approval_id: str | None,
    expected_step_id: str | None,
    expected_step_index: int | None,
    expected_employee_id: str | None,
    expected_request_fingerprint: str | None,
    business_approval_durable: bool = False,
) -> None:
    """Reject incomplete purpose-specific approval before credential work."""
    if preview.business_approval_required:
        if not business_approval_durable and not approve_business:
            _workflow_cli_error("business approval is required")
        if approve_business:
            if business_approved_by is None or business_approved_by == "":
                _workflow_cli_error("business-approved-by is required")
            if business_approval_id is None or business_approval_id == "":
                _workflow_cli_error("business-approval-id is required")
        elif business_approved_by is not None or business_approval_id is not None:
            _workflow_cli_error("--approve-business is required for new approval")
    elif (
        approve_business
        or business_approved_by is not None
        or business_approval_id is not None
    ):
        _workflow_cli_error("business approval is not required for this step")

    if not approve_execution:
        _workflow_cli_error("execution approval is required")
    if execution_approved_by is None or execution_approved_by == "":
        _workflow_cli_error("execution-approved-by is required")
    if execution_approval_id is None or execution_approval_id == "":
        _workflow_cli_error("execution-approval-id is required")
    if (
        expected_step_id is None
        or expected_step_index is None
        or expected_employee_id is None
        or expected_request_fingerprint is None
    ):
        _workflow_cli_error("all expected preview values are required")


def _expected_preview_matches(
    preview: _WorkflowStepPreview,
    expected_step_id: str | None,
    expected_step_index: int | None,
    expected_employee_id: str | None,
    expected_request_fingerprint: str | None,
) -> bool:
    """Compare caller values with the newly rebuilt preview without coercion."""
    request = preview.step_request
    return (
        type(expected_step_id) is str
        and expected_step_id == request.step_id
        and type(expected_step_index) is int
        and not isinstance(expected_step_index, bool)
        and expected_step_index == request.step_index
        and type(expected_employee_id) is str
        and expected_employee_id == request.employee_id
        and type(expected_request_fingerprint) is str
        and expected_request_fingerprint == preview.request_fingerprint
    )


def _build_start_context(
    preview: _WorkflowStepPreview,
    business_approved_by: str | None,
    business_approval_id: str | None,
    execution_approved_by: str,
    execution_approval_id: str,
    *,
    manifest_store: WorkflowRunManifestStore,
) -> ApprovedWorkflowBootstrapContext:
    """Create the exact fresh-start context only after preview binding passes."""
    try:
        business_approval_evidence = None
        if (
            preview.business_approval_required
            and business_approved_by is not None
            and business_approval_id is not None
        ):
            if preview.run_binding is None:
                raise ValueError("business approval is incomplete")
            business_approval_evidence = approve_business_step(
                binding=preview.run_binding,
                workflow_id=preview.step_request.workflow_id,
                step_id=preview.step_request.step_id,
                step_index=preview.step_request.step_index,
                employee_id=preview.step_request.employee_id,
                approved_by=business_approved_by,
                approval_id=business_approval_id,
            )
        api_key = _load_api_key_for_target(preview.execution_target)
        preparation_approval = InitialStepPreparationApproval(
            True,
            preview.step_request.workflow_id,
            preview.step_request.step_id,
            preview.step_request.step_index,
            preview.step_request.employee_id,
            business_approval_evidence,
        )
        execution_approval = approve_model_invocation_execution(
            preview.invocation_request,
            preview.resolved_tools,
            provider=preview.execution_target.provider,
            approved_by=execution_approved_by,
            approval_id=execution_approval_id,
            execution_target=preview.execution_target,
        )
    except Exception:
        _workflow_cli_error("credential or approval configuration is invalid")
    return ApprovedWorkflowBootstrapContext(
        preparation_approval=preparation_approval,
        employee=preview.employee,
        resolved_tools=preview.resolved_tools,
        api_key=api_key,
        execution_approval=execution_approval,
        transport=send_openai_responses_http_request,
        execution_target=preview.execution_target,
        binding=preview.run_binding,
        manifest_store=manifest_store,
    )


def _build_continuation_approvals(
    decision: WorkflowProgressionDecision,
    preview: _WorkflowStepPreview,
    business_approved_by: str | None,
    business_approval_id: str | None,
    execution_approved_by: str,
    execution_approval_id: str,
) -> tuple[NextStepPreparationApproval, OpenAIApiKey, ModelInvocationExecutionApproval]:
    """Build approvals and credentials for this one explicitly previewed step."""
    try:
        business_approval_evidence = None
        if (
            preview.business_approval_required
            and business_approved_by is not None
            and business_approval_id is not None
        ):
            if preview.run_binding is None:
                raise ValueError("business approval is incomplete")
            if (
                decision.next_step_id is None
                or decision.next_step_index is None
                or decision.next_employee_id is None
            ):
                _workflow_cli_error("next-step business approval is invalid")
            business_approval_evidence = approve_business_step(
                binding=preview.run_binding,
                workflow_id=decision.workflow_id,
                step_id=decision.next_step_id,
                step_index=decision.next_step_index,
                employee_id=decision.next_employee_id,
                approved_by=business_approved_by,
                approval_id=business_approval_id,
                progression_from_step_id=decision.current_step_id,
                progression_from_step_index=decision.current_step_index,
            )
        api_key = _load_api_key_for_target(preview.execution_target)
        preparation_approval = NextStepPreparationApproval(
            True,
            decision.workflow_id,
            decision.current_step_id,
            decision.current_step_index,
            decision.next_step_id,
            decision.next_step_index,
            decision.next_employee_id,
            business_approval_evidence,
        )
        execution_approval = approve_model_invocation_execution(
            preview.invocation_request,
            preview.resolved_tools,
            provider=preview.execution_target.provider,
            approved_by=execution_approved_by,
            approval_id=execution_approval_id,
            execution_target=preview.execution_target,
        )
    except Exception:
        _workflow_cli_error("credential or approval configuration is invalid")
    return preparation_approval, api_key, execution_approval


def _durable_business_approval_exists(
    store: WorkflowRunManifestStore,
    preview: _WorkflowStepPreview,
    *,
    progression_from_step_id: str | None = None,
    progression_from_step_index: int | None = None,
) -> bool:
    """Check only authoritative exact durable Business Approval evidence."""
    if not preview.business_approval_required or preview.run_binding is None:
        return False
    try:
        manifest = load_workflow_run_manifest(store, preview.run_binding.run_id)
    except (WorkflowRunManifestError, OSError):
        return False
    if (
        manifest.digest != preview.run_binding.manifest_digest
        or manifest.workflow_id != preview.step_request.workflow_id
    ):
        return False
    try:
        evidence = find_business_approval_evidence(
            store,
            binding=preview.run_binding,
            workflow_id=preview.step_request.workflow_id,
            step_id=preview.step_request.step_id,
            step_index=preview.step_request.step_index,
            employee_id=preview.step_request.employee_id,
            progression_from_step_id=progression_from_step_id,
            progression_from_step_index=progression_from_step_index,
        )
    except Exception:
        _workflow_cli_error("Run Business Approval evidence is invalid")
    return evidence is not None


def _run_fresh_workflow(
    workflow: object,
    state_path: Path,
    events_path: Path,
    context: ApprovedWorkflowBootstrapContext,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Start step 1 once through the existing fresh-start owner."""
    try:
        return route_approved_workflow_fresh_start(
            workflow, state_path, events_path, context
        )
    except Exception:
        _workflow_cli_error("workflow execution failed")


def _read_persisted_continue_route(
    workflow: object,
    state_path: Path,
    events_path: Path,
    *,
    allow_artifact_completion: bool,
    expected_binding: WorkflowRunBinding | None = None,
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    """Run canonical persisted routing with the caller's Artifact-write policy."""
    try:
        routed = route_persisted_execution_outcome_reentry(
            workflow,
            state_path,
            events_path,
            allow_artifact_completion=allow_artifact_completion,
        )
    except Exception:
        if expected_binding is not None:
            try:
                persisted = load_workflow_execution_history(
                    WorkflowExecutionPersistenceTargets(state_path, events_path)
                )
            except Exception:
                pass
            else:
                if binding_of(persisted.state) != expected_binding:
                    _workflow_cli_error(
                        "persisted workflow Run binding is inconsistent"
                    )
        _workflow_cli_error(
            "persisted workflow state requires recovery or investigation"
        )
    if type(routed) not in (PersistedExecutionOutcome, WorkflowProgressionDecision):
        _workflow_cli_error("persisted workflow route is incompatible")
    try:
        if expected_binding is not None and binding_of(routed) != expected_binding:
            _workflow_cli_error("persisted workflow Run binding is inconsistent")
    except (TypeError, ValueError):
        _workflow_cli_error("persisted workflow Run binding is invalid")
    return routed


def _persisted_result_json(
    routed: PersistedExecutionOutcome | WorkflowProgressionDecision,
    history: object,
    *,
    run_input: str | None = None,
) -> dict[str, object]:
    """Project one canonical persisted route and its exact terminal event."""
    try:
        state = history.state
        events = history.events
        latest = events[-1]
    except (AttributeError, IndexError, TypeError):
        _workflow_cli_error("persisted workflow result is inconsistent")

    try:
        state_binding = binding_of(state)
        event_binding = binding_of(latest)
        routed_binding = binding_of(routed)
    except (TypeError, ValueError):
        _workflow_cli_error("persisted workflow result binding is invalid")
    if not (
        state_binding is not None and state_binding == event_binding == routed_binding
    ):
        _workflow_cli_error("persisted workflow result binding is inconsistent")

    state_identity = (
        state.workflow_id,
        state.current_step_id,
        state.current_step_index,
        state.current_employee_id,
    )
    binding_value = {
        "manifest_digest": state_binding.manifest_digest,
        "run_id": state_binding.run_id,
        "run_input": run_input,
    }
    routed_identity = (
        routed.workflow_id,
        routed.current_step_id,
        routed.current_step_index,
        routed.current_employee_id,
    )
    event_identity = (
        latest.workflow_id,
        latest.step_id,
        latest.step_index,
        latest.employee_id,
    )
    if state_identity != routed_identity or state_identity != event_identity:
        _workflow_cli_error("persisted workflow result is inconsistent")

    if type(routed) is WorkflowProgressionDecision:
        if routed.decision not in {"prepare_next_step", "workflow_complete"}:
            _workflow_cli_error("persisted workflow result is incompatible")
        if (
            latest.event_type != "step_succeeded"
            or latest.failure_category is not None
            or latest.message is not None
            or type(latest.output_text) is not str
        ):
            _workflow_cli_error("persisted workflow result is inconsistent")
        if routed.decision == "prepare_next_step":
            if (
                routed.reason != "next_step_available"
                or routed.next_step_id is None
                or routed.next_step_index is None
                or routed.next_employee_id is None
            ):
                _workflow_cli_error("persisted workflow result is inconsistent")
        elif routed.reason != "last_step_succeeded" or any(
            value is not None
            for value in (
                routed.next_step_id,
                routed.next_step_index,
                routed.next_employee_id,
            )
        ):
            _workflow_cli_error("persisted workflow result is inconsistent")

        return {
            "current_employee_id": routed.current_employee_id,
            "current_step_id": routed.current_step_id,
            "current_step_index": routed.current_step_index,
            "failure_category": None,
            "next_employee_id": routed.next_employee_id,
            "next_step_id": routed.next_step_id,
            "next_step_index": routed.next_step_index,
            "output": {
                "employee_id": latest.employee_id,
                "output_text": latest.output_text,
                "sha256": _upstream_output_digest(latest),
                "step_id": latest.step_id,
                "step_index": latest.step_index,
                "workflow_id": latest.workflow_id,
            },
            "reason": routed.reason,
            "status": routed.decision,
            "workflow_id": routed.workflow_id,
            **binding_value,
        }

    if type(routed) is not PersistedExecutionOutcome:
        _workflow_cli_error("persisted workflow result is incompatible")
    if (
        routed.outcome != "persisted_failure"
        or latest.event_type != "step_failed"
        or latest.output_text is not None
        or latest.failure_category != routed.failure_category
    ):
        _workflow_cli_error("persisted workflow result is inconsistent")
    return {
        "current_employee_id": routed.current_employee_id,
        "current_step_id": routed.current_step_id,
        "current_step_index": routed.current_step_index,
        "failure_category": routed.failure_category,
        "next_employee_id": None,
        "next_step_id": None,
        "next_step_index": None,
        "output": None,
        "reason": None,
        "status": routed.outcome,
        "workflow_id": routed.workflow_id,
        **binding_value,
    }


@workflows_app.command("start")
def start_workflow(
    workflow_id: str,
    run_id: str = typer.Option(..., "--run-id"),
    run_input: str | None = typer.Option(None, "--run-input"),
    run_input_file: Path | None = typer.Option(None, "--run-input-file"),
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
    preview_only: bool = typer.Option(False, "--preview-only"),
    execution_target: str = typer.Option("openai", "--execution-target"),
    execution_destinations: Path | None = typer.Option(
        None, "--execution-destinations"
    ),
    step_model: list[str] = typer.Option(
        [],
        "--step-model",
        help="Override one Workflow Step as STEP_ID=MODEL; repeat for other Steps.",
    ),
    legacy_execution_model: str | None = typer.Option(
        None, "--execution-model", hidden=True
    ),
    approve_business: bool = typer.Option(False, "--approve-business"),
    business_approved_by: str | None = typer.Option(None, "--business-approved-by"),
    business_approval_id: str | None = typer.Option(None, "--business-approval-id"),
    approve_execution: bool = typer.Option(False, "--approve-execution"),
    execution_approved_by: str | None = typer.Option(None, "--execution-approved-by"),
    execution_approval_id: str | None = typer.Option(None, "--execution-approval-id"),
    expected_step_id: str | None = typer.Option(None, "--expected-step-id"),
    expected_step_index: int | None = typer.Option(None, "--expected-step-index"),
    expected_employee_id: str | None = typer.Option(None, "--expected-employee-id"),
    expected_request_fingerprint: str | None = typer.Option(
        None, "--expected-request-fingerprint"
    ),
) -> None:
    """Preview or execute exactly one fresh workflow step."""
    _reject_legacy_execution_model(legacy_execution_model)
    if preview_only and _has_approval_fields(
        approve_business,
        business_approved_by,
        business_approval_id,
        approve_execution,
        execution_approved_by,
        execution_approval_id,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
    ):
        _workflow_cli_error("preview-only cannot include execution options")

    workflows, employees = _load_workflow_command_inputs(directory, employees_directory)
    selected_run_input = _resolve_run_input(run_input, run_input_file)
    source_workflow = _apply_step_model_overrides(
        _select_workflow_or_exit(workflows, workflow_id), step_model
    )
    execution_targets = _resolve_workflow_execution_targets(
        source_workflow, execution_target, execution_destinations
    )
    manifest, binding, pinned_workflows, pinned_employees, tool_catalog = (
        _build_start_manifest_preview(
            workflow_id,
            run_id,
            selected_run_input,
            workflows,
            employees,
            step_model,
            execution_targets=execution_targets,
        )
    )
    _validate_manifest_execution_destinations(manifest, execution_destinations)
    target = manifest.execution_destination_for_step(1).target
    workflow, preview = _build_workflow_step_preview(
        pinned_workflows,
        pinned_employees,
        workflow_id,
        1,
        execution_target=target,
        run_binding=binding,
        run_input=manifest.run_input,
        tool_catalog=tool_catalog,
    )
    if preview_only:
        _emit_json(_step_preview_json("start", preview))
        return
    if workflow.definition.bounded_review is not None:
        _workflow_cli_error("bounded review workflow requires bounded-job-run")
    durable_business_approval = False
    if run_store.exists():
        existing_store = _load_run_store_or_exit(run_store)
        durable_business_approval = _durable_business_approval_exists(
            existing_store, preview
        )
    _require_execution_options(
        preview,
        approve_business,
        business_approved_by,
        business_approval_id,
        approve_execution,
        execution_approved_by,
        execution_approval_id,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
        business_approval_durable=durable_business_approval,
    )
    assert execution_approved_by is not None and execution_approval_id is not None
    if not _expected_preview_matches(
        preview,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
    ):
        _workflow_cli_error("expected preview does not match current step")
    store = _prepare_run_store(run_store)
    _persist_start_manifest_or_exit(store, manifest)
    state_path, events_path = store.execution_paths(binding.run_id)
    context = _build_start_context(
        preview,
        business_approved_by,
        business_approval_id,
        execution_approved_by,
        execution_approval_id,
        manifest_store=store,
    )
    result = _run_fresh_workflow(
        workflow.definition,
        state_path,
        events_path,
        context,
    )
    _emit_json(_result_json("start", "execute", result, run_input=manifest.run_input))
    if type(result) is PersistedExecutionOutcome:
        raise typer.Exit(code=1)


@workflows_app.command("continue")
def continue_workflow(
    run_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
    preview_only: bool = typer.Option(False, "--preview-only"),
    execution_destinations: Path | None = typer.Option(
        None, "--execution-destinations"
    ),
    approve_business: bool = typer.Option(False, "--approve-business"),
    business_approved_by: str | None = typer.Option(None, "--business-approved-by"),
    business_approval_id: str | None = typer.Option(None, "--business-approval-id"),
    approve_execution: bool = typer.Option(False, "--approve-execution"),
    execution_approved_by: str | None = typer.Option(None, "--execution-approved-by"),
    execution_approval_id: str | None = typer.Option(None, "--execution-approval-id"),
    expected_step_id: str | None = typer.Option(None, "--expected-step-id"),
    expected_step_index: int | None = typer.Option(None, "--expected-step-index"),
    expected_employee_id: str | None = typer.Option(None, "--expected-employee-id"),
    expected_request_fingerprint: str | None = typer.Option(
        None, "--expected-request-fingerprint"
    ),
) -> None:
    """Preview or execute exactly one persisted next workflow step."""
    if preview_only and _has_approval_fields(
        approve_business,
        business_approved_by,
        business_approval_id,
        approve_execution,
        execution_approved_by,
        execution_approval_id,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
    ):
        _workflow_cli_error("preview-only cannot include execution options")

    store = _load_run_store_or_exit(run_store)
    manifest = _load_run_manifest_or_exit(store, run_id)
    binding = _run_binding_for_manifest(manifest)
    pinned_workflows, pinned_employees, tool_catalog = _pinned_run_inputs(manifest)
    _validate_manifest_execution_destinations(manifest, execution_destinations)
    workflow = _select_workflow_or_exit(pinned_workflows, manifest.workflow_id)
    if workflow.definition.bounded_review is not None:
        _workflow_cli_error("bounded review workflow requires bounded-job-run")
    state_path, events_path = store.execution_paths(binding.run_id)
    routed = _read_persisted_continue_route(
        workflow.definition,
        state_path,
        events_path,
        allow_artifact_completion=not preview_only,
        expected_binding=binding,
    )

    if type(routed) is PersistedExecutionOutcome:
        _emit_json(
            _result_json(
                "continue",
                "preview" if preview_only else "execute",
                routed,
                run_input=manifest.run_input,
            )
        )
        if not preview_only:
            raise typer.Exit(code=1)
        return
    if routed.decision == "workflow_complete":
        _emit_json(
            _result_json(
                "continue",
                "preview" if preview_only else "execute",
                routed,
                run_input=manifest.run_input,
            )
        )
        return
    if routed.decision != "prepare_next_step":
        _workflow_cli_error("persisted workflow requires recovery or investigation")

    assert routed.next_step_index is not None
    try:
        history, state_source_sha256, _events_source_sha256 = (
            load_workflow_execution_history_with_source_digests(
                WorkflowExecutionPersistenceTargets(
                    state_path,
                    events_path,
                    binding=binding,
                )
            )
        )
        upstream_inputs = build_immediate_predecessor_upstream_inputs(
            manifest.workflow_id,
            routed.next_step_index,
            history,
        )
        runtime_facts = build_persisted_continuation_runtime_facts(
            manifest.workflow_id,
            routed.next_step_index,
            history,
            state_source_sha256=state_source_sha256,
        )
    except Exception:
        _workflow_cli_error("persisted workflow handoff is invalid")
    workflow, preview = _build_workflow_step_preview(
        pinned_workflows,
        pinned_employees,
        manifest.workflow_id,
        routed.next_step_index,
        upstream_inputs,
        execution_target=manifest.execution_destination_for_step(
            routed.next_step_index
        ).target,
        runtime_facts=runtime_facts,
        run_binding=binding,
        run_input=manifest.run_input,
        tool_catalog=tool_catalog,
    )
    if preview_only:
        _emit_json(_step_preview_json("continue", preview))
        return

    durable_business_approval = _durable_business_approval_exists(
        store,
        preview,
        progression_from_step_id=routed.current_step_id,
        progression_from_step_index=routed.current_step_index,
    )
    _require_execution_options(
        preview,
        approve_business,
        business_approved_by,
        business_approval_id,
        approve_execution,
        execution_approved_by,
        execution_approval_id,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
        business_approval_durable=durable_business_approval,
    )
    assert execution_approved_by is not None and execution_approval_id is not None
    if not _expected_preview_matches(
        preview,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
    ):
        _workflow_cli_error("expected preview does not match current step")
    preparation_approval, api_key, execution_approval = _build_continuation_approvals(
        routed,
        preview,
        business_approved_by,
        business_approval_id,
        execution_approved_by,
        execution_approval_id,
    )
    try:
        result = route_approved_workflow_continuation_cycle(
            routed,
            workflow.definition,
            preparation_approval,
            preview.employee,
            state_path,
            events_path,
            preview.resolved_tools,
            api_key,
            execution_approval,
            send_openai_responses_http_request,
        )
    except Exception:
        _workflow_cli_error("workflow continuation failed")
    _emit_json(
        _result_json("continue", "execute", result, run_input=manifest.run_input)
    )
    if type(result) is PersistedExecutionOutcome:
        raise typer.Exit(code=1)


@workflows_app.command("result")
def result_workflow(
    run_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """Read one exact persisted workflow business result without executing."""
    store = _load_run_store_or_exit(run_store)
    manifest = _load_run_manifest_or_exit(store, run_id)
    binding = _run_binding_for_manifest(manifest)
    pinned_workflows, _pinned_employees, _tool_catalog = _pinned_run_inputs(manifest)
    workflow = _select_workflow_or_exit(pinned_workflows, manifest.workflow_id)
    state_path, events_path = store.execution_paths(binding.run_id)
    routed = _read_persisted_continue_route(
        workflow.definition,
        state_path,
        events_path,
        allow_artifact_completion=False,
        expected_binding=binding,
    )
    try:
        history = load_workflow_execution_history(
            WorkflowExecutionPersistenceTargets(
                state_path=state_path,
                events_path=events_path,
                binding=binding,
            )
        )
    except Exception:
        _workflow_cli_error(
            "persisted workflow state requires recovery or investigation"
        )
    _emit_json(_persisted_result_json(routed, history, run_input=manifest.run_input))
    if type(routed) is PersistedExecutionOutcome:
        raise typer.Exit(code=1)


def _recovery_assessment_json(assessment: object) -> dict[str, object]:
    return {
        "action": assessment.action,
        "eligible": assessment.eligible,
        "employee_id": assessment.employee_id,
        "events_sha256": assessment.events_sha256,
        "execution_target_fingerprint": assessment.execution_target_fingerprint,
        "invocation_fingerprint": assessment.invocation_fingerprint,
        "manifest_digest": assessment.manifest_digest,
        "normalized_result_evidence_sha256": (
            assessment.normalized_result_evidence_sha256
        ),
        "operation": "recovery",
        "previous_attempt_evidence_sha256": (
            assessment.previous_attempt_evidence_sha256
        ),
        "previous_attempt_id": assessment.previous_attempt_id,
        "provider": assessment.provider,
        "raw_response_evidence_sha256": assessment.raw_response_evidence_sha256,
        "reason": assessment.reason,
        "recovery_decision_sha256": assessment.digest,
        "run_id": assessment.run_id,
        "state_sha256": assessment.state_sha256,
        "state_status": assessment.state_status,
        "step_id": assessment.step_id,
        "step_index": assessment.step_index,
        "workflow_id": assessment.workflow_id,
    }


def _build_recovery_preview(
    store: WorkflowRunManifestStore,
    manifest: object,
    binding: WorkflowRunBinding,
    pinned_workflows: list[object],
    pinned_employees: list[object],
    tool_catalog: object,
    assessment: object,
    target: ModelExecutionTarget,
) -> tuple[object, _WorkflowStepPreview, object, str, str, object]:
    """Reconstruct the pinned request for the exact current recovery attempt."""
    from hashlib import sha256

    from ai_office.engine.persisted_continuation_runtime_facts import (
        build_persisted_continuation_runtime_facts,
    )
    from ai_office.engine.upstream_step_output_handoff import (
        build_immediate_predecessor_upstream_inputs,
    )
    from ai_office.execution_evidence import (
        execution_target_fingerprint,
        load_execution_attempt_evidence,
    )
    from ai_office.invocation import (
        EMPTY_RUNTIME_FACTS,
        build_model_invocation_task_input,
    )
    from ai_office.runtime import WorkflowExecutionState
    from ai_office.storage.workflow_execution_history import (
        LoadedWorkflowExecutionHistory,
        load_workflow_execution_history_with_source_digests,
    )
    from ai_office.storage.workflow_execution_persistence import (
        WorkflowExecutionPersistenceTargets,
        serialize_workflow_execution_state_json,
    )

    try:
        workflow = _select_workflow_or_exit(pinned_workflows, manifest.workflow_id)
        state_path, events_path = store.execution_paths(binding.run_id)
        history, state_digest, events_digest = (
            load_workflow_execution_history_with_source_digests(
                WorkflowExecutionPersistenceTargets(
                    state_path, events_path, binding=binding
                )
            )
        )
        state = history.state
        if (
            state.status not in {"running", "failed"}
            or state.workflow_id != manifest.workflow_id
            or state.current_step_id != assessment.step_id
            or state.current_step_index != assessment.step_index
            or state.current_employee_id != assessment.employee_id
            or state.current_step_index > len(workflow.definition.steps)
        ):
            _workflow_cli_error("recovery state does not match the pinned step")
        expected_completed = tuple(
            step.id
            for step in workflow.definition.steps[: state.current_step_index - 1]
        )
        if state.completed_step_ids != expected_completed:
            _workflow_cli_error("completed predecessor history is inconsistent")

        attempt = load_execution_attempt_evidence(
            store.root, binding.run_id, assessment.previous_attempt_id
        )
        if (
            attempt.digest != assessment.previous_attempt_evidence_sha256
            or attempt.workflow_id != manifest.workflow_id
            or attempt.step_id != state.current_step_id
            or attempt.step_index != state.current_step_index
            or attempt.employee_id != state.current_employee_id
            or attempt.invocation_fingerprint != assessment.invocation_fingerprint
        ):
            _workflow_cli_error("recovery attempt binding is inconsistent")

        upstream_inputs: tuple[object, ...] = ()
        runtime_facts = EMPTY_RUNTIME_FACTS
        if state.current_step_index > 1:
            predecessor_index = state.current_step_index - 1
            predecessor_definition = workflow.definition.steps[predecessor_index - 1]
            matches = tuple(
                (event_index, event)
                for event_index, event in enumerate(history.events)
                if event.event_type == "step_succeeded"
                and event.step_index == predecessor_index
                and event.step_id == predecessor_definition.id
                and event.employee_id == predecessor_definition.employee
            )
            if len(matches) != 1:
                _workflow_cli_error("completed predecessor evidence is inconsistent")
            event_index, predecessor_event = matches[0]
            predecessor_state = WorkflowExecutionState(
                workflow_id=manifest.workflow_id,
                status="succeeded",
                current_step_id=predecessor_definition.id,
                current_step_index=predecessor_index,
                current_employee_id=predecessor_definition.employee,
                completed_step_ids=expected_completed,
                last_failure_category=None,
                binding=binding,
            )
            prior_history = LoadedWorkflowExecutionHistory(
                state=predecessor_state,
                events=history.events[: event_index + 1],
            )
            prior_state_bytes = serialize_workflow_execution_state_json(
                predecessor_state
            ).encode("utf-8")
            upstream_inputs = build_immediate_predecessor_upstream_inputs(
                manifest.workflow_id, state.current_step_index, prior_history
            )
            runtime_facts = build_persisted_continuation_runtime_facts(
                manifest.workflow_id,
                state.current_step_index,
                prior_history,
                state_source_sha256=sha256(prior_state_bytes).hexdigest(),
            )

        if execution_target_fingerprint(target) != attempt.execution_target_fingerprint:
            _workflow_cli_error("recovery cannot change the pinned execution target")
        _workflow, preview = _build_workflow_step_preview(
            pinned_workflows,
            pinned_employees,
            manifest.workflow_id,
            state.current_step_index,
            upstream_inputs,
            execution_target=target,
            runtime_facts=runtime_facts,
            run_binding=binding,
            run_input=manifest.run_input,
            tool_catalog=tool_catalog,
        )
        task_digest = sha256(
            build_model_invocation_task_input(preview.invocation_request).encode(
                "utf-8"
            )
        ).hexdigest()
        if (
            preview.request_fingerprint != attempt.invocation_fingerprint
            or sha256(
                preview.invocation_request.system_instructions.encode("utf-8")
            ).hexdigest()
            != attempt.system_instruction_sha256
            or task_digest != attempt.task_input_sha256
            or preview.execution_target.provider != attempt.provider
        ):
            _workflow_cli_error("pinned recovery invocation is inconsistent")
        return workflow, preview, history, state_digest, events_digest, attempt
    except typer.Exit:
        raise
    except Exception:
        _workflow_cli_error("pinned recovery request could not be reconstructed")


def _existing_attempt_evidence_context(
    store: WorkflowRunManifestStore,
    preview: _WorkflowStepPreview,
    attempt: object,
) -> object:
    from ai_office.engine.workflow_approval_evidence import (
        load_execution_approval_evidence,
        validate_execution_approval_evidence,
    )
    from ai_office.execution_evidence import (
        build_execution_evidence_context,
        execution_target_fingerprint,
    )

    assert preview.run_binding is not None
    try:
        evidence = load_execution_approval_evidence(
            store, preview.run_binding.run_id, attempt.execution_approval_id
        )
        validate_execution_approval_evidence(
            evidence,
            binding=preview.run_binding,
            workflow_id=preview.step_request.workflow_id,
            step_id=preview.step_request.step_id,
            step_index=preview.step_request.step_index,
            employee_id=preview.step_request.employee_id,
            provider=attempt.provider,
            execution_target_fingerprint_value=execution_target_fingerprint(
                preview.execution_target
            ),
            request_fingerprint=preview.request_fingerprint,
        )
        if evidence.digest != attempt.execution_approval_evidence_sha256:
            raise ValueError("execution approval does not match attempt")
        approval = approve_model_invocation_execution(
            preview.invocation_request,
            preview.resolved_tools,
            provider=attempt.provider,
            approved_by=evidence.approved_by,
            approval_id=evidence.approval_id,
            execution_target=preview.execution_target,
        )
        return build_execution_evidence_context(
            store_root=store.root,
            binding=preview.run_binding,
            workflow_id=preview.step_request.workflow_id,
            step_id=preview.step_request.step_id,
            step_index=preview.step_request.step_index,
            employee_id=preview.step_request.employee_id,
            request=preview.invocation_request,
            resolved_tools=preview.resolved_tools,
            approval=approval,
            target=preview.execution_target,
        )
    except Exception:
        _workflow_cli_error("existing attempt approval is invalid")


def _recovery_runtime_result(preview: _WorkflowStepPreview, result: object) -> object:
    from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
    from ai_office.runtime import (
        StepRuntimeExecutionFailure,
        StepRuntimeExecutionSuccess,
    )

    identity = {
        "workflow_id": preview.step_request.workflow_id,
        "step_id": preview.step_request.step_id,
        "step_index": preview.step_request.step_index,
        "employee_id": preview.step_request.employee_id,
        "binding": preview.run_binding,
    }
    if type(result) is ModelInvocationSuccess:
        return StepRuntimeExecutionSuccess(**identity, invocation_result=result)
    if type(result) is ModelInvocationFailure:
        return StepRuntimeExecutionFailure(**identity, invocation_result=result)
    _workflow_cli_error("durable normalized result is invalid")


@workflows_app.command("recovery")
def inspect_workflow_recovery(
    run_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
) -> None:
    """Inspect a Run's recovery eligibility using read-only durable evidence."""
    from ai_office.engine.workflow_recovery import assess_workflow_recovery

    store = _load_run_store_or_exit(run_store)
    try:
        assessment = assess_workflow_recovery(store.root, run_id)
    except Exception:
        _workflow_cli_error("Run recovery evidence is invalid or unavailable")
    _emit_json(_recovery_assessment_json(assessment))
    if not assessment.eligible:
        raise typer.Exit(code=1)


@workflows_app.command("recover")
def recover_workflow(
    run_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
    recovery_decision_sha256: str = typer.Option(..., "--recovery-decision-sha256"),
    approve_recovery: bool = typer.Option(False, "--approve-recovery"),
    recovery_approved_by: str | None = typer.Option(None, "--recovery-approved-by"),
    recovery_approval_id: str | None = typer.Option(None, "--recovery-approval-id"),
    execution_destinations: Path | None = typer.Option(
        None, "--execution-destinations"
    ),
    approve_execution: bool = typer.Option(False, "--approve-execution"),
    execution_approved_by: str | None = typer.Option(None, "--execution-approved-by"),
    execution_approval_id: str | None = typer.Option(None, "--execution-approval-id"),
    expected_step_id: str | None = typer.Option(None, "--expected-step-id"),
    expected_step_index: int | None = typer.Option(None, "--expected-step-index"),
    expected_employee_id: str | None = typer.Option(None, "--expected-employee-id"),
    expected_request_fingerprint: str | None = typer.Option(
        None, "--expected-request-fingerprint"
    ),
) -> None:
    """Apply one explicit recovery decision to the exact current Run evidence."""
    from ai_office.engine.prepared_step_execution_start import (
        PreparedStepExecutionStart,
    )
    from ai_office.engine.workflow_approval_evidence import (
        build_execution_approval_evidence_for_tools,
        build_recovery_approval_evidence,
        persist_execution_approval_evidence,
        persist_recovery_approval_evidence,
    )
    from ai_office.engine.workflow_recovery import (
        WorkflowRecoveryAssessment,
        assess_workflow_recovery,
        validate_workflow_recovery_authorization,
    )
    from ai_office.execution_evidence import (
        execution_target_fingerprint,
        load_normalized_result_evidence,
        load_raw_response_evidence,
        persist_normalized_result_evidence,
    )
    from ai_office.providers.openai import (
        OpenAIResponsesRawHttpResponse,
        normalize_openai_compatible_raw_response,
    )
    from ai_office.runtime import WorkflowExecutionState
    from ai_office.runtime.executed_step_transition_persistence import (
        persist_executed_step_transition,
    )
    from ai_office.runtime.persisted_start_execution import (
        execute_persisted_start_openai_step,
    )

    store = _load_run_store_or_exit(run_store)
    try:
        assessment = assess_workflow_recovery(store.root, run_id)
    except Exception:
        _workflow_cli_error("Run recovery evidence is invalid or unavailable")
    if type(assessment) is not WorkflowRecoveryAssessment or not assessment.eligible:
        _workflow_cli_error("Run is not eligible for explicit recovery")
    if recovery_decision_sha256 != assessment.digest:
        _workflow_cli_error("recovery decision is stale or does not match")
    if not approve_recovery or not recovery_approved_by or not recovery_approval_id:
        _workflow_cli_error("Recovery Approval requires explicit approval fields")

    manifest = _load_run_manifest_or_exit(store, run_id)
    binding = _run_binding_for_manifest(manifest)
    pinned_workflows, pinned_employees, tool_catalog = _pinned_run_inputs(manifest)
    if assessment.step_index is None:
        _workflow_cli_error("recovery attempt does not identify a pinned Step")
    requested_target = manifest.execution_destination_for_step(
        assessment.step_index
    ).target
    if requested_target.provider != assessment.provider:
        _workflow_cli_error("recovery evidence differs from the Run Manifest")
    _validate_manifest_execution_destinations(manifest, execution_destinations)
    state_path, events_path = store.execution_paths(run_id)
    workflow, preview, history, state_digest, events_digest, attempt = (
        _build_recovery_preview(
            store,
            manifest,
            binding,
            pinned_workflows,
            pinned_employees,
            tool_catalog,
            assessment,
            requested_target,
        )
    )
    if (state_digest, events_digest) != (
        assessment.state_sha256,
        assessment.events_sha256,
    ):
        _workflow_cli_error("recovery decision became stale")

    recovery_evidence = build_recovery_approval_evidence(
        assessment,
        approved_by=recovery_approved_by,
        approval_id=recovery_approval_id,
    )

    if assessment.action in {"complete_result", "complete_raw_response"}:
        if any(
            (
                approve_execution,
                execution_approved_by is not None,
                execution_approval_id is not None,
                expected_step_id is not None,
                expected_step_index is not None,
                expected_employee_id is not None,
                expected_request_fingerprint is not None,
            )
        ):
            _workflow_cli_error(
                "deterministic completion does not accept execution options"
            )
        try:
            persist_recovery_approval_evidence(store, recovery_evidence)
            validate_workflow_recovery_authorization(
                store.root, assessment, recovery_evidence
            )
            if assessment.action == "complete_result":
                normalized = load_normalized_result_evidence(
                    store.root, run_id, attempt.attempt_id
                )
            else:
                raw = load_raw_response_evidence(store.root, run_id, attempt.attempt_id)
                execution_evidence = _existing_attempt_evidence_context(
                    store, preview, attempt
                )
                raw_response = OpenAIResponsesRawHttpResponse(
                    status_code=raw.status_code,
                    reason="",
                    headers=raw.safe_headers,
                    body=raw.body,
                )
                outcome = normalize_openai_compatible_raw_response(
                    raw_response,
                    protocol=attempt.execution_target_protocol,
                    provider=attempt.provider,
                )
                normalized = persist_normalized_result_evidence(
                    execution_evidence,
                    attempt,
                    outcome,
                    raw_response=raw,
                )
            runtime_result = _recovery_runtime_result(preview, normalized.result)
            persist_executed_step_transition(runtime_result, state_path, events_path)
        except Exception:
            _workflow_cli_error("durable recovery completion could not be persisted")
    else:
        if (
            not approve_execution
            or not execution_approved_by
            or not execution_approval_id
        ):
            _workflow_cli_error(
                "provider recovery requires a new explicit Execution Approval"
            )
        if not _expected_preview_matches(
            preview,
            expected_step_id,
            expected_step_index,
            expected_employee_id,
            expected_request_fingerprint,
        ):
            _workflow_cli_error("expected recovery preview does not match")
        if preview.business_approval_required:
            predecessor = (
                None
                if preview.step_request.step_index == 1
                else pinned_workflows[0].definition.steps[
                    preview.step_request.step_index - 2
                ]
            )
            if not _durable_business_approval_exists(
                store,
                preview,
                progression_from_step_id=(
                    None if predecessor is None else predecessor.id
                ),
                progression_from_step_index=(
                    None if predecessor is None else preview.step_request.step_index - 1
                ),
            ):
                _workflow_cli_error("the step's durable Business Approval is required")
        target = preview.execution_target
        if requested_target != target:
            _workflow_cli_error("recovery cannot change the pinned execution target")
        if execution_target_fingerprint(target) != attempt.execution_target_fingerprint:
            _workflow_cli_error("recovery cannot change the pinned execution target")
        try:
            api_key = _load_api_key_for_target(target)
            execution_approval = approve_model_invocation_execution(
                preview.invocation_request,
                preview.resolved_tools,
                provider=target.provider,
                approved_by=execution_approved_by,
                approval_id=execution_approval_id,
                execution_target=target,
            )
            execution_evidence = build_execution_approval_evidence_for_tools(
                preview.invocation_request,
                preview.resolved_tools,
                execution_approval,
                workflow_id=preview.step_request.workflow_id,
                step_id=preview.step_request.step_id,
                step_index=preview.step_request.step_index,
                employee_id=preview.step_request.employee_id,
                target=target,
            )
            persist_execution_approval_evidence(store, execution_evidence)
            persist_recovery_approval_evidence(store, recovery_evidence)
            validate_workflow_recovery_authorization(
                store.root, assessment, recovery_evidence
            )
            running_state = WorkflowExecutionState(
                workflow_id=preview.step_request.workflow_id,
                status="running",
                current_step_id=preview.step_request.step_id,
                current_step_index=preview.step_request.step_index,
                current_employee_id=preview.step_request.employee_id,
                completed_step_ids=history.state.completed_step_ids,
                last_failure_category=None,
                binding=binding,
            )
            start = PreparedStepExecutionStart(
                preview.invocation_request,
                running_state,
            )
            result = execute_persisted_start_openai_step(
                start,
                state_path,
                workflow.definition,
                preview.employee,
                preview.resolved_tools,
                api_key,
                execution_approval,
                transport=send_openai_responses_http_request,
                events_path=events_path,
                recovery_assessment=assessment,
                recovery_approval=recovery_evidence,
            )
            persist_executed_step_transition(result, state_path, events_path)
        except Exception:
            _workflow_cli_error("explicit provider recovery failed")

    routed = _read_persisted_continue_route(
        workflow.definition,
        state_path,
        events_path,
        allow_artifact_completion=True,
        expected_binding=binding,
    )
    _emit_json(_result_json("recover", "execute", routed, run_input=manifest.run_input))
    if type(routed) is PersistedExecutionOutcome:
        raise typer.Exit(code=1)


@workflows_app.command("plan")
def plan_workflow(
    workflow_id: str,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Build and display a validated workflow execution plan without running it."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
    except WorkflowSelectionError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    _display_execution_plan(plan)
    if workflow.definition.bounded_review is not None:
        flow = workflow.definition.bounded_review
        typer.echo()
        typer.echo("Bounded review: draft -> independent review")
        typer.echo("CHANGES_REQUIRED: revision -> independent re-review")
        typer.echo("PASS: stop as human-acceptance candidate; BLOCKED: stop")
        typer.echo(f"Maximum revisions: {flow.max_revisions}")


@workflows_app.command("request", context_settings={"ignore_unknown_options": True})
def request_workflow_step(
    workflow_id: str,
    step_index: int,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Build and display one step request without executing it."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        request = build_step_execution_request(plan, step_index, employees)
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    _display_step_execution_request(request)


@workflows_app.command("invocation", context_settings={"ignore_unknown_options": True})
def invocation_workflow_step(
    workflow_id: str,
    step_index: int,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Build and display one provider-independent model invocation request."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(plan, step_index, employees)
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
    ) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None

    _display_model_invocation_request(build_model_invocation_request(step_request))


@workflows_app.command(
    "provider-request", context_settings={"ignore_unknown_options": True}
)
def provider_request_workflow_step(
    provider: str,
    workflow_id: str,
    step_index: str,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Build and display one provider-specific pre-runtime request."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(plan, int(step_index), employees)
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
        ValueError,
    ) as error:
        message = (
            f"invalid step index: {step_index}"
            if isinstance(error, ValueError)
            else str(error)
        )
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1) from None

    if provider != "openai":
        typer.echo(f"Error: unsupported provider: {provider}", err=True)
        raise typer.Exit(code=1)

    invocation_request = build_model_invocation_request(step_request)
    _display_openai_responses_request(
        build_openai_responses_request(invocation_request)
    )


@workflows_app.command(
    "resolve-tools", context_settings={"ignore_unknown_options": True}
)
def resolve_workflow_step_tools(
    workflow_id: str,
    step_index: str,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Resolve one step's allowed tool names without executing a tool."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(plan, int(step_index), employees)
        invocation_request = build_model_invocation_request(step_request)
        resolved_tools = resolve_tool_names(
            DEFAULT_TOOL_CATALOG, invocation_request.allowed_tools
        )
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
        ToolCatalogError,
        ValueError,
    ) as error:
        message = (
            f"invalid step index: {step_index}"
            if isinstance(error, ValueError) and not isinstance(error, ToolCatalogError)
            else str(error)
        )
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1) from None

    _display_resolved_tools(resolved_tools)


@workflows_app.command(
    "provider-tools", context_settings={"ignore_unknown_options": True}
)
def provider_workflow_step_tools(
    provider: str,
    workflow_id: str,
    step_index: str,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Build and display static provider-specific tool schema models."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(plan, int(step_index), employees)
        invocation_request = build_model_invocation_request(step_request)
        resolved_tools = resolve_tool_names(
            DEFAULT_TOOL_CATALOG, invocation_request.allowed_tools
        )
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
        ToolCatalogError,
        ValueError,
    ) as error:
        message = (
            f"invalid step index: {step_index}"
            if isinstance(error, ValueError) and not isinstance(error, ToolCatalogError)
            else str(error)
        )
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1) from None

    if provider != "openai":
        typer.echo(f"Error: unsupported provider: {provider}", err=True)
        raise typer.Exit(code=1)
    _display_openai_responses_tools(build_openai_responses_tools(resolved_tools))


@workflows_app.command(
    "provider-payload", context_settings={"ignore_unknown_options": True}
)
def provider_workflow_step_payload(
    provider: str,
    workflow_id: str,
    step_index: str,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Build and display one static provider payload model."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(plan, int(step_index), employees)
        invocation_request = build_model_invocation_request(step_request)
        payload = build_openai_responses_payload_from_invocation(
            invocation_request, DEFAULT_TOOL_CATALOG
        )
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
        ToolCatalogError,
        ValueError,
    ) as error:
        message = (
            f"invalid step index: {step_index}"
            if isinstance(error, ValueError) and not isinstance(error, ToolCatalogError)
            else str(error)
        )
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1) from None

    if provider != "openai":
        typer.echo(f"Error: unsupported provider: {provider}", err=True)
        raise typer.Exit(code=1)
    _display_openai_responses_payload(payload)


@workflows_app.command(
    "provider-dict-payload", context_settings={"ignore_unknown_options": True}
)
def provider_workflow_step_dict_payload(
    provider: str,
    workflow_id: str,
    step_index: str,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Build and display one static provider dictionary payload."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(plan, int(step_index), employees)
        invocation_request = build_model_invocation_request(step_request)
        payload = build_openai_responses_payload_dict_from_invocation(
            invocation_request, DEFAULT_TOOL_CATALOG
        )
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
        ToolCatalogError,
        ValueError,
    ) as error:
        message = (
            f"invalid step index: {step_index}"
            if isinstance(error, ValueError) and not isinstance(error, ToolCatalogError)
            else str(error)
        )
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1) from None

    if provider != "openai":
        typer.echo(f"Error: unsupported provider: {provider}", err=True)
        raise typer.Exit(code=1)
    _display_openai_responses_dictionary_payload(payload)


@workflows_app.command(
    "provider-json", context_settings={"ignore_unknown_options": True}
)
def provider_workflow_step_json(
    provider: str,
    workflow_id: str,
    step_index: str,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Build and display one static provider payload as pretty JSON."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(plan, int(step_index), employees)
        invocation_request = build_model_invocation_request(step_request)
        payload_dict = build_openai_responses_payload_dict_from_invocation(
            invocation_request, DEFAULT_TOOL_CATALOG
        )
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
        ToolCatalogError,
        ValueError,
    ) as error:
        message = (
            f"invalid step index: {step_index}"
            if isinstance(error, ValueError) and not isinstance(error, ToolCatalogError)
            else str(error)
        )
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1) from None

    if provider != "openai":
        typer.echo(f"Error: unsupported provider: {provider}", err=True)
        raise typer.Exit(code=1)
    typer.echo("Provider: openai")
    typer.echo("JSON payload:")
    typer.echo(serialize_openai_responses_payload_dict_pretty(payload_dict))


@workflows_app.command(
    "provider-http-request", context_settings={"ignore_unknown_options": True}
)
def provider_workflow_step_http_request(
    provider: str,
    workflow_id: str,
    step_index: str,
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
) -> None:
    """Build and display one unauthenticated provider HTTP request template."""
    workflows, employees = _load_validated_definitions_or_exit(
        directory, employees_directory
    )
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(plan, int(step_index), employees)
        invocation_request = build_model_invocation_request(step_request)
        request_template = build_openai_responses_http_request_from_invocation(
            invocation_request, DEFAULT_TOOL_CATALOG
        )
    except (
        WorkflowSelectionError,
        StepSelectionError,
        EmployeeSelectionError,
        ToolCatalogError,
        ValueError,
    ) as error:
        message = (
            f"invalid step index: {step_index}"
            if isinstance(error, ValueError) and not isinstance(error, ToolCatalogError)
            else str(error)
        )
        typer.echo(f"Error: {message}", err=True)
        raise typer.Exit(code=1) from None

    if provider != "openai":
        typer.echo(f"Error: unsupported provider: {provider}", err=True)
        raise typer.Exit(code=1)
    typer.echo("Provider: openai")
    typer.echo("HTTP request template:")
    typer.echo(f"Method: {request_template.method}")
    typer.echo(f"URL: {request_template.url}")
    typer.echo("Headers:")
    for name, value in request_template.headers:
        typer.echo(f"  {name}: {value}")
    typer.echo("Body:")
    typer.echo(request_template.body)
