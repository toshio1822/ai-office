"""Tests for employee definition CLI commands."""

from pathlib import Path

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


def test_employees_list_displays_validated_definitions(tmp_path: Path) -> None:
    write_valid_employee(tmp_path)

    result = runner.invoke(app, ["employees", "list", "--directory", str(tmp_path)])

    assert result.exit_code == 0
    assert "general-researcher" in result.stdout
    assert "General Researcher" in result.stdout
    assert "codex" in result.stdout


def test_employees_list_reports_empty_directory(tmp_path: Path) -> None:
    result = runner.invoke(app, ["employees", "list", "--directory", str(tmp_path)])

    assert result.exit_code == 0
    assert "No employee definitions found." in result.stdout


def test_employees_list_does_not_partially_display_invalid_definitions(
    tmp_path: Path,
) -> None:
    write_valid_employee(tmp_path)
    (tmp_path / "invalid.yml").write_text("id: [", encoding="utf-8")

    result = runner.invoke(app, ["employees", "list", "--directory", str(tmp_path)])

    assert result.exit_code != 0
    assert "invalid.yml" in result.stderr
    assert "general-researcher" not in result.stdout


def test_employees_validate_reports_count(tmp_path: Path) -> None:
    write_valid_employee(tmp_path)

    result = runner.invoke(app, ["employees", "validate", "--directory", str(tmp_path)])

    assert result.exit_code == 0
    assert "Validated 1 employee definition(s)." in result.stdout


def test_employees_validate_reports_user_facing_error(tmp_path: Path) -> None:
    (tmp_path / "invalid.yaml").write_text("id: [", encoding="utf-8")

    result = runner.invoke(app, ["employees", "validate", "--directory", str(tmp_path)])

    assert result.exit_code != 0
    assert "Error:" in result.stderr
    assert "invalid.yaml" in result.stderr
