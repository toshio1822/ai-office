"""Observable Phase 31 persisted-success progression guarantees."""

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedSuccessProgressionCompatibilityError,
    decide_persisted_success_progression,
)
from ai_office.engine import persisted_success_progression as progression_module
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow(**changes: object) -> WorkflowDefinition:
    values: dict[str, object] = {
        "id": "workflow",
        "name": "Workflow",
        "description": "Description",
        "steps": [
            {
                "id": "first",
                "name": "First",
                "employee": "employee",
                "instructions": "a",
            },
            {"id": "step", "name": "Step", "employee": "employee", "instructions": "b"},
        ],
    }
    values.update(changes)
    return WorkflowDefinition(**values)


def state(**changes: object) -> WorkflowExecutionState:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "status": "succeeded",
        "current_step_id": "step",
        "current_step_index": 2,
        "current_employee_id": "employee",
        "completed_step_ids": ("first", "step"),
        "last_failure_category": None,
    }
    values.update(changes)
    return WorkflowExecutionState(**values)  # type: ignore[arg-type]


def event(**changes: object) -> RuntimeStepEvent:
    values: dict[str, object] = {
        "event_type": "step_succeeded",
        "workflow_id": "workflow",
        "step_id": "step",
        "step_index": 2,
        "employee_id": "employee",
        "previous_status": "running",
        "next_status": "succeeded",
        "provider": "openai",
        "failure_category": None,
        "response_id": "response",
        "request_id": "request",
        "output_text": "output",
        "message": None,
    }
    values.update(changes)
    return RuntimeStepEvent(**values)  # type: ignore[arg-type]


def write_history(
    tmp_path: Path, value: WorkflowExecutionState, *events: RuntimeStepEvent
) -> WorkflowExecutionPersistenceTargets:
    targets = WorkflowExecutionPersistenceTargets(
        tmp_path / "state.json", tmp_path / "events.jsonl"
    )
    targets.state_path.write_text(serialize_workflow_execution_state_json(value))
    targets.events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
    )
    return targets


def test_public_contract_removes_decision_function_seam(tmp_path: Path) -> None:
    assert list(inspect.signature(decide_persisted_success_progression).parameters) == [
        "workflow",
        "state_path",
        "events_path",
    ]
    targets = write_history(tmp_path, state(), event())
    with pytest.raises(TypeError):
        decide_persisted_success_progression(
            workflow(),
            targets.state_path,
            targets.events_path,
            decision_function=lambda *_: None,
        )  # type: ignore[call-arg]


def test_non_final_success_returns_prepare_next_step_without_writing(
    tmp_path: Path,
) -> None:
    value = state(
        current_step_index=1, current_step_id="first", completed_step_ids=("first",)
    )
    targets = write_history(tmp_path, value, event(step_id="first", step_index=1))
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())

    result = decide_persisted_success_progression(
        workflow(), targets.state_path, targets.events_path
    )

    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "prepare_next_step"
    assert result.next_step_id == "step"
    assert result.next_step_index == 2
    assert result.reason == "next_step_available"
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_final_success_returns_workflow_complete_without_writing(
    tmp_path: Path,
) -> None:
    targets = write_history(tmp_path, state(), event())
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())

    result = decide_persisted_success_progression(
        workflow(), targets.state_path, targets.events_path
    )

    assert result.decision == "workflow_complete"
    assert result.next_step_id is None
    assert result.next_step_index is None
    assert result.next_employee_id is None
    assert result.reason == "last_step_succeeded"
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


@pytest.mark.parametrize("status", ["ready", "running", "failed"])
def test_non_success_status_fails_closed_before_decision(
    tmp_path: Path, status: str
) -> None:
    value = state(
        status=status,
        completed_step_ids=(),
        last_failure_category="api_error" if status == "failed" else None,
    )
    terminal = (
        event(
            event_type="step_failed",
            next_status="failed",
            failure_category="api_error",
            response_id=None,
            output_text=None,
            message="safe",
        )
        if status == "failed"
        else None
    )
    targets = write_history(tmp_path, value, *(()) if terminal is None else (terminal,))
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())
    called = False
    real = progression_module.decide_workflow_progression

    def unexpected(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        return real(*_args, **_kwargs)

    progression_module.decide_workflow_progression = unexpected  # type: ignore[assignment]
    try:
        with pytest.raises(PersistedSuccessProgressionCompatibilityError):
            decide_persisted_success_progression(
                workflow(), targets.state_path, targets.events_path
            )
    finally:
        progression_module.decide_workflow_progression = real  # type: ignore[assignment]

    assert called is False
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_decision_contract_rejects_malformed_owner_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    targets = write_history(
        tmp_path,
        state(
            current_step_index=1,
            current_step_id="first",
            completed_step_ids=("first",),
        ),
        event(step_id="first", step_index=1),
    )
    valid = WorkflowProgressionDecision(
        decision="prepare_next_step",
        workflow_id="workflow",
        current_step_id="first",
        current_step_index=1,
        current_employee_id="employee",
        next_step_id="step",
        next_step_index=2,
        next_employee_id="employee",
        reason="next_step_available",
    )
    monkeypatch.setattr(
        progression_module,
        "decide_workflow_progression",
        lambda *_args, **_kwargs: replace(valid, reason="unexpected"),
    )

    with pytest.raises(PersistedSuccessProgressionCompatibilityError) as error:
        decide_persisted_success_progression(
            workflow(), targets.state_path, targets.events_path
        )

    assert error.value.detail.classification == "decision_contract"


@pytest.mark.parametrize(
    ("state_changes", "event_changes", "workflow_changes", "classification"),
    [
        ({"status": "running"}, {}, {}, "state_status"),
        (
            {"status": "failed", "last_failure_category": "api_error"},
            {},
            {},
            "history_data",
        ),
        ({"last_failure_category": "api_error"}, {}, {}, "history_data"),
        (
            {},
            {
                "event_type": "step_failed",
                "next_status": "failed",
                "failure_category": "api_error",
                "message": "safe",
                "response_id": None,
                "output_text": None,
            },
            {},
            "history_data",
        ),
        ({}, {"employee_id": "other"}, {}, "history_data"),
        ({}, {}, {"id": "other"}, "workflow_identity"),
    ],
)
def test_invalid_persisted_success_fails_closed_without_writing(
    tmp_path: Path,
    state_changes: dict[str, object],
    event_changes: dict[str, object],
    workflow_changes: dict[str, object],
    classification: str,
) -> None:
    targets = write_history(tmp_path, state(**state_changes), event(**event_changes))
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())

    with pytest.raises(PersistedSuccessProgressionCompatibilityError) as error:
        decide_persisted_success_progression(
            workflow(**workflow_changes), targets.state_path, targets.events_path
        )

    assert error.value.detail.classification == classification
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


@pytest.mark.parametrize("contents", [None, b"bad", b"\xff"])
def test_missing_or_malformed_history_is_safe(
    tmp_path: Path, contents: bytes | None
) -> None:
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    if contents is None:
        state_path.write_text(serialize_workflow_execution_state_json(state()))
        events_path.write_text("")
    else:
        state_path.write_bytes(contents)
        events_path.write_text("")

    with pytest.raises(PersistedSuccessProgressionCompatibilityError) as error:
        decide_persisted_success_progression(workflow(), state_path, events_path)

    assert str(state_path) not in str(error.value)
