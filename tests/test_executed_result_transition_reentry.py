"""Tests for Phase 36 using only fake Phase 30 dependencies."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionReentryCompatibilityError,
    persist_executed_result_transition_reentry,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.runtime.executed_step_transition_persistence import (
    persist_executed_step_transition,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "Test",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "a",
                },
                {
                    "id": "step",
                    "name": "Step",
                    "employee": "employee",
                    "instructions": "b",
                },
            ],
        }
    )


def running(**changes: object) -> WorkflowExecutionState:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "status": "running",
        "current_step_id": "step",
        "current_step_index": 2,
        "current_employee_id": "employee",
        "completed_step_ids": ("first", "first"),
        "last_failure_category": None,
    }
    values.update(changes)
    return WorkflowExecutionState(**values)  # type: ignore[arg-type]


def success(**changes: object) -> StepRuntimeExecutionSuccess:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "step_id": "step",
        "step_index": 2,
        "employee_id": "employee",
        "invocation_result": ModelInvocationSuccess(
            "openai", "response", "request", "completed", ("output",), "output"
        ),
    }
    values.update(changes)
    return StepRuntimeExecutionSuccess(**values)  # type: ignore[arg-type]


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "workflow",
        "step",
        2,
        "employee",
        ModelInvocationFailure(
            "openai", "api_error", "safe", "request", None, None, None
        ),
    )


def setup(
    tmp_path: Path, state: WorkflowExecutionState | None = None
) -> tuple[Path, Path, bytes, bytes]:
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_bytes = serialize_workflow_execution_state_json(state or running()).encode()
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(b"")
    return state_path, events_path, state_bytes, b""


@pytest.mark.parametrize(
    "result,status,event",
    [(success(), "succeeded", "step_succeeded"), (failure(), "failed", "step_failed")],
)
def test_valid_result_delegates_once_and_returns_exact_result(
    tmp_path: Path, result: object, status: str, event: str
) -> None:
    state_path, events_path, _, _ = setup(tmp_path)
    calls = 0

    def persist(*args: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls
        calls += 1
        return persist_executed_step_transition(*args)  # type: ignore[arg-type]

    actual = persist_executed_result_transition_reentry(
        result, workflow(), state_path, events_path, persistence_function=persist
    )
    assert calls == 1
    assert actual.state_path == state_path
    assert actual.events_path == events_path
    assert actual.state_bytes_written == len(state_path.read_bytes())
    assert actual.event_bytes_appended == len(events_path.read_bytes())
    assert f'"status":"{status}"'.encode() in state_path.read_bytes()
    assert f'"event_type":"{event}"'.encode() in events_path.read_bytes()


def test_returns_injected_phase_30_object_identity(tmp_path: Path) -> None:
    state_path, events_path, _, _ = setup(tmp_path)
    expected: WorkflowExecutionPersistenceResult | None = None

    def persist(*args: object) -> WorkflowExecutionPersistenceResult:
        nonlocal expected
        expected = persist_executed_step_transition(*args)  # type: ignore[arg-type]
        return expected

    actual = persist_executed_result_transition_reentry(
        success(), workflow(), state_path, events_path, persistence_function=persist
    )
    assert actual is expected


@pytest.mark.parametrize(
    ("state", "classification"),
    [
        (running(status="ready"), "state_identity"),
        (running(status="failed", last_failure_category="api_error"), "state_identity"),
        (running(current_step_id="first"), "workflow_identity"),
        (running(completed_step_ids=("step",)), "workflow_identity"),
    ],
)
def test_invalid_persisted_state_rejects_before_delegation(
    tmp_path: Path, state: WorkflowExecutionState, classification: str
) -> None:
    state_path, events_path, before_state, before_events = setup(tmp_path, state)
    calls = 0

    def unexpected(*_args: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ExecutedResultTransitionReentryCompatibilityError) as caught:
        persist_executed_result_transition_reentry(
            success(),
            workflow(),
            state_path,
            events_path,
            persistence_function=unexpected,
        )
    assert caught.value.detail.classification == classification
    assert calls == 0
    assert state_path.read_bytes() == before_state
    assert events_path.read_bytes() == before_events


@pytest.mark.parametrize(
    "result", [success(step_id="other"), replace(success(), invocation_result=object())]
)
def test_invalid_or_stale_result_rejects_before_delegation(
    tmp_path: Path, result: object
) -> None:
    state_path, events_path, _, _ = setup(tmp_path)
    calls = 0

    def unexpected(*_args: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ExecutedResultTransitionReentryCompatibilityError) as caught:
        persist_executed_result_transition_reentry(
            result, workflow(), state_path, events_path, persistence_function=unexpected
        )
    assert caught.value.detail.classification == "result_contract"
    assert calls == 0


@pytest.mark.parametrize("mode", ["none", "state", "events", "both"])
def test_bad_injected_persistence_is_rolled_back(tmp_path: Path, mode: str) -> None:
    state_path, events_path, before_state, before_events = setup(tmp_path)

    def persist(*_args: object) -> WorkflowExecutionPersistenceResult:
        if mode in {"state", "both"}:
            state_path.write_bytes(b"bad")
        if mode in {"events", "both"}:
            events_path.write_bytes(b"bad\n")
        return WorkflowExecutionPersistenceResult(state_path, events_path, 1, 1)

    with pytest.raises(ExecutedResultTransitionReentryCompatibilityError) as caught:
        persist_executed_result_transition_reentry(
            success(), workflow(), state_path, events_path, persistence_function=persist
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert state_path.read_bytes() == before_state
    assert events_path.read_bytes() == before_events


def test_phase_30_error_is_preserved_after_restoration(tmp_path: Path) -> None:
    state_path, events_path, before_state, before_events = setup(tmp_path)
    expected = RuntimeError("safe phase 30 error")

    def persist(*_args: object) -> WorkflowExecutionPersistenceResult:
        state_path.write_bytes(b"bad")
        events_path.write_bytes(b"bad")
        raise expected

    with pytest.raises(RuntimeError) as caught:
        persist_executed_result_transition_reentry(
            success(), workflow(), state_path, events_path, persistence_function=persist
        )
    assert caught.value is expected
    assert state_path.read_bytes() == before_state
    assert events_path.read_bytes() == before_events


def test_missing_or_malformed_event_target_rejects_before_delegation(
    tmp_path: Path,
) -> None:
    state_path, events_path, _, _ = setup(tmp_path)
    events_path.write_bytes(b"not json\n")
    with pytest.raises(ExecutedResultTransitionReentryCompatibilityError) as caught:
        persist_executed_result_transition_reentry(
            success(), workflow(), state_path, events_path
        )
    assert caught.value.detail.classification == "event_target"
