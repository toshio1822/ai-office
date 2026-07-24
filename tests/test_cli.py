"""Tests for the public command-line interface."""

from typer.testing import CliRunner

from ai_office.cli import app

runner = CliRunner()


def test_help_is_available() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "ai-office" in result.stdout
