"""Tests for employee definition loading and validation."""

from pathlib import Path

import pytest

from ai_office.definitions.employee import (
    EmployeeLoadError,
    load_employee_file,
    load_employees,
)


def write_employee(path: Path, **overrides: object) -> None:
    """Write a valid employee YAML file with optional field overrides."""
    definition: dict[str, object] = {
        "id": "general-researcher",
        "name": "General Researcher",
        "role": "Organizes information.",
        "instructions": "Work on the assigned step.",
        "model": "codex",
        "allowed_tools": [],
    }
    definition.update(overrides)
    lines = []
    for key, value in definition.items():
        if isinstance(value, list):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {value!r}")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_load_employee_file_loads_valid_yaml(tmp_path: Path) -> None:
    source = tmp_path / "employee.yaml"
    write_employee(source, allowed_tools=["later", "first"])

    loaded = load_employee_file(source)

    assert loaded.source_path == source
    assert loaded.definition.id == "general-researcher"
    assert loaded.definition.allowed_tools == ["later", "first"]


def test_load_employees_loads_yaml_and_yml_in_path_order(tmp_path: Path) -> None:
    write_employee(tmp_path / "zeta.yml", id="zeta")
    write_employee(tmp_path / "alpha.yaml", id="alpha")
    write_employee(tmp_path / "ignored.txt", id="ignored")

    loaded = load_employees(tmp_path)

    assert [employee.definition.id for employee in loaded] == ["alpha", "zeta"]


def test_load_employees_ignores_subdirectories_and_yaml_named_directories(
    tmp_path: Path,
) -> None:
    write_employee(tmp_path / "direct.yaml", id="direct")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested.yaml").mkdir()
    (tmp_path / "archive.yml").mkdir()

    loaded = load_employees(tmp_path)

    assert [employee.definition.id for employee in loaded] == ["direct"]


def test_load_employees_returns_empty_list_for_empty_directory(tmp_path: Path) -> None:
    assert load_employees(tmp_path) == []


def test_load_employees_rejects_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(EmployeeLoadError, match=str(missing)):
        load_employees(missing)


def test_load_employees_rejects_file_as_directory(tmp_path: Path) -> None:
    source = tmp_path / "employee.yaml"
    write_employee(source)

    with pytest.raises(EmployeeLoadError, match="expected a directory"):
        load_employees(source)


def test_load_employee_file_rejects_invalid_yaml(tmp_path: Path) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_text("id: [", encoding="utf-8")

    with pytest.raises(EmployeeLoadError, match="invalid YAML"):
        load_employee_file(source)


def test_load_employee_file_rejects_invalid_utf8(tmp_path: Path) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_bytes(b"\xff")

    with pytest.raises(EmployeeLoadError, match="could not read file"):
        load_employee_file(source)


@pytest.mark.parametrize("content", ["- employee", "employee", "null"])
def test_load_employee_file_rejects_non_mapping_yaml(
    tmp_path: Path, content: str
) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(EmployeeLoadError, match="top level must be a mapping"):
        load_employee_file(source)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"unexpected": "field"}, "unexpected"),
        ({"allowed_tools": [""]}, "allowed_tools"),
        ({"allowed_tools": ["search", "search"]}, "allowed_tools"),
    ],
)
def test_load_employee_file_rejects_invalid_definition(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    source = tmp_path / "invalid.yaml"
    write_employee(source, **overrides)

    with pytest.raises(EmployeeLoadError, match=message):
        load_employee_file(source)


@pytest.mark.parametrize(
    "invalid_id",
    ["Uppercase", "contains_underscore", "-leading", "trailing-", "double--hyphen"],
)
def test_load_employee_file_rejects_invalid_id(tmp_path: Path, invalid_id: str) -> None:
    source = tmp_path / "invalid.yaml"
    write_employee(source, id=invalid_id)

    with pytest.raises(EmployeeLoadError, match="id"):
        load_employee_file(source)


def test_load_employee_file_rejects_missing_required_field(tmp_path: Path) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_text("id: general-researcher\n", encoding="utf-8")

    with pytest.raises(EmployeeLoadError, match="name"):
        load_employee_file(source)


def test_load_employee_file_requires_allowed_tools(tmp_path: Path) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_text(
        """id: general-researcher
name: General Researcher
role: Organizes information.
instructions: Work on the assigned step.
model: codex
""",
        encoding="utf-8",
    )

    with pytest.raises(EmployeeLoadError, match="allowed_tools"):
        load_employee_file(source)


@pytest.mark.parametrize("field", ["name", "role", "instructions", "model"])
def test_load_employee_file_rejects_blank_required_string(
    tmp_path: Path, field: str
) -> None:
    source = tmp_path / "invalid.yaml"
    write_employee(source, **{field: "   "})

    with pytest.raises(EmployeeLoadError, match=field):
        load_employee_file(source)


def test_load_employees_rejects_duplicate_ids(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yml"
    write_employee(first, id="duplicate")
    write_employee(second, id="duplicate")

    with pytest.raises(EmployeeLoadError, match="duplicate employee id") as error:
        load_employees(tmp_path)

    assert str(first) in str(error.value)
    assert str(second) in str(error.value)
