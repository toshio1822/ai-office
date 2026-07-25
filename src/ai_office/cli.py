"""Command-line interface for AI Office."""

from pathlib import Path

import typer

from ai_office.definitions.employee import EmployeeLoadError, load_employees
from ai_office.definitions.workflow import (
    WorkflowLoadError,
    load_workflows,
    validate_workflow_employee_references,
)
from ai_office.invocation import (
    ModelInvocationRequest,
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
)
from ai_office.providers.openai import (
    OpenAIResponsesFunctionTool,
    OpenAIResponsesPayload,
    OpenAIResponsesRequest,
    build_openai_responses_payload_from_invocation,
    build_openai_responses_request,
    build_openai_responses_tools,
)
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


def _display_payload_text(value: str) -> None:
    """Display a payload string while preserving every newline line."""
    if value == "":
        typer.echo("    <empty>")
        return
    for line in value.split("\n"):
        typer.echo(f"    {line}")


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
