"""Tests for workflow definition loading and validation."""

from pathlib import Path

import pytest
import yaml

from ai_office.definitions.employee import EmployeeDefinition, LoadedEmployee
from ai_office.definitions.workflow import (
    WorkflowLoadError,
    load_workflow_file,
    load_workflows,
    validate_workflow_employee_references,
)


def workflow_data(**overrides: object) -> dict[str, object]:
    """Return a valid workflow mapping with optional overrides."""
    definition: dict[str, object] = {
        "id": "research-and-summarize",
        "name": "Research and Summarize",
        "description": "Researches a topic and summarizes it.",
        "steps": [
            {
                "id": "research",
                "name": "Research",
                "employee": "general-researcher",
                "instructions": "Gather relevant information.",
            },
            {
                "id": "summarize",
                "name": "Summarize",
                "employee": "general-researcher",
                "instructions": "Summarize the information.",
            },
        ],
    }
    definition.update(overrides)
    return definition


def write_workflow(path: Path, **overrides: object) -> None:
    path.write_text(
        yaml.safe_dump(workflow_data(**overrides), sort_keys=False), encoding="utf-8"
    )


def loaded_employee(employee_id: str) -> LoadedEmployee:
    return LoadedEmployee(
        source_path=Path(f"{employee_id}.yaml"),
        definition=EmployeeDefinition(
            id=employee_id,
            name="Employee",
            role="Handles assigned work.",
            instructions="Work on the assigned step.",
            model="codex",
            allowed_tools=[],
        ),
    )


def test_load_workflow_file_loads_valid_yaml_and_preserves_step_order(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.yaml"
    write_workflow(source)

    loaded = load_workflow_file(source)

    assert loaded.source_path == source
    assert loaded.definition.id == "research-and-summarize"
    assert [step.id for step in loaded.definition.steps] == ["research", "summarize"]


def test_load_workflows_loads_yaml_and_yml_in_path_order(tmp_path: Path) -> None:
    write_workflow(tmp_path / "zeta.yml", id="zeta")
    write_workflow(tmp_path / "alpha.yaml", id="alpha")
    write_workflow(tmp_path / "ignored.txt", id="ignored")

    loaded = load_workflows(tmp_path)

    assert [workflow.definition.id for workflow in loaded] == ["alpha", "zeta"]


def test_load_workflows_ignores_subdirectories_and_yaml_named_directories(
    tmp_path: Path,
) -> None:
    write_workflow(tmp_path / "direct.yaml", id="direct")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested.yaml").mkdir()
    (tmp_path / "archive.yml").mkdir()
    write_workflow(tmp_path / "nested" / "ignored.yaml", id="ignored")

    loaded = load_workflows(tmp_path)

    assert [workflow.definition.id for workflow in loaded] == ["direct"]


def test_load_workflows_returns_empty_list_for_empty_directory(tmp_path: Path) -> None:
    assert load_workflows(tmp_path) == []


def test_load_workflows_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(WorkflowLoadError, match="directory does not exist"):
        load_workflows(tmp_path / "missing")


def test_load_workflows_rejects_file_as_directory(tmp_path: Path) -> None:
    source = tmp_path / "workflow.yaml"
    write_workflow(source)

    with pytest.raises(WorkflowLoadError, match="expected a directory"):
        load_workflows(source)


def test_load_workflow_file_rejects_invalid_yaml(tmp_path: Path) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_text("id: [", encoding="utf-8")

    with pytest.raises(WorkflowLoadError, match="invalid YAML"):
        load_workflow_file(source)


def test_load_workflow_file_rejects_invalid_utf8(tmp_path: Path) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_bytes(b"\xff")

    with pytest.raises(WorkflowLoadError, match="could not read file"):
        load_workflow_file(source)


@pytest.mark.parametrize("content", ["- workflow", "workflow", "null"])
def test_load_workflow_file_rejects_non_mapping_yaml(
    tmp_path: Path, content: str
) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(WorkflowLoadError, match="top level must be a mapping"):
        load_workflow_file(source)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"unexpected": "field"}, "unexpected"),
        ({"steps": []}, "steps"),
        ({"steps": [{"id": "research"}]}, "name"),
        (
            {
                "steps": [
                    {
                        "id": "research",
                        "name": "Research",
                        "employee": "general-researcher",
                        "instructions": "Gather.",
                        "unexpected": "field",
                    }
                ]
            },
            "unexpected",
        ),
    ],
)
def test_load_workflow_file_rejects_invalid_definition(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    source = tmp_path / "invalid.yaml"
    write_workflow(source, **overrides)

    with pytest.raises(WorkflowLoadError, match=message):
        load_workflow_file(source)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("id", "Uppercase"),
        ("id", "contains_underscore"),
        ("id", "-leading"),
        ("id", "trailing-"),
        ("id", "double--hyphen"),
    ],
)
def test_load_workflow_file_rejects_invalid_workflow_id(
    tmp_path: Path, field: str, invalid_value: str
) -> None:
    source = tmp_path / "invalid.yaml"
    write_workflow(source, **{field: invalid_value})

    with pytest.raises(WorkflowLoadError, match="id"):
        load_workflow_file(source)


