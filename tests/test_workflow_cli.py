"""Tests for workflow definition CLI commands."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
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


def write_three_step_workflow(directory: Path) -> None:
    """Add one future step while preserving the standard test workflow."""
    write_valid_workflow(directory)
    path = directory / "workflow.yaml"
    definition = yaml.safe_load(path.read_text(encoding="utf-8"))
    definition["steps"].append(
        {
            "id": "review",
            "name": "Review",
            "employee": "general-researcher",
            "instructions": "Review the information.",
        }
    )
    path.write_text(
        yaml.safe_dump(definition, sort_keys=False),
        encoding="utf-8",
    )


def write_single_step_workflow(directory: Path) -> None:
    """Write a one-step workflow for terminal continuation coverage."""
    (directory / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "single-workflow",
                "name": "Single Workflow",
                "description": "One deterministic step.",
                "steps": [
                    {
                        "id": "only-step",
                        "name": "Only Step",
                        "employee": "general-researcher",
                        "instructions": "Complete the only step.",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def workflow_command_paths(tmp_path: Path) -> dict[str, Path]:
    """Create explicit definition and persistence paths for CLI tests."""
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    return {
        "workflows": workflows_directory,
        "employees": employees_directory,
        "state": tmp_path / "state.json",
        "events": tmp_path / "events.jsonl",
    }


def workflow_command_args(
    operation: str, workflow_id: str, paths: dict[str, Path]
) -> list[str]:
    """Return the common explicit path arguments for a new workflow command."""
    return [
        "workflows",
        operation,
        workflow_id,
        "--state-path",
        str(paths["state"]),
        "--events-path",
        str(paths["events"]),
        "--directory",
        str(paths["workflows"]),
        "--employees-directory",
        str(paths["employees"]),
    ]


def synthetic_transport(
    calls: list[object],
    *,
    status_code: int = 200,
    body: bytes | None = None,
    error: Exception | None = None,
):
    """Build a deterministic provider seam; this helper never opens a socket."""
    response_body = body
    if response_body is None and status_code == 200:
        response_body = (
            b'{"id":"synthetic-response","object":"response",'
            b'"status":"completed","output":[{"type":"message",'
            b'"content":[{"type":"output_text","text":"synthetic ok"}]}]}'
        )
    if response_body is None:
        response_body = (
            b'{"error":{"message":"synthetic provider failure",'
            b'"type":"synthetic_error","param":null,"code":null}}'
        )

    def send(request: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(request)
        if error is not None:
            raise error
        return OpenAIResponsesRawHttpResponse(
            status_code,
            "synthetic",
            (("x-request-id", "synthetic-request"),),
            response_body,
        )

    return send


def patch_cli_execution_seams(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[object],
    key_calls: list[int],
    *,
    status_code: int = 200,
    body: bytes | None = None,
    error: Exception | None = None,
) -> None:
    """Patch both CLI-visible paid seams with deterministic test doubles."""
    def load_key() -> OpenAIApiKey:
        key_calls.append(1)
        return OpenAIApiKey(value=SecretStr("synthetic-api-key"))

    monkeypatch.setattr(cli_module, "load_openai_api_key_from_environment", load_key)
    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        synthetic_transport(
            calls,
            status_code=status_code,
            body=body,
            error=error,
        ),
    )


def preview_command(
    operation: str, workflow_id: str, paths: dict[str, Path]
) -> tuple[object, dict[str, object]]:
    """Run a preview command and parse its sole JSON line."""
    result = runner.invoke(
        app,
        workflow_command_args(operation, workflow_id, paths) + ["--preview-only"],
    )
    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.endswith("\n")
    assert result.stdout.count("\n") == 1
    return result, json.loads(result.stdout)


def execution_options(preview: dict[str, object]) -> list[str]:
    """Return caller-supplied approval binding copied exactly from a preview."""
    return [
        "--approve-preparation",
        "--approve-execution",
        "--approved-by",
        "synthetic-operator",
        "--approval-id",
        "synthetic-approval",
        "--expected-step-id",
        str(preview["step_id"]),
        "--expected-step-index",
        str(preview["step_index"]),
        "--expected-employee-id",
        str(preview["employee_id"]),
        "--expected-request-fingerprint",
        str(preview["request_fingerprint"]),
    ]


def invoke_execution(
    operation: str,
    workflow_id: str,
    paths: dict[str, Path],
    preview: dict[str, object],
) -> object:
    """Invoke a command with the exact values returned by its preview."""
    return runner.invoke(
        app,
        workflow_command_args(operation, workflow_id, paths)
        + execution_options(preview),
    )


def write_succeeded_prefix(paths: dict[str, Path], current: int) -> None:
    """Write a strict synthetic succeeded history through the requested step."""
    step_ids = ("research", "summarize", "review")
    completed = step_ids[:current]
    state = WorkflowExecutionState(
        workflow_id="research-and-summarize",
        status="succeeded",
        current_step_id=step_ids[current - 1],
        current_step_index=current,
        current_employee_id="general-researcher",
        completed_step_ids=completed,
        last_failure_category=None,
    )
    events = "".join(
        serialize_runtime_step_event_jsonl(
            RuntimeStepEvent(
                event_type="step_succeeded",
                workflow_id="research-and-summarize",
                step_id=step_ids[index - 1],
                step_index=index,
                employee_id="general-researcher",
                previous_status="running",
                next_status="succeeded",
                provider="openai",
                failure_category=None,
                response_id=f"synthetic-response-{index}",
                request_id=f"synthetic-request-{index}",
                output_text="synthetic output",
                message=None,
            )
        )
        for index in range(1, current + 1)
    )
    paths["state"].write_text(
        serialize_workflow_execution_state_json(state),
        encoding="utf-8",
    )
    paths["events"].write_text(events, encoding="utf-8")


def replace_last_output(paths: dict[str, Path], output_text: str) -> None:
    """Replace one synthetic predecessor output through the event contract."""
    records = [
        json.loads(line)
        for line in paths["events"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records[-1]["output_text"] = output_text
    paths["events"].write_text(
        "".join(
            serialize_runtime_step_event_jsonl(
                RuntimeStepEvent(**record)  # type: ignore[arg-type]
            )
            for record in records
        ),
        encoding="utf-8",
    )


def write_nonterminal_history(paths: dict[str, Path], status: str) -> None:
    """Write a ready/running history that Phase 37 must reject without replay."""
    state = WorkflowExecutionState(
        workflow_id="research-and-summarize",
        status=status,  # type: ignore[arg-type]
        current_step_id="research",
        current_step_index=1,
        current_employee_id="general-researcher",
        completed_step_ids=(),
        last_failure_category=None,
    )
    paths["state"].write_text(
        serialize_workflow_execution_state_json(state),
        encoding="utf-8",
    )
    paths["events"].write_text("", encoding="utf-8")


def write_failed_history(paths: dict[str, Path]) -> None:
    """Write a strict synthetic failed terminal history for step one."""
    state = WorkflowExecutionState(
        workflow_id="research-and-summarize",
        status="failed",
        current_step_id="research",
        current_step_index=1,
        current_employee_id="general-researcher",
        completed_step_ids=(),
        last_failure_category="api_error",
    )
    event = RuntimeStepEvent(
        event_type="step_failed",
        workflow_id="research-and-summarize",
        step_id="research",
        step_index=1,
        employee_id="general-researcher",
        previous_status="running",
        next_status="failed",
        provider="openai",
        failure_category="api_error",
        response_id=None,
        request_id="synthetic-request",
        output_text=None,
        message="synthetic failure",
    )
    paths["state"].write_text(
        serialize_workflow_execution_state_json(state),
        encoding="utf-8",
    )
    paths["events"].write_text(
        serialize_runtime_step_event_jsonl(event),
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


@pytest.mark.parametrize(
    "provider", ["anthropic", "OpenAI", "OPENAI", " openai", "openai "]
)
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


def test_workflows_provider_json_displays_parseable_pretty_json(tmp_path: Path) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(
        workflows_directory, research_instructions="Input\nsecond line"
    )
    write_valid_employee(
        employees_directory,
        instructions="日本語 ✨",
        allowed_tools=["web_search"],
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-json",
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
    prefix = "Provider: openai\nJSON payload:\n"
    assert result.stdout.startswith(prefix)
    json_text = result.stdout.removeprefix(prefix)
    payload = json.loads(json_text)
    assert list(payload) == ["model", "instructions", "input", "tools"]
    assert payload["instructions"] == "日本語 ✨"
    assert payload["input"] == "Input\nsecond line"
    assert payload["tools"][0]["strict"] is False
    assert "  \"model\": \"codex\"" in json_text
    assert "日本語 ✨" in json_text


def test_workflows_provider_json_preserves_empty_values(
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
            "provider-json",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    payload = json.loads(
        result.stdout.removeprefix("Provider: openai\nJSON payload:\n")
    )
    assert result.exit_code == 0
    assert payload == {"model": "codex", "instructions": "", "input": "", "tools": []}


@pytest.mark.parametrize("provider", ["anthropic", "OpenAI", " openai", "openai "])
def test_workflows_provider_json_rejects_unsupported_provider_without_stdout(
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
            "provider-json",
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


def test_workflows_provider_json_reports_unknown_tool_without_stdout(
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
            "provider-json",
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


def test_workflows_provider_http_request_displays_compact_json_body(
    tmp_path: Path,
) -> None:
    workflows_directory = tmp_path / "workflows"
    employees_directory = tmp_path / "employees"
    workflows_directory.mkdir()
    employees_directory.mkdir()
    write_valid_workflow(
        workflows_directory, research_instructions="Input\nsecond line"
    )
    write_valid_employee(
        employees_directory,
        instructions="日本語 ✨",
        allowed_tools=["web_search"],
    )

    result = runner.invoke(
        app,
        [
            "workflows",
            "provider-http-request",
            "openai",
            "research-and-summarize",
            "1",
            "--directory",
            str(workflows_directory),
            "--employees-directory",
            str(employees_directory),
        ],
    )

    prefix = (
        "Provider: openai\n"
        "HTTP request template:\n"
        "Method: POST\n"
        "URL: https://api.openai.com/v1/responses\n"
        "Headers:\n"
        "  Content-Type: application/json\n"
        "Body:\n"
    )
    assert result.exit_code == 0
    assert result.stdout.startswith(prefix)
    body = result.stdout.removeprefix(prefix).removesuffix("\n")
    assert json.loads(body)["instructions"] == "日本語 ✨"
    assert json.loads(body)["input"] == "Input\nsecond line"
    assert "\n  \"model\"" not in body
    assert "Authorization" not in result.stdout
    assert "api_key" not in result.stdout


@pytest.mark.parametrize("provider", ["anthropic", "OpenAI", " openai", "openai "])
def test_workflows_provider_http_request_rejects_unsupported_provider_without_stdout(
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
            "provider-http-request",
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


def test_workflows_provider_http_request_reports_unknown_tool_without_stdout(
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
            "provider-http-request",
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


def test_workflows_start_preview_is_read_only_and_displays_exact_approval_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)

    help_result = runner.invoke(app, ["workflows", "--help"])
    result, preview = preview_command("start", "research-and-summarize", paths)

    assert help_result.exit_code == 0
    assert "start" in help_result.stdout
    assert "continue" in help_result.stdout
    assert result.stderr == ""
    assert preview == {
        "allowed_tools": [],
        "employee_id": "general-researcher",
        "mode": "preview",
        "model": "codex",
        "operation": "start",
        "request_fingerprint": preview["request_fingerprint"],
        "resolved_tools": [],
        "status": "step_ready",
        "step_id": "research",
        "step_index": 1,
        "system_instructions": "Work on the assigned step.",
        "task_instructions": "Gather relevant information.",
        "workflow_id": "research-and-summarize",
    }
    assert isinstance(preview["request_fingerprint"], str)
    assert len(preview["request_fingerprint"]) == 64
    assert not paths["state"].exists()
    assert not paths["events"].exists()
    assert calls == []
    assert key_calls == []


def test_workflows_start_rejects_ambiguous_or_incomplete_execution_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)
    common = workflow_command_args("start", "research-and-summarize", paths)

    cases = [
        common + ["--preview-only", "--approve-preparation"],
        common + ["--approve-preparation"],
        common
        + [
            "--approve-preparation",
            "--approve-execution",
            "--approved-by",
            "operator",
            "--approval-id",
            "approval",
        ],
    ]
    for arguments in cases:
        result = runner.invoke(app, arguments)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr.startswith("Error: ")

    assert calls == []
    assert key_calls == []
    assert not paths["state"].exists()
    assert not paths["events"].exists()


def test_workflows_start_rejects_mismatched_expected_preview_before_key_or_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)
    _, preview = preview_command("start", "research-and-summarize", paths)
    stale_preview = dict(preview)
    stale_preview["request_fingerprint"] = "0" * 64

    result = invoke_execution("start", "research-and-summarize", paths, stale_preview)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: expected preview does not match current step\n"
    assert calls == []
    assert key_calls == []
    assert not paths["state"].exists()
    assert not paths["events"].exists()


def test_workflows_start_success_executes_step1_once_with_empty_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)
    _, preview = preview_command("start", "research-and-summarize", paths)

    result = invoke_execution("start", "research-and-summarize", paths, preview)
    output = json.loads(result.stdout)

    assert result.exit_code == 0
    assert result.stderr == ""
    assert output["operation"] == "start"
    assert output["mode"] == "execute"
    assert output["status"] == "prepare_next_step"
    assert output["current_step_id"] == "research"
    assert output["current_step_index"] == 1
    assert output["next_step_id"] == "summarize"
    assert output["next_step_index"] == 2
    assert calls and len(calls) == 1
    assert key_calls == [1]
    assert load_workflow_execution_state(paths["state"]).completed_step_ids == (
        "research",
    )
    assert paths["events"].read_text(encoding="utf-8").count("step_succeeded") == 1


def test_workflows_start_failure_executes_once_and_stops_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls, status_code=500)
    _, preview = preview_command("start", "research-and-summarize", paths)

    result = invoke_execution("start", "research-and-summarize", paths, preview)
    output = json.loads(result.stdout)

    assert result.exit_code == 1
    assert output["status"] == "persisted_failure"
    assert output["failure_category"] == "api_error"
    assert output["next_step_id"] is None
    assert output["next_step_index"] is None
    assert len(calls) == 1
    assert key_calls == [1]
    assert load_workflow_execution_state(paths["state"]).status == "failed"
    assert paths["events"].read_text(encoding="utf-8").count("step_failed") == 1


def test_workflows_continue_preview_is_read_only_and_uses_persisted_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    write_succeeded_prefix(paths, 1)
    before = (paths["state"].read_bytes(), paths["events"].read_bytes())
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)

    result, preview = preview_command("continue", "research-and-summarize", paths)

    assert result.exit_code == 0
    assert preview["operation"] == "continue"
    assert preview["status"] == "step_ready"
    assert preview["step_id"] == "summarize"
    assert preview["step_index"] == 2
    assert preview["employee_id"] == "general-researcher"
    assert isinstance(preview["request_fingerprint"], str)
    assert (paths["state"].read_bytes(), paths["events"].read_bytes()) == before
    assert calls == []
    assert key_calls == []


def test_workflows_continue_terminal_routes_need_no_future_approval_or_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)

    write_succeeded_prefix(paths, 2)
    complete_before = (paths["state"].read_bytes(), paths["events"].read_bytes())
    complete = runner.invoke(
        app,
        workflow_command_args("continue", "research-and-summarize", paths),
    )
    complete_output = json.loads(complete.stdout)
    assert complete.exit_code == 0
    assert complete_output["status"] == "workflow_complete"
    assert complete_output["mode"] == "execute"
    assert (
        paths["state"].read_bytes(), paths["events"].read_bytes()
    ) == complete_before

    write_failed_history(paths)
    failed_before = (paths["state"].read_bytes(), paths["events"].read_bytes())
    failed = runner.invoke(
        app,
        workflow_command_args("continue", "research-and-summarize", paths),
    )
    failed_output = json.loads(failed.stdout)
    assert failed.exit_code == 1
    assert failed_output["status"] == "persisted_failure"
    assert failed_output["mode"] == "execute"
    assert (paths["state"].read_bytes(), paths["events"].read_bytes()) == failed_before
    assert calls == []
    assert key_calls == []


def test_workflows_continue_success_executes_exactly_one_next_step_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_three_step_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)
    write_succeeded_prefix(paths, 1)
    _, preview = preview_command("continue", "research-and-summarize", paths)

    result = invoke_execution("continue", "research-and-summarize", paths, preview)
    output = json.loads(result.stdout)

    assert result.exit_code == 0
    assert output["status"] == "prepare_next_step"
    assert output["current_step_id"] == "summarize"
    assert output["current_step_index"] == 2
    assert output["next_step_id"] == "review"
    assert output["next_step_index"] == 3
    assert len(calls) == 1
    assert key_calls == [1]
    assert load_workflow_execution_state(paths["state"]).completed_step_ids == (
        "research",
        "summarize",
    )


def test_workflows_continue_stale_expected_preview_is_rejected_before_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_three_step_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    write_succeeded_prefix(paths, 1)
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)
    _, old_preview = preview_command("continue", "research-and-summarize", paths)
    write_succeeded_prefix(paths, 2)
    before = (paths["state"].read_bytes(), paths["events"].read_bytes())

    result = invoke_execution(
        "continue", "research-and-summarize", paths, old_preview
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: expected preview does not match current step\n"
    assert (paths["state"].read_bytes(), paths["events"].read_bytes()) == before
    assert calls == []
    assert key_calls == []


def test_workflows_continue_preserves_phase212_revalidation_after_cli_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_three_step_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    write_succeeded_prefix(paths, 1)
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)
    _, preview = preview_command("continue", "research-and-summarize", paths)
    real_phase212 = cli_module.route_persisted_terminal_workflow_bounded

    def mutate_before_phase212(
        workflow: object,
        state_path: object,
        events_path: object,
        contexts: object,
    ) -> object:
        assert state_path == paths["state"]
        assert events_path == paths["events"]
        write_succeeded_prefix(paths, 2)
        return real_phase212(workflow, state_path, events_path, contexts)

    monkeypatch.setattr(
        cli_module,
        "route_persisted_terminal_workflow_bounded",
        mutate_before_phase212,
    )
    result = invoke_execution("continue", "research-and-summarize", paths, preview)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: workflow continuation failed\n"
    assert calls == []
    assert key_calls == [1]
    assert load_workflow_execution_state(paths["state"]).current_step_index == 2


def test_workflows_continue_rejects_ready_and_running_without_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)

    for status in ("ready", "running"):
        write_nonterminal_history(paths, status)
        before = (paths["state"].read_bytes(), paths["events"].read_bytes())
        result = runner.invoke(
            app,
            workflow_command_args("continue", "research-and-summarize", paths)
            + ["--preview-only"],
        )
        assert result.exit_code == 2
        assert result.stdout == ""
        assert result.stderr == (
            "Error: persisted workflow state requires recovery or investigation\n"
        )
        assert (paths["state"].read_bytes(), paths["events"].read_bytes()) == before

    assert calls == []
    assert key_calls == []


def test_workflows_execution_output_and_errors_never_expose_credentials_or_raw_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    calls: list[object] = []
    key_calls: list[int] = []
    raw_secret = "credential-only-secret"
    raw_payload = "raw-provider-payload-secret"

    def load_key() -> OpenAIApiKey:
        key_calls.append(1)
        return OpenAIApiKey(value=SecretStr(raw_secret))

    monkeypatch.setattr(cli_module, "load_openai_api_key_from_environment", load_key)
    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        synthetic_transport(
            calls,
            status_code=500,
            body=json.dumps(
                {
                    "error": {
                        "message": raw_payload,
                        "type": "secret_type",
                        "param": "secret_param",
                        "code": "secret_code",
                    }
                }
            ).encode(),
        ),
    )
    _, preview = preview_command("start", "research-and-summarize", paths)

    result = invoke_execution("start", "research-and-summarize", paths, preview)
    combined = result.stdout + result.stderr

    assert result.exit_code == 1
    assert raw_secret not in combined
    assert raw_payload not in combined
    assert "secret_type" not in combined
    assert "secret_param" not in combined
    assert "secret_code" not in combined
    assert len(calls) == 1
    assert key_calls == [1]


def test_workflows_continue_preview_displays_exact_upstream_provenance_text_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_valid_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    write_succeeded_prefix(paths, 1)
    sentinel = "  prior line\n日本語 😀\n\tfinal line  "
    replace_last_output(paths, sentinel)
    before = paths["state"].read_bytes(), paths["events"].read_bytes()
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)

    result, preview = preview_command("continue", "research-and-summarize", paths)

    provenance = {
        "workflow_id": "research-and-summarize",
        "step_id": "research",
        "step_index": 1,
        "employee_id": "general-researcher",
        "output_text": sentinel,
    }
    digest_value = json.dumps(
        provenance,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    task_input = json.dumps(
        {
            "task_instructions": "Summarize the information.",
            "upstream_inputs": [provenance],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    assert result.exit_code == 0
    assert preview["upstream_inputs"] == [
        {
            **provenance,
            "sha256": hashlib.sha256(digest_value.encode("utf-8")).hexdigest(),
        }
    ]
    assert preview["task_input"] == task_input
    assert preview["system_instructions"] == "Work on the assigned step."
    assert sentinel not in preview["system_instructions"]
    assert (paths["state"].read_bytes(), paths["events"].read_bytes()) == before
    assert calls == []
    assert key_calls == []


def test_workflows_continue_restart_style_execution_sends_exact_upstream_once_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_three_step_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    write_succeeded_prefix(paths, 1)
    sentinel = "restart-safe sentinel\n日本語"
    replace_last_output(paths, sentinel)
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)
    _, preview = preview_command("continue", "research-and-summarize", paths)

    result = invoke_execution("continue", "research-and-summarize", paths, preview)
    output = json.loads(result.stdout)
    request_body = json.loads(calls[0].body)  # type: ignore[union-attr]
    event_records = [
        json.loads(line)
        for line in paths["events"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result.exit_code == 0
    assert output["status"] == "prepare_next_step"
    assert len(calls) == 1
    assert request_body["input"] == preview["task_input"]
    assert (
        json.loads(request_body["input"])["upstream_inputs"][0]["output_text"]
        == sentinel
    )
    assert sentinel not in request_body["instructions"]
    assert len(event_records) == 2
    assert event_records[-1]["step_id"] == "summarize"
    assert load_workflow_execution_state(paths["state"]).current_step_index == 2
    assert key_calls == [1]


def test_workflows_continue_changed_predecessor_rejects_old_fingerprint_before_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_three_step_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    write_succeeded_prefix(paths, 1)
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)
    _, old_preview = preview_command("continue", "research-and-summarize", paths)
    replace_last_output(paths, "changed after preview")
    before = paths["state"].read_bytes(), paths["events"].read_bytes()

    result = invoke_execution("continue", "research-and-summarize", paths, old_preview)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: expected preview does not match current step\n"
    assert (paths["state"].read_bytes(), paths["events"].read_bytes()) == before
    assert calls == []
    assert key_calls == []


def test_workflows_continue_mutation_before_phase190_guard_rejects_before_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = workflow_command_paths(tmp_path)
    write_three_step_workflow(paths["workflows"])
    write_valid_employee(paths["employees"])
    write_succeeded_prefix(paths, 1)
    calls: list[object] = []
    key_calls: list[int] = []
    patch_cli_execution_seams(monkeypatch, calls, key_calls)
    _, preview = preview_command("continue", "research-and-summarize", paths)
    real_phase212 = cli_module.route_persisted_terminal_workflow_bounded
    mutated_snapshot: list[tuple[bytes, bytes]] = []

    def mutate_before_phase190(
        workflow: object,
        state_path: object,
        events_path: object,
        contexts: object,
    ) -> object:
        assert state_path == paths["state"]
        assert events_path == paths["events"]
        replace_last_output(paths, "mutated after CLI approval binding")
        mutated_snapshot.append(
            (paths["state"].read_bytes(), paths["events"].read_bytes())
        )
        return real_phase212(workflow, state_path, events_path, contexts)

    monkeypatch.setattr(
        cli_module,
        "route_persisted_terminal_workflow_bounded",
        mutate_before_phase190,
    )
    result = invoke_execution("continue", "research-and-summarize", paths, preview)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == "Error: workflow continuation failed\n"
    assert mutated_snapshot
    assert (
        paths["state"].read_bytes(),
        paths["events"].read_bytes(),
    ) == mutated_snapshot[0]
    assert load_workflow_execution_state(paths["state"]).current_step_index == 1
    assert calls == []
    assert key_calls == [1]
