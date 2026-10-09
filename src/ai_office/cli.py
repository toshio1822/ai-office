"""Command-line interface for AI Office."""

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import typer

from ai_office.definitions.employee import (
    EmployeeDefinition,
    EmployeeLoadError,
    load_employees,
)
from ai_office.definitions.workflow import (
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
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
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
from ai_office.execution_evidence import inspect_run_execution_evidence
from ai_office.execution_target import (
    ModelExecutionTarget,
    ModelExecutionTargetError,
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
        typer.echo(f"{definition.id}\t{definition.name}\t{definition.model}")


@employees_app.command("validate")
def validate_employees(
    directory: Path = typer.Option(Path("employees"), "--directory"),
) -> None:
    """Validate all employee definitions."""
    employees = _load_employees_or_exit(directory)
    typer.echo(f"Validated {len(employees)} employee definition(s).")


def _load_validated_definitions_or_exit(
    directory: Path, employees_directory: Path
):
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
                typer.echo(
                    f"            description: {property_value['description']}"
                )
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


def _load_run_manifest_or_exit(
    store: WorkflowRunManifestStore, run_id: str
) -> object:
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
    execution_model: str | None = None,
    execution_target: ModelExecutionTarget | None = None,
) -> tuple[object, WorkflowRunBinding, object, list[object], object]:
    """Freeze fresh Run meaning in memory before preview or durable creation."""
    if run_id is None or run_input is None:
        _workflow_cli_error("--run-id and --run-input are required")
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        manifest = build_workflow_run_manifest(
            run_id,
            run_input,
            workflow,
            employees,
            tool_catalog=DEFAULT_TOOL_CATALOG,
            execution_model=execution_model,
            execution_target=execution_target,
        )
        binding = _run_binding_for_manifest(manifest)
        pinned_workflows, pinned_employees, catalog = _pinned_run_inputs(manifest)
        return manifest, binding, pinned_workflows, pinned_employees, catalog
    except (WorkflowSelectionError, WorkflowRunManifestError):
        _workflow_cli_error("fresh Run definition or input is invalid")


def _resolve_run_input(
    run_input: str | None, run_input_file: Path | None
) -> str:
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


def _resolve_execution_model(
    target: ModelExecutionTarget,
    registry_path: Path | None,
    model: str | None,
) -> str | None:
    """Validate an operator model against the selected registry policy."""
    if target.provider in {"openai", "omniroute"}:
        if model is not None:
            _workflow_cli_error("--execution-model requires a configured destination")
        return None
    if registry_path is None or model is None:
        _workflow_cli_error("configured destinations require --execution-model")
    try:
        return (
            load_execution_destination_registry(registry_path)
            .resolve(target.provider)
            .select_model(model)
        )
    except ExecutionDestinationError:
        _workflow_cli_error("execution model is invalid")


def _validate_pinned_models_for_target(
    target: ModelExecutionTarget,
    registry_path: Path | None,
    employees: list[object],
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
        for employee in employees:
            destination.select_model(employee.definition.model)
    except (AttributeError, ExecutionDestinationError):
        _workflow_cli_error("pinned execution model is no longer permitted")


def _validate_manifest_execution_target(
    manifest: object, target: ModelExecutionTarget
) -> None:
    """Prevent a configured Run from switching destination or configuration."""
    destination = getattr(manifest, "execution_destination", None)
    if destination is None:
        if target.configuration_fingerprint is not None:
            _workflow_cli_error(
                "Run Manifest does not pin a configured execution destination"
            )
        return
    try:
        pinned_target = destination.target
    except Exception:
        _workflow_cli_error("Run Manifest execution destination is invalid")
    if target != pinned_target:
        _workflow_cli_error("execution target differs from the Run Manifest")


def _load_api_key_for_target(target: ModelExecutionTarget) -> OpenAIApiKey:
    """Keep direct-OpenAI compatibility while selecting target credentials."""
    if target.provider == "openai":
        return load_openai_api_key_from_environment()
    return load_api_key_for_execution_target(target)


def _select_workflow_or_exit(
    workflows: list[object], workflow_id: str
) -> object:
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
) -> None:
    """Inspect safe, strict execution evidence for one Run without execution."""
    store = _load_run_store_or_exit(run_store)
    manifest = _load_run_manifest_or_exit(store, run_id)
    try:
        evidence = inspect_run_execution_evidence(store.root, run_id)
    except Exception:
        _workflow_cli_error("Run execution evidence is invalid or unavailable")
    _emit_json(
        {
            "attempts": [asdict(item) for item in evidence],
            "manifest_digest": manifest.digest,
            "operation": "execution-evidence",
            "run_id": manifest.run_id,
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
            "terminal_success_event_sha256": (
                artifact.terminal_success_event_sha256
            ),
        },
        "run_id": artifact.run_id,
        "step_id": artifact.step_id,
        "step_index": artifact.step_index,
        "workflow_id": artifact.workflow_id,
    }


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
        state_binding is not None
        and state_binding == event_binding == routed_binding
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
    execution_model: str | None = typer.Option(None, "--execution-model"),
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
    target = _resolve_execution_target(execution_target, execution_destinations)
    selected_model = _resolve_execution_model(
        target, execution_destinations, execution_model
    )
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

    workflows, employees = _load_workflow_command_inputs(
        directory, employees_directory
    )
    selected_run_input = _resolve_run_input(run_input, run_input_file)
    manifest, binding, pinned_workflows, pinned_employees, tool_catalog = (
        _build_start_manifest_preview(
            workflow_id,
            run_id,
            selected_run_input,
            workflows,
            employees,
            selected_model,
            target if selected_model is not None else None,
        )
    )
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
    _emit_json(
        _result_json("start", "execute", result, run_input=manifest.run_input)
    )
    if type(result) is PersistedExecutionOutcome:
        raise typer.Exit(code=1)


@workflows_app.command("continue")
def continue_workflow(
    run_id: str,
    run_store: Path = typer.Option(Path("runs"), "--run-store", "--run-root"),
    preview_only: bool = typer.Option(False, "--preview-only"),
    execution_target: str = typer.Option("openai", "--execution-target"),
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
    target = _resolve_execution_target(execution_target, execution_destinations)
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
    _validate_manifest_execution_target(manifest, target)
    _validate_pinned_models_for_target(target, execution_destinations, pinned_employees)
    workflow = _select_workflow_or_exit(pinned_workflows, manifest.workflow_id)
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
        execution_target=target,
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
    _emit_json(
        _persisted_result_json(routed, history, run_input=manifest.run_input)
    )
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
            for step in workflow.definition.steps[
                : state.current_step_index - 1
            ]
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
            or sha256(preview.invocation_request.system_instructions.encode("utf-8"))
            .hexdigest()
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
        return StepRuntimeExecutionSuccess(
            **identity, invocation_result=result
        )
    if type(result) is ModelInvocationFailure:
        return StepRuntimeExecutionFailure(
            **identity, invocation_result=result
        )
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
    execution_target: str = typer.Option("openai", "--execution-target"),
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
    if (
        not approve_recovery
        or not recovery_approved_by
        or not recovery_approval_id
    ):
        _workflow_cli_error("Recovery Approval requires explicit approval fields")

    manifest = _load_run_manifest_or_exit(store, run_id)
    binding = _run_binding_for_manifest(manifest)
    pinned_workflows, pinned_employees, tool_catalog = _pinned_run_inputs(manifest)
    requested_target = _resolve_execution_target(
        execution_target, execution_destinations
    )
    if requested_target.provider != assessment.provider:
        _workflow_cli_error("recovery cannot change the pinned execution target")
    _validate_manifest_execution_target(manifest, requested_target)
    _validate_pinned_models_for_target(
        requested_target, execution_destinations, pinned_employees
    )
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
                raw = load_raw_response_evidence(
                    store.root, run_id, attempt.attempt_id
                )
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
            runtime_result = _recovery_runtime_result(
                preview, normalized.result
            )
            persist_executed_step_transition(
                runtime_result, state_path, events_path
            )
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
    _emit_json(
        _result_json("recover", "execute", routed, run_input=manifest.run_input)
    )
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