@pytest.mark.parametrize(
    "field", ["name", "description"]
)
def test_load_workflow_file_rejects_blank_workflow_string(
    tmp_path: Path, field: str
) -> None:
    source = tmp_path / "invalid.yaml"
    write_workflow(source, **{field: "   "})

    with pytest.raises(WorkflowLoadError, match=field):
        load_workflow_file(source)


@pytest.mark.parametrize(
    "invalid_value",
    ["Uppercase", "contains_underscore", "-leading", "trailing-", "double--hyphen"],
)
def test_load_workflow_file_rejects_invalid_step_id(
    tmp_path: Path, invalid_value: str
) -> None:
    source = tmp_path / "invalid.yaml"
    data = workflow_data()
    steps = data["steps"]
    assert isinstance(steps, list)
    steps[0]["id"] = invalid_value
    source.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowLoadError, match="steps.0.id"):
        load_workflow_file(source)


@pytest.mark.parametrize(
    "invalid_value",
    [
        "Uppercase",
        "contains_underscore",
        "-leading",
        "trailing-",
        "double--hyphen",
        "   ",
    ],
)
def test_load_workflow_file_rejects_invalid_employee_id(
    tmp_path: Path, invalid_value: str
) -> None:
    source = tmp_path / "invalid.yaml"
    data = workflow_data()
    steps = data["steps"]
    assert isinstance(steps, list)
    steps[0]["employee"] = invalid_value
    source.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowLoadError, match="employee"):
        load_workflow_file(source)


@pytest.mark.parametrize("field", ["name", "instructions"])
def test_load_workflow_file_rejects_blank_step_string(
    tmp_path: Path, field: str
) -> None:
    source = tmp_path / "invalid.yaml"
    data = workflow_data()
    steps = data["steps"]
    assert isinstance(steps, list)
    steps[0][field] = "   "
    source.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowLoadError, match=field):
        load_workflow_file(source)


def test_load_workflow_file_rejects_missing_steps(tmp_path: Path) -> None:
    source = tmp_path / "invalid.yaml"
    data = workflow_data()
    data.pop("steps")
    source.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowLoadError, match="steps"):
        load_workflow_file(source)


def test_load_workflow_file_rejects_duplicate_step_ids_with_path(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid.yaml"
    data = workflow_data()
    steps = data["steps"]
    assert isinstance(steps, list)
    steps[1]["id"] = "research"
    source.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowLoadError, match="duplicate step id") as error:
        load_workflow_file(source)

    assert str(source) in str(error.value)


def test_load_workflows_rejects_duplicate_ids_with_both_paths(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yml"
    write_workflow(first, id="duplicate")
    write_workflow(second, id="duplicate")

    with pytest.raises(WorkflowLoadError, match="duplicate workflow id") as error:
        load_workflows(tmp_path)

    assert str(first) in str(error.value)
    assert str(second) in str(error.value)


def test_validate_workflow_employee_references_accepts_reused_employee(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.yaml"
    write_workflow(source)
    workflows = [load_workflow_file(source)]

    validate_workflow_employee_references(
        workflows, [loaded_employee("general-researcher")]
    )


def test_validate_workflow_employee_references_reports_missing_employee(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.yaml"
    write_workflow(source)
    workflow = load_workflow_file(source)

    with pytest.raises(
        WorkflowLoadError, match="employee 'general-researcher'"
    ) as error:
        validate_workflow_employee_references([workflow], [])

    message = str(error.value)
    assert str(source) in message
    assert "research-and-summarize" in message
    assert "research" in message


def test_validate_workflow_employee_references_fails_in_path_then_step_order(
    tmp_path: Path,
) -> None:
    first = tmp_path / "alpha.yaml"
    second = tmp_path / "zeta.yml"
    write_workflow(first, id="alpha")
    write_workflow(second, id="zeta")
    workflows = [load_workflow_file(second), load_workflow_file(first)]

    with pytest.raises(WorkflowLoadError, match="alpha.yaml") as error:
        validate_workflow_employee_references(workflows, [])

    assert "step 'research'" in str(error.value)
