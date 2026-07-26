"""Tests for the read-only Phase 31 progression boundary."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedSuccessProgressionCompatibilityError,
    decide_persisted_success_progression,
)
from ai_office.engine.workflow_progression import decide_workflow_progression
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


def test_non_final_success_delegates_once_and_returns_exact_decision(
    tmp_path: Path,
) -> None:
    value = state(
        current_step_index=1, current_step_id="first", completed_step_ids=("first",)
    )
    targets = write_history(tmp_path, value, event(step_id="first", step_index=1))
    calls = 0
    expected = decide_workflow_progression(
        workflow(),
        __import__(
            "ai_office.storage", fromlist=["load_workflow_execution_history"]
        ).load_workflow_execution_history(targets),
    )

    def decide(definition: WorkflowDefinition, history: object) -> object:
        nonlocal calls
        calls += 1
        return expected

    assert (
        decide_persisted_success_progression(
            workflow(),
            targets.state_path,
            targets.events_path,
            decision_function=decide,
        )
        is expected
    )  # type: ignore[arg-type]
    assert calls == 1


def test_final_success_returns_existing_complete_decision(tmp_path: Path) -> None:
    targets = write_history(tmp_path, state(), event())
    result = decide_persisted_success_progression(
        workflow(), targets.state_path, targets.events_path
    )
    assert result.decision == "workflow_complete"


@pytest.mark.parametrize(
    ("final", "change"),
    [
        (False, None),
        (False, {"workflow_id": "other"}),
        (False, {"decision": "workflow_complete"}),
        (False, {"next_step_id": "other"}),
        (False, {"next_step_index": 9}),
        (False, {"next_employee_id": "other"}),
        (
            True,
            {
                "decision": "prepare_next_step",
                "next_step_id": "step",
                "next_step_index": 3,
                "next_employee_id": "employee",
                "reason": "next_step_available",
            },
        ),
        (True, {"reason": "other"}),
    ],
)
def test_invalid_decision_result_is_rejected_after_one_call(
    tmp_path: Path, final: bool, change: object
) -> None:
    current = (
        state()
        if final
        else state(
            current_step_index=1, current_step_id="first", completed_step_ids=("first",)
        )
    )
    current_event = event() if final else event(step_id="first", step_index=1)
    targets = write_history(tmp_path, current, current_event)
    before = (targets.state_path.read_bytes(), targets.events_path.read_bytes())
    valid = decide_workflow_progression(
        workflow(),
        __import__(
            "ai_office.storage", fromlist=["load_workflow_execution_history"]
        ).load_workflow_execution_history(targets),
    )
    calls = 0

    def returned(*_args: object) -> object:
        nonlocal calls
        calls += 1
        return object() if change is None else replace(valid, **change)  # type: ignore[arg-type]

    with pytest.raises(PersistedSuccessProgressionCompatibilityError) as error:
        decide_persisted_success_progression(
            workflow(),
            targets.state_path,
            targets.events_path,
            decision_function=returned,
        )  # type: ignore[arg-type]
    assert error.value.detail.classification == "decision_contract"
    assert calls == 1
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


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
        (
            {},
            {},
            {
                "steps": [
                    {
                        "id": "first",
                        "name": "First",
                        "employee": "employee",
                        "instructions": "a",
                    }
                ]
            },
            "workflow_identity",
        ),
    ],
)
def test_invalid_persisted_success_rejects_before_decision(
    tmp_path: Path,
    state_changes: dict[str, object],
    event_changes: dict[str, object],
    workflow_changes: dict[str, object],
    classification: str,
) -> None:
    targets = write_history(tmp_path, state(**state_changes), event(**event_changes))
    original_state, original_events = (
        targets.state_path.read_bytes(),
        targets.events_path.read_bytes(),
    )
    calls = 0

    def unexpected(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedSuccessProgressionCompatibilityError) as error:
        decide_persisted_success_progression(
            workflow(**workflow_changes),
            targets.state_path,
            targets.events_path,
            decision_function=unexpected,
        )  # type: ignore[arg-type]
    assert error.value.detail.classification == classification
    assert calls == 0
    assert targets.state_path.read_bytes() == original_state
    assert targets.events_path.read_bytes() == original_events


@pytest.mark.parametrize("contents", [None, b"bad", b"\xff"])
def test_missing_or_malformed_history_is_safe(
    tmp_path: Path, contents: bytes | None
) -> None:
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    if contents is None:
        events_path.write_text("")
    else:
        state_path.write_bytes(contents)
        events_path.write_text("")
    with pytest.raises(PersistedSuccessProgressionCompatibilityError) as error:
        decide_persisted_success_progression(workflow(), state_path, events_path)
    assert str(state_path) not in str(error.value)


def test_empty_event_history_is_rejected(tmp_path: Path) -> None:
    targets = write_history(tmp_path, state())
    with pytest.raises(PersistedSuccessProgressionCompatibilityError) as error:
        decide_persisted_success_progression(
            workflow(), targets.state_path, targets.events_path
        )
    assert error.value.detail.classification == "history_data"
