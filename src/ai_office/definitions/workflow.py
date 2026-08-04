"""Workflow definition models, YAML loading, and employee reference validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from ai_office.definitions.employee import LoadedEmployee


class WorkflowStepDefinition(BaseModel):
    """One ordered step in a text-defined workflow."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str
    employee: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    instructions: str

    @field_validator("name", "instructions")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        """Reject blank values without changing the supplied definition."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class WorkflowDefinition(BaseModel):
    """A text-defined, ordered workflow."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str
    description: str
    steps: list[WorkflowStepDefinition] = Field(min_length=1)

    @field_validator("name", "description")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        """Reject blank values without changing the supplied definition."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def step_ids_must_be_unique(self) -> WorkflowDefinition:
        """Reject duplicate step IDs without changing their order."""
        seen_ids: set[str] = set()
        for step in self.steps:
            if step.id in seen_ids:
                raise ValueError(f"duplicate step id {step.id!r}")
            seen_ids.add(step.id)
        return self


@dataclass(frozen=True)
class LoadedWorkflow:
    """A workflow definition together with its source file."""

    source_path: Path
    definition: WorkflowDefinition


class WorkflowLoadError(ValueError):
    """An error that can be shown to a workflow-definition user."""


def load_workflow_file(path: Path) -> LoadedWorkflow:
    """Load and validate one workflow definition YAML file."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise WorkflowLoadError(f"{path}: could not read file: {error}") from error

    try:
        raw_definition = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise WorkflowLoadError(f"{path}: invalid YAML: {error}") from error

    if not isinstance(raw_definition, dict):
        raise WorkflowLoadError(f"{path}: YAML top level must be a mapping")

    try:
        definition = WorkflowDefinition.model_validate(raw_definition)
    except ValidationError as error:
        message = f"{path}: invalid workflow definition: {error}"
        raise WorkflowLoadError(message) from error

    return LoadedWorkflow(source_path=path, definition=definition)


def load_workflows(directory: Path) -> list[LoadedWorkflow]:
    """Load all direct YAML children of a directory in deterministic order."""
    if not directory.exists():
        raise WorkflowLoadError(f"{directory}: directory does not exist")
    if not directory.is_dir():
        raise WorkflowLoadError(f"{directory}: expected a directory")

    paths = sorted(
        (
            path
            for pattern in ("*.yaml", "*.yml")
            for path in directory.glob(pattern)
            if path.is_file()
        ),
        key=lambda path: path.as_posix(),
    )
    loaded_workflows = [load_workflow_file(path) for path in paths]

    seen_ids: dict[str, Path] = {}
    for loaded_workflow in loaded_workflows:
        workflow_id = loaded_workflow.definition.id
        if workflow_id in seen_ids:
            first_path = seen_ids[workflow_id]
            raise WorkflowLoadError(
                f"{loaded_workflow.source_path}: duplicate workflow id "
                f"{workflow_id!r}; already defined in {first_path}"
            )
        seen_ids[workflow_id] = loaded_workflow.source_path

    return loaded_workflows


def validate_workflow_employee_references(
    workflows: list[LoadedWorkflow], employees: list[LoadedEmployee]
) -> None:
    """Fail fast on the first missing employee in deterministic workflow order."""
    employee_ids = {employee.definition.id for employee in employees}
    for workflow in sorted(workflows, key=lambda item: item.source_path.as_posix()):
        for step in workflow.definition.steps:
            if step.employee not in employee_ids:
                raise WorkflowLoadError(
                    f"{workflow.source_path}: workflow {workflow.definition.id!r}, "
                    f"step {step.id!r}: employee {step.employee!r} is not defined"
                )
