"""Command-line interface for AI Office."""

from pathlib import Path

import typer

from ai_office.definitions.employee import EmployeeLoadError, load_employees
from ai_office.definitions.workflow import (
    WorkflowLoadError,
    load_workflows,
    validate_workflow_employee_references,
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


def _load_workflows_or_exit(
    directory: Path, employees_directory: Path
):
    try:
        workflows = load_workflows(directory)
        employees = load_employees(employees_directory)
        validate_workflow_employee_references(workflows, employees)
        return workflows
    except (EmployeeLoadError, WorkflowLoadError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from None


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
