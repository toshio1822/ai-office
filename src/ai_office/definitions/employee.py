"""Employee definition models and YAML loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class EmployeeDefinition(BaseModel):
    """A text-defined AI employee."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str
    role: str
    instructions: str
    model: str
    allowed_tools: list[str]

    @field_validator("name", "role", "instructions", "model")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        """Reject blank values without changing the supplied definition."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def tools_must_be_unique_and_nonblank(cls, value: list[str]) -> list[str]:
        """Validate tool names without resolving or modifying them."""
        if any(not tool.strip() for tool in value):
            raise ValueError("must not contain blank values")
        if len(value) != len(set(value)):
            raise ValueError("must not contain duplicate values")
        return value


@dataclass(frozen=True)
class LoadedEmployee:
    """An employee definition together with its source file."""

    source_path: Path
    definition: EmployeeDefinition


class EmployeeLoadError(ValueError):
    """An error that can be shown to an employee-definition user."""


def load_employee_file(path: Path) -> LoadedEmployee:
    """Load and validate one employee definition YAML file."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise EmployeeLoadError(f"{path}: could not read file: {error}") from error

    try:
        raw_definition = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise EmployeeLoadError(f"{path}: invalid YAML: {error}") from error

    if not isinstance(raw_definition, dict):
        raise EmployeeLoadError(f"{path}: YAML top level must be a mapping")

    try:
        definition = EmployeeDefinition.model_validate(raw_definition)
    except ValidationError as error:
        message = f"{path}: invalid employee definition: {error}"
        raise EmployeeLoadError(message) from error

    return LoadedEmployee(source_path=path, definition=definition)


def load_employees(directory: Path) -> list[LoadedEmployee]:
    """Load all direct YAML children of a directory in deterministic order."""
    if not directory.exists():
        raise EmployeeLoadError(f"{directory}: directory does not exist")
    if not directory.is_dir():
        raise EmployeeLoadError(f"{directory}: expected a directory")

    paths = sorted(
        [*directory.glob("*.yaml"), *directory.glob("*.yml")],
        key=lambda path: path.as_posix(),
    )
    loaded_employees = [load_employee_file(path) for path in paths]

    seen_ids: dict[str, Path] = {}
    for loaded_employee in loaded_employees:
        employee_id = loaded_employee.definition.id
        if employee_id in seen_ids:
            first_path = seen_ids[employee_id]
            raise EmployeeLoadError(
                f"{loaded_employee.source_path}: duplicate employee id "
                f"{employee_id!r}; "
                f"already defined in {first_path}"
            )
        seen_ids[employee_id] = loaded_employee.source_path

    return loaded_employees
