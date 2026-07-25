"""Tests for workflow definition CLI commands."""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.invocation import ModelInvocationRequest
from ai_office.tools import ToolCatalog, ToolDefinition, ToolParameterDefinition

runner = CliRunner()


def write_valid_employee(
    directory: Path,
    *,
    role: str = "Organizes information.",
    instructions: str = "Work on the assigned step.",
    allowed_tools: list[str] | None = None,
) -> None:
    allowed_tools = [] if allowed_tools is None else allowed_tools
    (directory / "employee.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "general-researcher",
                "name": "General Researcher",
                "role": role,
                "instructions": instructions,
                "model": "codex",
                "allowed_tools": allowed_tools,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def write_valid_workflow(
    directory: Path,
    *,
    employee: str = "general-researcher",
    research_instructions: str = "Gather relevant information.",
) -> None:
    (directory / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "research-and-summarize",
                "name": "Research and Summarize",
                "description": "Researches a topic and summarizes it.",
                "steps": [
                    {
                        "id": "research",
                        "name": "Research",
                        "employee": employee,
                        "instructions": research_instructions,
                    },
                    {
                        "id": "summarize",
                        "name": "Summarize",
                        "employee": employee,
                        "instructions": "Summarize the information.",
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_workflows_list_displays_validated_definitions(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "list",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "research-and-summarize" in result.stdout
    assert "Research and Summarize" in result.stdout
    assert "2" in result.stdout


def test_workflows_list_reports_empty_directory(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()

    result = runner.invoke(
        app,
        [
            "workflows",
            "list",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "No workflow definitions found." in result.stdout


def test_workflows_list_does_not_partially_display_invalid_definitions(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    (workflows_directory / "invalid.yml").write_text("id: [", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflows",
            "list",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "invalid.yml" in result.stderr
    assert "research-and-summarize" not in result.stdout


def test_workflows_validate_reports_workflow_and_step_counts(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "validate",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "Validated 1 workflow definition(s) with 2 step(s)." in result.stdout


def test_workflows_validate_reports_missing_employee_to_stderr(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory, employee="missing-employee")

    result = runner.invoke(
        app,
        [
            "workflows",
            "validate",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert "missing-employee" in result.stderr


def test_workflows_plan_displays_execution_plan(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "plan",
            "research-and-summarize",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Workflow: research-and-summarize\n"
        "Name: Research and Summarize\n"
        "Steps: 2\n"
        "\n"
        "1. research\n"
        "   Name: Research\n"
        "   Employee: general-researcher\n"
        "   Instructions:\n"
        "     Gather relevant information.\n"
        "\n"
        "2. summarize\n"
        "   Name: Summarize\n"
        "   Employee: general-researcher\n"
        "   Instructions:\n"
        "     Summarize the information.\n"
    )


def test_workflows_plan_reports_missing_workflow_to_stderr(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "plan",
            "missing-workflow",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert "missing-workflow" in result.stderr
    assert result.stdout == ""


def test_workflows_plan_reports_invalid_workflow_id_to_stderr(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "plan",
            "Invalid_ID",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert "invalid workflow id" in result.stderr
    assert result.stdout == ""


def test_workflows_plan_displays_multiline_instructions_with_equal_indentation(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(
        workflows_directory,
        research_instructions=(
            "Gather relevant information.\nSeparate facts from assumptions."
        ),
    )
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "plan",
            "research-and-summarize",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "     Gather relevant information.\n" in result.stdout
    assert "     Separate facts from assumptions.\n" in result.stdout


def test_workflows_plan_reports_empty_workflow_directory_to_stderr(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "plan",
            "research-and-summarize",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert result.stdout == ""


def test_workflows_plan_reports_invalid_employee_definition_to_stderr(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    (employees_directory / "invalid-employee.yaml").write_text(
        "id: [", encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "plan",
            "research-and-summarize",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert "invalid-employee.yaml" in result.stderr
    assert result.stdout == ""


def test_workflows_help_lists_plan_command() -> None:
    result = runner.invoke(app, ["workflows", "--help"])

    assert result.exit_code == 0
    assert "plan" in result.stdout


def test_workflows_plan_validates_all_workflows_before_selection(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    (workflows_directory / "invalid.yml").write_text("id: [", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflows",
            "plan",
            "research-and-summarize",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "invalid.yml" in result.stderr
    assert result.stdout == ""


def test_workflows_plan_validates_unselected_employee_references(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    (workflows_directory / "unrelated.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "unrelated",
                "name": "Unrelated",
                "description": "Uses an undefined employee.",
                "steps": [
                    {
                        "id": "unrelated-step",
                        "name": "Unrelated Step",
                        "employee": "missing-employee",
                        "instructions": "Do unrelated work.",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "plan",
            "research-and-summarize",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "missing-employee" in result.stderr
    assert result.stdout == ""


def test_workflows_request_displays_selected_step_and_employee_values(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(
        employees_directory,
        role="Organizes information.\nSeparates facts.",
        instructions="Work on the assigned step.\nKeep facts separate.",
        allowed_tools=["search", "read"],
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "request",
            "research-and-summarize",
            "2",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "Step: 2. summarize" in result.stdout
    assert "  Organizes information.\n  Separates facts." in result.stdout
    assert "Allowed tools: search, read" in result.stdout
    assert "  Work on the assigned step.\n  Keep facts separate." in result.stdout
    assert "  Summarize the information." in result.stdout


def test_workflows_request_displays_none_for_empty_allowed_tools(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "request",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "Step: 1. research" in result.stdout
    assert "Allowed tools: none" in result.stdout


@pytest.mark.parametrize("workflow_id", ["missing-workflow", "Invalid_ID"])
def test_workflows_request_reports_invalid_workflow_without_stdout(
    tmp_path: Path, workflow_id: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "request",
            workflow_id,
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("step_index", ["0", "-1", "3"])
def test_workflows_request_reports_invalid_step_without_stdout(
    tmp_path: Path, step_index: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "request",
            "research-and-summarize",
            step_index,
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert result.stdout == ""


def test_workflows_request_validates_unselected_definitions(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    (workflows_directory / "invalid.yml").write_text("id: [", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflows",
            "request",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "invalid.yml" in result.stderr
    assert result.stdout == ""


def test_workflows_request_reports_invalid_employee_definition_without_stdout(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    (employees_directory / "invalid-employee.yaml").write_text(
        "id: [", encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "request",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "invalid-employee.yaml" in result.stderr
    assert result.stdout == ""


def test_workflows_request_validates_unselected_employee_references(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    (workflows_directory / "unrelated.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "unrelated",
                "name": "Unrelated",
                "description": "Uses an undefined employee.",
                "steps": [
                    {
                        "id": "unrelated-step",
                        "name": "Unrelated Step",
                        "employee": "missing-employee",
                        "instructions": "Do unrelated work.",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "request",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "missing-employee" in result.stderr
    assert result.stdout == ""


def test_workflows_help_lists_request_command() -> None:
    result = runner.invoke(app, ["workflows", "--help"])

    assert result.exit_code == 0
    assert "request" in result.stdout


def test_workflows_invocation_displays_provider_independent_values(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(
        workflows_directory,
        research_instructions="Gather relevant information.\n\nKeep facts separate.",
    )
    write_valid_employee(
        employees_directory,
        role="Organizes information.",
        instructions="Work on the assigned step.\n\nFollow the evidence.",
        allowed_tools=["search", "read"],
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "invocation",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Model: codex\n"
        "Allowed tools:\n"
        "  search\n"
        "  read\n"
        "System instructions:\n"
        "  Work on the assigned step.\n"
        "  \n"
        "  Follow the evidence.\n"
        "Task instructions:\n"
        "  Gather relevant information.\n"
        "  \n"
        "  Keep facts separate.\n"
    )
    assert "research-and-summarize" not in result.stdout
    assert "general-researcher" not in result.stdout
    assert "Organizes information." not in result.stdout


def test_workflows_invocation_displays_none_for_empty_allowed_tools(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "invocation",
            "research-and-summarize",
            "2",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "Allowed tools:\n  none\n" in result.stdout
    assert "Task instructions:\n  Summarize the information.\n" in result.stdout


@pytest.mark.parametrize("workflow_id", ["missing-workflow", "Invalid_ID"])
def test_workflows_invocation_reports_invalid_workflow_without_stdout(
    tmp_path: Path, workflow_id: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "invocation",
            workflow_id,
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("step_index", ["0", "-1", "3"])
def test_workflows_invocation_reports_invalid_step_without_stdout(
    tmp_path: Path, step_index: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "invocation",
            "research-and-summarize",
            step_index,
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert result.stdout == ""


def test_workflows_invocation_rejects_unknown_options_without_stdout(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "invocation",
            "research-and-summarize",
            "1",
            "--unknown-option",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "unexpected extra argument" in result.stderr.lower()
    assert result.stdout == ""


def test_workflows_invocation_validates_unselected_definitions(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    (employees_directory / "invalid-employee.yaml").write_text(
        "id: [", encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "invocation",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "invalid-employee.yaml" in result.stderr
    assert result.stdout == ""


def test_workflows_invocation_validates_unselected_employee_references(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    (workflows_directory / "unrelated.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "unrelated",
                "name": "Unrelated",
                "description": "Uses an undefined employee.",
                "steps": [
                    {
                        "id": "unrelated-step",
                        "name": "Unrelated Step",
                        "employee": "missing-employee",
                        "instructions": "Do unrelated work.",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "invocation",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "missing-employee" in result.stderr
    assert result.stdout == ""


def test_workflows_help_lists_invocation_command() -> None:
    result = runner.invoke(app, ["workflows", "--help"])

    assert result.exit_code == 0
    assert "invocation" in result.stdout


def test_workflows_provider_request_displays_openai_values_without_combining(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(
        workflows_directory,
        research_instructions="  Write this.\n\nDo not trim.  \n",
    )
    write_valid_employee(
        employees_directory,
        instructions="\n  You are a writer.\n\nKeep  spaces.\t\n",
        allowed_tools=["web_search", "FileRead"],
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-request",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Provider: openai\n"
        "Model: codex\n"
        "Allowed tool names:\n"
        "  web_search\n"
        "  FileRead\n"
        "Instructions:\n"
        "  \n"
        "    You are a writer.\n"
        "  \n"
        "  Keep  spaces.\t\n"
        "  \n"
        "Input:\n"
        "    Write this.\n"
        "  \n"
        "  Do not trim.  \n"
        "  \n"
    )
    assert "System instructions:" not in result.stdout
    assert "Task instructions:" not in result.stdout


def test_workflows_provider_request_displays_none_for_empty_tools(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-request",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "Allowed tool names:\n  none\n" in result.stdout


def test_workflows_provider_request_reports_unsupported_provider_without_stdout(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-request",
            "anthropic",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == "Error: unsupported provider: anthropic\n"


@pytest.mark.parametrize("workflow_id", ["missing-workflow", "Invalid_ID"])
def test_workflows_provider_request_reports_invalid_workflow_without_stdout(
    tmp_path: Path, workflow_id: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-request",
            "openai",
            workflow_id,
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("step_index", ["0", "-1", "3", "not-an-index"])
def test_workflows_provider_request_reports_invalid_step_without_stdout(
    tmp_path: Path, step_index: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-request",
            "openai",
            "research-and-summarize",
            step_index,
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert result.stdout == ""


def test_workflows_provider_request_validates_unselected_definitions(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    (workflows_directory / "invalid.yml").write_text("id: [", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-request",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "invalid.yml" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "invalid_definition",
    ["employee", "employee-reference"],
)
def test_workflows_provider_request_reports_definition_errors_without_stdout(
    tmp_path: Path, invalid_definition: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    if invalid_definition == "employee":
        (employees_directory / "invalid.yml").write_text("id: [", encoding="utf-8")
        expected_error = "invalid.yml"
    else:
        (workflows_directory / "unrelated.yml").write_text(
            yaml.safe_dump(
                {
                    "id": "unrelated",
                    "name": "Unrelated",
                    "description": "Uses an undefined employee.",
                    "steps": [
                        {
                            "id": "unrelated-step",
                            "name": "Unrelated Step",
                            "employee": "missing-employee",
                            "instructions": "Do unrelated work.",
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        expected_error = "missing-employee"

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-request",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert expected_error in result.stderr
    assert result.stdout == ""


def test_workflows_resolve_tools_displays_resolved_catalog_definitions(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["web_search", "FileRead"])

    result = runner.invoke(
        app,
        [
            "workflows",
            "resolve-tools",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Resolved tools:\n"
        "  web_search\n"
        "    Description: Search the web for relevant information.\n"
        "    Parameters:\n"
        "      query\n"
        "        Type: string\n"
        "        Required: yes\n"
        "        Description: The search query.\n"
        "  FileRead\n"
        "    Description: Read the contents of a file.\n"
        "    Parameters:\n"
        "      path\n"
        "        Type: string\n"
        "        Required: yes\n"
        "        Description: The path of the file to read.\n"
    )
    assert "Provider:" not in result.stdout
    assert '"tools"' not in result.stdout


def test_workflows_resolve_tools_displays_none_for_empty_tools(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "resolve-tools",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "Resolved tools:\n  none\n"


def test_workflows_resolve_tools_displays_none_for_empty_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["catalog_only"])
    monkeypatch.setattr(
        cli_module,
        "DEFAULT_TOOL_CATALOG",
        ToolCatalog(
            tools=(
                ToolDefinition(
                    name="catalog_only",
                    description="A static catalog entry.",
                    parameters=(),
                ),
            )
        ),
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "resolve-tools",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "Parameters:\n      none\n" in result.stdout


@pytest.mark.parametrize("step_index", ["0", "3", "not-an-index"])
def test_workflows_resolve_tools_reports_invalid_step_without_stdout(
    tmp_path: Path, step_index: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "resolve-tools",
            "research-and-summarize",
            step_index,
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert result.stdout == ""


def test_workflows_resolve_tools_reports_unknown_tool_without_stdout(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["UnknownTool"])

    result = runner.invoke(
        app,
        [
            "workflows",
            "resolve-tools",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == "Error: Tool not found: UnknownTool\n"


def test_workflows_provider_tools_displays_openai_function_tools(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["web_search", "FileRead"])

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-tools",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Provider: openai\n"
        "Tools:\n"
        "  Type: function\n"
        "  Name: web_search\n"
        "  Description: Search the web for relevant information.\n"
        "  Strict: no\n"
        "  Parameters:\n"
        "    Type: object\n"
        "    Additional properties: no\n"
        "    Properties:\n"
        "      query\n"
        "        Type: string\n"
        "        Description: The search query.\n"
        "    Required:\n"
        "      query\n"
        "  Type: function\n"
        "  Name: FileRead\n"
        "  Description: Read the contents of a file.\n"
        "  Strict: no\n"
        "  Parameters:\n"
        "    Type: object\n"
        "    Additional properties: no\n"
        "    Properties:\n"
        "      path\n"
        "        Type: string\n"
        "        Description: The path of the file to read.\n"
        "    Required:\n"
        "      path\n"
    )
    assert "{" not in result.stdout


def test_workflows_provider_tools_displays_none_for_empty_tools(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-tools",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "Provider: openai\nTools:\n  none\n"


def test_workflows_provider_tools_displays_none_for_empty_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["catalog_only"])
    monkeypatch.setattr(
        cli_module,
        "DEFAULT_TOOL_CATALOG",
        ToolCatalog(
            tools=(
                ToolDefinition(
                    name="catalog_only",
                    description="A static catalog entry.",
                    parameters=(),
                ),
            )
        ),
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-tools",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "Properties:\n      none\n" in result.stdout
    assert "Required:\n      none\n" in result.stdout


def test_workflows_provider_tools_preserves_property_and_required_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["catalog_only"])
    monkeypatch.setattr(
        cli_module,
        "DEFAULT_TOOL_CATALOG",
        ToolCatalog(
            tools=(
                ToolDefinition(
                    name="catalog_only",
                    description="A static catalog entry.",
                    parameters=(
                        ToolParameterDefinition("first", "First.", "string", True),
                        ToolParameterDefinition(
                            "optional", "Optional.", "integer", False
                        ),
                        ToolParameterDefinition("last", "Last.", "boolean", True),
                    ),
                ),
            )
        ),
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-tools",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.index("      first\n") < result.stdout.index(
        "      optional\n"
    ) < result.stdout.index("      last\n")
    required_section = result.stdout.split("    Required:\n", maxsplit=1)[1]
    assert required_section == "      first\n      last\n"


@pytest.mark.parametrize("provider", ["anthropic", "OpenAI", " openai", "openai "])
def test_workflows_provider_tools_rejects_unsupported_provider_without_stdout(
    tmp_path: Path, provider: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-tools",
            provider,
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == f"Error: unsupported provider: {provider}\n"


def test_workflows_provider_tools_reports_unknown_tool_without_stdout(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["UnknownTool"])

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-tools",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == "Error: Tool not found: UnknownTool\n"


@pytest.mark.parametrize("step_index", ["0", "3", "not-an-index"])
def test_workflows_provider_tools_reports_invalid_step_without_stdout(
    tmp_path: Path, step_index: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-tools",
            "openai",
            "research-and-summarize",
            step_index,
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr.startswith("Error:")


def test_workflows_provider_tools_validates_unselected_definitions(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    (workflows_directory / "invalid.yml").write_text("id: [", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-tools",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "invalid.yml" in result.stderr


def test_workflows_provider_payload_displays_static_payload(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(
        workflows_directory,
        research_instructions="  Input line\n\n終端  \n",
    )
    write_valid_employee(
        employees_directory,
        instructions="\n  指示  ✨\n",
        allowed_tools=["web_search", "FileRead"],
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-payload",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "Provider: openai\n"
        "Payload:\n"
        "  Model: codex\n"
        "  Instructions:\n"
        "    \n"
        "      指示  ✨\n"
        "    \n"
        "  Input:\n"
        "      Input line\n"
        "    \n"
        "    終端  \n"
        "    \n"
        "  Tools:\n"
        "    Type: function\n"
        "    Name: web_search\n"
        "    Description: Search the web for relevant information.\n"
        "    Strict: no\n"
        "    Parameters:\n"
        "      Type: object\n"
        "      Additional properties: no\n"
        "      Properties:\n"
        "        query\n"
        "          Type: string\n"
        "          Description: The search query.\n"
        "      Required:\n"
        "        query\n"
        "    Type: function\n"
        "    Name: FileRead\n"
        "    Description: Read the contents of a file.\n"
        "    Strict: no\n"
        "    Parameters:\n"
        "      Type: object\n"
        "      Additional properties: no\n"
        "      Properties:\n"
        "        path\n"
        "          Type: string\n"
        "          Description: The path of the file to read.\n"
        "      Required:\n"
        "        path\n"
    )
    assert "{" not in result.stdout


def test_workflows_provider_payload_displays_empty_values_and_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    monkeypatch.setattr(
        cli_module,
        "build_model_invocation_request",
        lambda _request: ModelInvocationRequest(
            model="codex",
            system_instructions="",
            task_instructions="",
            allowed_tools=(),
        ),
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-payload",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "  Instructions:\n    <empty>\n" in result.stdout
    assert "  Input:\n    <empty>\n" in result.stdout
    assert result.stdout.endswith("  Tools:\n    none\n")


@pytest.mark.parametrize("provider", ["anthropic", "OpenAI", " openai", "openai "])
def test_workflows_provider_payload_rejects_unsupported_provider_without_stdout(
    tmp_path: Path, provider: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-payload",
            provider,
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == f"Error: unsupported provider: {provider}\n"


def test_workflows_provider_payload_reports_unknown_tool_without_stdout(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["UnknownTool"])

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-payload",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == "Error: Tool not found: UnknownTool\n"


def test_workflows_provider_dict_payload_displays_deterministic_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(
        workflows_directory,
        research_instructions="  Input line\n終端  ",
    )
    write_valid_employee(
        employees_directory,
        instructions="  Instructions ✨\nsecond line",
        allowed_tools=["web_search", "FileRead"],
    )
    monkeypatch.setattr(
        cli_module,
        "build_model_invocation_request",
        lambda _request: ModelInvocationRequest(
            model="codex",
            system_instructions="  Instructions ✨\nsecond line",
            task_instructions="  Input line\n終端  ",
            allowed_tools=("web_search", "FileRead", "web_search"),
        ),
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-dict-payload",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.startswith(
        "Provider: openai\n"
        "Dictionary payload:\n"
        "  model: codex\n"
        "  instructions:\n"
        "      Instructions ✨\n"
        "    second line\n"
        "  input:\n"
        "      Input line\n"
        "    終端  \n"
        "  tools:\n"
        "    - type: function\n"
        "      name: web_search\n"
    )
    assert result.stdout.count("      name: web_search\n") == 2
    assert result.stdout.index("      name: web_search\n") < result.stdout.index(
        "      name: FileRead\n"
    )
    assert "        additionalProperties: false\n" in result.stdout
    assert "      strict: false\n" in result.stdout
    assert "{'" not in result.stdout
    assert '"model"' not in result.stdout


def test_workflows_provider_dict_payload_displays_empty_values_and_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)
    monkeypatch.setattr(
        cli_module,
        "build_model_invocation_request",
        lambda _request: ModelInvocationRequest("codex", "", "", ()),
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-dict-payload",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout.endswith(
        "  instructions:\n"
        "    <empty>\n"
        "  input:\n"
        "    <empty>\n"
        "  tools: []\n"
    )


def test_workflows_provider_dict_payload_displays_empty_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["empty_parameters"])
    monkeypatch.setattr(
        cli_module,
        "DEFAULT_TOOL_CATALOG",
        ToolCatalog(
            tools=(
                ToolDefinition(
                    name="empty_parameters",
                    description="No parameters.",
                    parameters=(),
                ),
            )
        ),
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-dict-payload",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code == 0
    assert "        properties: {}\n" in result.stdout
    assert "        required: []\n" in result.stdout
    assert "        additionalProperties: false\n" in result.stdout


@pytest.mark.parametrize("provider", ["anthropic", "OpenAI", " openai", "openai "])
def test_workflows_provider_dict_payload_rejects_unsupported_provider_without_stdout(
    tmp_path: Path, provider: str
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory)

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-dict-payload",
            provider,
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == f"Error: unsupported provider: {provider}\n"


def test_workflows_provider_dict_payload_reports_unknown_tool_without_stdout(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(workflows_directory)
    write_valid_employee(employees_directory, allowed_tools=["UnknownTool"])

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-dict-payload",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == "Error: Tool not found: UnknownTool\n"
