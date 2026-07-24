"""Tests for workflow definition CLI commands."""

from pathlib import Path

import yaml
from typer.testing import CliRunner

from ai_office.cli import app

runner = CliRunner()


def write_valid_employee(directory: Path) -> None:
    (directory / "employee.yaml").write_text(
        """id: general-researcher
name: General Researcher
role: Organizes information.
instructions: Work on the assigned step.
model: codex
allowed_tools: []
""",
        encoding="utf-8",
    )


def write_valid_workflow(
    directory: Path, *, employee: str = "general-researcher"
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
                        "instructions": "Gather relevant information.",
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
