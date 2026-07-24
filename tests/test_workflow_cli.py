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
