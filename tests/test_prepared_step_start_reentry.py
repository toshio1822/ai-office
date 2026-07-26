"""Tests for the read-only Phase 33 prepared-step start reentry boundary."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PreparedStepExecutionStartCompatibilityError,
    PreparedStepStartReentryCompatibilityError,
    PreparedWorkflowStep,
    prepare_persisted_prepared_step_start,
)
from ai_office.engine.prepared_step_execution_start import (
    prepare_prepared_step_execution_start,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "Description",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "a",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "b",
                },
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "two",
            "name": "Two",
            "role": "Role",
            "instructions": "employee instructions",
            "model": "model",
            "allowed_tools": ["first", "second"],
        }
    )


def state(**changes: object) -> WorkflowExecutionState:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "status": "succeeded",
        "current_step_id": "first",
        "current_step_index": 1,
        "current_employee_id": "one",
        "completed_step_ids": ("first",),
        "last_failure_category": None,
    }
    values.update(changes)
    return WorkflowExecutionState(**values)  # type: ignore[arg-type]


def event() -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded", "workflow", "first", 1, "one", "running", "succeeded",
        "openai", None, "response", "request", "output", None
    )


def write_history(
    tmp_path: Path, value: WorkflowExecutionState
) -> WorkflowExecutionPersistenceTargets:
    targets = WorkflowExecutionPersistenceTargets(
        tmp_path / "state.json", tmp_path / "events.jsonl"
    )
    targets.state_path.write_text(serialize_workflow_execution_state_json(value))
    targets.events_path.write_text(serialize_runtime_step_event_jsonl(event()))
    return targets


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow",
        "second",
        2,
        "two",
        "employee instructions",
        "b",
        "model",
        ("first", "second"),
    )


def test_valid_reentry_delegates_once_and_returns_exact_result(tmp_path: Path) -> None:
    targets = write_history(tmp_path, state())
    expected = prepare_prepared_step_execution_start(
        prepared(), load_workflow_execution_history(targets)
    )
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())
    calls = 0

    def start(*_args: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert prepare_persisted_prepared_step_start(
        workflow(), employee(), targets.state_path, targets.events_path, prepared(),
        start_function=start,  # type: ignore[arg-type]
    ) is expected
    assert calls == 1
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


@pytest.mark.parametrize(
    "changed",
    [
        lambda value: object(),
        lambda value: replace(value, workflow_id="other"),
        lambda value: replace(value, step_id="other"),
        lambda value: replace(value, step_index=3),
        lambda value: replace(value, employee_id="other"),
        lambda value: replace(value, employee_instructions="other"),
        lambda value: replace(value, step_instructions="other"),
        lambda value: replace(value, model="other"),
        lambda value: replace(value, allowed_tool_names=("other",)),
        lambda value: replace(value, allowed_tool_names=("second", "first")),
        lambda value: replace(value, step_id=""),
    ],
)
def test_invalid_prepared_step_rejects_before_start(
    tmp_path: Path, changed: object
) -> None:
    targets = write_history(tmp_path, state())
    value = changed(prepared())  # type: ignore[operator]
    calls = 0
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())

    def unexpected(*_args: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PreparedStepStartReentryCompatibilityError):
        prepare_persisted_prepared_step_start(
            workflow(), employee(), targets.state_path, targets.events_path, value,
            start_function=unexpected,  # type: ignore[arg-type]
        )
    assert calls == 0
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


@pytest.mark.parametrize(
    "changed_state",
    [
        lambda value: replace(value, status="ready"),
        lambda value: replace(value, workflow_id="other"),
        lambda value: replace(value, completed_step_ids=("second",)),
    ],
)
def test_bad_history_rejects_before_start(
    tmp_path: Path, changed_state: object
) -> None:
    targets = write_history(tmp_path, changed_state(state()))  # type: ignore[operator]
    calls = 0

    def unexpected(*_args: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PreparedStepStartReentryCompatibilityError):
        prepare_persisted_prepared_step_start(
            workflow(), employee(), targets.state_path, targets.events_path, prepared(),
            start_function=unexpected,  # type: ignore[arg-type]
        )
    assert calls == 0


@pytest.mark.parametrize(
    "changed",
    [
        lambda value: object(),
        lambda value: replace(value, request=replace(value.request, model="other")),
        lambda value: replace(
            value, request=replace(value.request, system_instructions="other")
        ),
        lambda value: replace(
            value, request=replace(value.request, task_instructions="other")
        ),
        lambda value: replace(
            value, request=replace(value.request, allowed_tools=("second", "first"))
        ),
        lambda value: replace(
            value, running_state=replace(value.running_state, status="succeeded")
        ),
        lambda value: replace(
            value, running_state=replace(value.running_state, workflow_id="other")
        ),
        lambda value: replace(
            value, running_state=replace(value.running_state, current_step_id="other")
        ),
        lambda value: replace(
            value, running_state=replace(value.running_state, current_step_index=3)
        ),
        lambda value: replace(
            value,
            running_state=replace(value.running_state, current_employee_id="other"),
        ),
        lambda value: replace(
            value,
            running_state=replace(value.running_state, completed_step_ids=("other",)),
        ),
        lambda value: replace(
            value,
            running_state=replace(
                value.running_state, last_failure_category="api_error"
            ),
        ),
    ],
)
def test_invalid_start_result_is_rejected_after_one_call(
    tmp_path: Path, changed: object
) -> None:
    targets = write_history(tmp_path, state())
    expected = prepare_prepared_step_execution_start(
        prepared(), load_workflow_execution_history(targets)
    )
    returned = changed(expected)  # type: ignore[operator]
    calls = 0

    def start(*_args: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    with pytest.raises(PreparedStepStartReentryCompatibilityError) as caught:
        prepare_persisted_prepared_step_start(
            workflow(), employee(), targets.state_path, targets.events_path, prepared(),
            start_function=start,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "start_contract"
    assert calls == 1


def test_phase_27_safe_error_is_preserved(tmp_path: Path) -> None:
    targets = write_history(tmp_path, state())
    expected = PreparedStepExecutionStartCompatibilityError("request_data")

    def rejected(*_args: object) -> object:
        raise expected

    with pytest.raises(PreparedStepExecutionStartCompatibilityError) as caught:
        prepare_persisted_prepared_step_start(
            workflow(), employee(), targets.state_path, targets.events_path, prepared(),
            start_function=rejected,  # type: ignore[arg-type]
        )
    assert caught.value is expected


def test_missing_target_is_safe(tmp_path: Path) -> None:
    with pytest.raises(PreparedStepStartReentryCompatibilityError) as caught:
        prepare_persisted_prepared_step_start(
            workflow(),
            employee(),
            tmp_path / "missing",
            tmp_path / "events",
            prepared(),
        )
    assert str(tmp_path) not in str(caught.value)
