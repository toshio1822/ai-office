"""Command-line interface for AI Office."""

import json
from dataclasses import dataclass
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
    ApprovedWorkflowContinuationContext,
    InitialStepPreparationApproval,
    NextStepPreparationApproval,
    classify_persisted_execution_outcome_reentry,
    route_approved_fresh_workflow_bounded,
    route_persisted_execution_outcome_reentry,
    route_persisted_terminal_workflow_bounded,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationRequest,
    approve_model_invocation_execution,
    build_model_invocation_execution_fingerprint,
    build_model_invocation_request,
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
    OpenAIResponsesFunctionTool,
    OpenAIResponsesPayload,
    OpenAIResponsesRequest,
    build_openai_responses_http_request_from_invocation,
    build_openai_responses_payload_dict_from_invocation,
    build_openai_responses_payload_from_invocation,
    build_openai_responses_request,
    build_openai_responses_tools,
    load_openai_api_key_from_environment,
    send_openai_responses_http_request,
    serialize_openai_responses_payload_dict_pretty,
)
from ai_office.providers.openai.responses_dict_payload import JsonValue
from ai_office.tools import (
    DEFAULT_TOOL_CATALOG,
    ToolCatalogError,
    ToolDefinition,
    resolve_tool_names,
)

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


def _build_workflow_step_preview(
    workflows: list[object],
    employees: list[object],
    workflow_id: str,
    step_index: int,
) -> tuple[object, _WorkflowStepPreview]:
    """Construct one exact step request through the existing public seams."""
    try:
        workflow = find_workflow_by_id(workflows, workflow_id)
        plan = build_execution_plan(workflow, employees)
        step_request = build_step_execution_request(plan, step_index, employees)
        selected_employee = find_employee_by_id(employees, step_request.employee_id)
        invocation_request = build_model_invocation_request(step_request)
        resolved_tools = resolve_tool_names(
            DEFAULT_TOOL_CATALOG, invocation_request.allowed_tools
        )
        fingerprint = build_model_invocation_execution_fingerprint(
            invocation_request, resolved_tools
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
    )


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


def _step_preview_json(
    operation: str, preview: _WorkflowStepPreview
) -> dict[str, object]:
    request = preview.step_request
    invocation = preview.invocation_request
    return {
        "allowed_tools": list(invocation.allowed_tools),
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


def _result_json(
    operation: str,
    mode: str,
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
) -> dict[str, object]:
    """Build safe result metadata without copying provider result contents."""
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
        }
    _workflow_cli_error("workflow result is incompatible")


def _has_execution_fields(
    approve_preparation: bool,
    approve_execution: bool,
    approved_by: str | None,
    approval_id: str | None,
    expected_step_id: str | None,
    expected_step_index: int | None,
    expected_employee_id: str | None,
    expected_request_fingerprint: str | None,
) -> bool:
    """Return whether any execution-only option was supplied by the caller."""
    return any(
        (
            approve_preparation,
            approve_execution,
            approved_by is not None,
            approval_id is not None,
            expected_step_id is not None,
            expected_step_index is not None,
            expected_employee_id is not None,
            expected_request_fingerprint is not None,
        )
    )


def _require_execution_options(
    approve_preparation: bool,
    approve_execution: bool,
    approved_by: str | None,
    approval_id: str | None,
    expected_step_id: str | None,
    expected_step_index: int | None,
    expected_employee_id: str | None,
    expected_request_fingerprint: str | None,
) -> None:
    """Reject incomplete explicit approval before credential or provider work."""
    if not approve_preparation or not approve_execution:
        _workflow_cli_error("both explicit approvals are required")
    if approved_by is None or approved_by == "":
        _workflow_cli_error("approved-by is required")
    if approval_id is None or approval_id == "":
        _workflow_cli_error("approval-id is required")
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
    approved_by: str,
    approval_id: str,
) -> ApprovedWorkflowBootstrapContext:
    """Create the exact fresh-start context only after preview binding passes."""
    try:
        api_key = load_openai_api_key_from_environment()
        preparation_approval = InitialStepPreparationApproval(
            True,
            preview.step_request.workflow_id,
            preview.step_request.step_id,
            preview.step_request.step_index,
            preview.step_request.employee_id,
        )
        execution_approval = approve_model_invocation_execution(
            preview.invocation_request,
            preview.resolved_tools,
            provider="openai",
            approved_by=approved_by,
            approval_id=approval_id,
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
    )


def _build_continuation_context(
    decision: WorkflowProgressionDecision,
    preview: _WorkflowStepPreview,
    approved_by: str,
    approval_id: str,
) -> ApprovedWorkflowContinuationContext:
    """Create one exact next-step context only after preview binding passes."""
    try:
        api_key = load_openai_api_key_from_environment()
        preparation_approval = NextStepPreparationApproval(
            True,
            decision.workflow_id,
            decision.current_step_id,
            decision.current_step_index,
            decision.next_step_id,
            decision.next_step_index,
            decision.next_employee_id,
        )
        execution_approval = approve_model_invocation_execution(
            preview.invocation_request,
            preview.resolved_tools,
            provider="openai",
            approved_by=approved_by,
            approval_id=approval_id,
        )
    except Exception:
        _workflow_cli_error("credential or approval configuration is invalid")
    return ApprovedWorkflowContinuationContext(
        preparation_approval=preparation_approval,
        employee=preview.employee,
        resolved_tools=preview.resolved_tools,
        api_key=api_key,
        execution_approval=execution_approval,
        transport=send_openai_responses_http_request,
    )


def _run_fresh_workflow(
    workflow: object,
    state_path: Path,
    events_path: Path,
    context: ApprovedWorkflowBootstrapContext,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Call the public Phase-210 composition with an exact empty tuple."""
    try:
        return route_approved_fresh_workflow_bounded(
            workflow,
            state_path,
            events_path,
            context,
            (),
        )
    except Exception:
        _workflow_cli_error("workflow execution failed")


def _read_persisted_continue_route(
    workflow: object,
    state_path: Path,
    events_path: Path,
) -> PersistedExecutionOutcome | WorkflowProgressionDecision:
    """Run the canonical read-only Phase-37 → Phase-38 preflight."""
    try:
        classified = classify_persisted_execution_outcome_reentry(
            workflow,
            state_path,
            events_path,
        )
        routed = route_persisted_execution_outcome_reentry(
            classified,
            workflow,
            state_path,
            events_path,
        )
    except Exception:
        _workflow_cli_error(
            "persisted workflow state requires recovery or investigation"
        )
    if type(routed) not in (PersistedExecutionOutcome, WorkflowProgressionDecision):
        _workflow_cli_error("persisted workflow route is incompatible")
    return routed


def _run_persisted_workflow(
    workflow: object,
    state_path: Path,
    events_path: Path,
    context: ApprovedWorkflowContinuationContext,
) -> WorkflowProgressionDecision | PersistedExecutionOutcome:
    """Call Phase-212 with exactly one built-in continuation context."""
    try:
        return route_persisted_terminal_workflow_bounded(
            workflow,
            state_path,
            events_path,
            (context,),
        )
    except Exception:
        _workflow_cli_error("workflow continuation failed")


@workflows_app.command("start")
def start_workflow(
    workflow_id: str,
    state_path: Path = typer.Option(..., "--state-path"),
    events_path: Path = typer.Option(..., "--events-path"),
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
    preview_only: bool = typer.Option(False, "--preview-only"),
    approve_preparation: bool = typer.Option(False, "--approve-preparation"),
    approve_execution: bool = typer.Option(False, "--approve-execution"),
    approved_by: str | None = typer.Option(None, "--approved-by"),
    approval_id: str | None = typer.Option(None, "--approval-id"),
    expected_step_id: str | None = typer.Option(None, "--expected-step-id"),
    expected_step_index: int | None = typer.Option(None, "--expected-step-index"),
    expected_employee_id: str | None = typer.Option(None, "--expected-employee-id"),
    expected_request_fingerprint: str | None = typer.Option(
        None, "--expected-request-fingerprint"
    ),
) -> None:
    """Preview or execute exactly one fresh workflow step."""
    if preview_only and _has_execution_fields(
        approve_preparation,
        approve_execution,
        approved_by,
        approval_id,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
    ):
        _workflow_cli_error("preview-only cannot include execution options")
    if not preview_only:
        _require_execution_options(
            approve_preparation,
            approve_execution,
            approved_by,
            approval_id,
            expected_step_id,
            expected_step_index,
            expected_employee_id,
            expected_request_fingerprint,
        )

    workflows, employees = _load_workflow_command_inputs(
        directory, employees_directory
    )
    workflow, preview = _build_workflow_step_preview(
        workflows, employees, workflow_id, 1
    )
    if preview_only:
        _emit_json(_step_preview_json("start", preview))
        return
    assert approved_by is not None and approval_id is not None
    if not _expected_preview_matches(
        preview,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
    ):
        _workflow_cli_error("expected preview does not match current step")
    context = _build_start_context(preview, approved_by, approval_id)
    result = _run_fresh_workflow(workflow.definition, state_path, events_path, context)
    _emit_json(_result_json("start", "execute", result))
    if type(result) is PersistedExecutionOutcome:
        raise typer.Exit(code=1)


@workflows_app.command("continue")
def continue_workflow(
    workflow_id: str,
    state_path: Path = typer.Option(..., "--state-path"),
    events_path: Path = typer.Option(..., "--events-path"),
    directory: Path = typer.Option(Path("workflows"), "--directory"),
    employees_directory: Path = typer.Option(
        Path("employees"), "--employees-directory"
    ),
    preview_only: bool = typer.Option(False, "--preview-only"),
    approve_preparation: bool = typer.Option(False, "--approve-preparation"),
    approve_execution: bool = typer.Option(False, "--approve-execution"),
    approved_by: str | None = typer.Option(None, "--approved-by"),
    approval_id: str | None = typer.Option(None, "--approval-id"),
    expected_step_id: str | None = typer.Option(None, "--expected-step-id"),
    expected_step_index: int | None = typer.Option(None, "--expected-step-index"),
    expected_employee_id: str | None = typer.Option(None, "--expected-employee-id"),
    expected_request_fingerprint: str | None = typer.Option(
        None, "--expected-request-fingerprint"
    ),
) -> None:
    """Preview or execute exactly one persisted next workflow step."""
    if preview_only and _has_execution_fields(
        approve_preparation,
        approve_execution,
        approved_by,
        approval_id,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
    ):
        _workflow_cli_error("preview-only cannot include execution options")

    workflows, employees = _load_workflow_command_inputs(
        directory, employees_directory
    )
    workflow = _select_workflow_or_exit(workflows, workflow_id)
    routed = _read_persisted_continue_route(
        workflow.definition, state_path, events_path
    )

    if type(routed) is PersistedExecutionOutcome:
        _emit_json(
            _result_json(
                "continue", "preview" if preview_only else "execute", routed
            )
        )
        if not preview_only:
            raise typer.Exit(code=1)
        return
    if routed.decision == "workflow_complete":
        _emit_json(
            _result_json(
                "continue", "preview" if preview_only else "execute", routed
            )
        )
        return
    if routed.decision != "prepare_next_step":
        _workflow_cli_error("persisted workflow requires recovery or investigation")

    assert routed.next_step_index is not None
    workflow, preview = _build_workflow_step_preview(
        workflows,
        employees,
        workflow_id,
        routed.next_step_index,
    )
    if preview_only:
        _emit_json(_step_preview_json("continue", preview))
        return

    _require_execution_options(
        approve_preparation,
        approve_execution,
        approved_by,
        approval_id,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
    )
    assert approved_by is not None and approval_id is not None
    if not _expected_preview_matches(
        preview,
        expected_step_id,
        expected_step_index,
        expected_employee_id,
        expected_request_fingerprint,
    ):
        _workflow_cli_error("expected preview does not match current step")
    context = _build_continuation_context(
        routed, preview, approved_by, approval_id
    )
    result = _run_persisted_workflow(
        workflow.definition, state_path, events_path, context
    )
    _emit_json(_result_json("continue", "execute", result))
    if type(result) is PersistedExecutionOutcome:
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
