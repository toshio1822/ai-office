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
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.runtime.executed_step_transition_persistence import (
    persist_executed_step_transition,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
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


def first_running() -> WorkflowExecutionState:
    return WorkflowExecutionState("workflow", "running", "first", 1, "one", (), None)


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


def first_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "workflow",
        "first",
        1,
        "one",
        ModelInvocationSuccess(
            "openai", "response", "request", "completed", ("output",), "output"
        ),
    )


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
    tmp_path: Path,
    state: WorkflowExecutionState | None = None,
    event_bytes: bytes | None = None,
) -> tuple[Path, Path, bytes, bytes]:
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_bytes = serialize_workflow_execution_state_json(state or running()).encode()
    if event_bytes is None:
        event_bytes = (
            serialize_runtime_step_event_jsonl(prior_success_event()).encode()
            + serialize_runtime_step_event_jsonl(prior_success_event()).encode()
        )
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def prior_success_event(**changes: object) -> RuntimeStepEvent:
    values: dict[str, object] = {
        "event_type": "step_succeeded",
        "workflow_id": "workflow",
        "step_id": "first",
        "step_index": 1,
        "employee_id": "one",
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


def assert_history_rejected_before_delegation(
    tmp_path: Path, event_bytes: bytes
) -> None:
    state_path, events_path, state_bytes, _ = setup(tmp_path)
    events_path.write_bytes(event_bytes)
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
    assert calls == 0
    assert state_path.read_bytes() == state_bytes
    assert events_path.read_bytes() == event_bytes
    message = str(caught.value)
    assert "workflow" not in message
    assert "response" not in message
    assert str(state_path) not in message


@pytest.mark.parametrize(
    "event_bytes",
    [
        serialize_runtime_step_event_jsonl(
            prior_success_event(workflow_id="other-workflow")
        ).encode(),
        serialize_runtime_step_event_jsonl(
            prior_success_event(step_id="step")
        ).encode(),
        serialize_runtime_step_event_jsonl(prior_success_event(step_index=2)).encode(),
        serialize_runtime_step_event_jsonl(
            prior_success_event(employee_id="other-employee")
        ).encode(),
        serialize_runtime_step_event_jsonl(
            prior_success_event(step_id="first")
        ).encode(),
        serialize_runtime_step_event_jsonl(
            prior_success_event(step_id="step", step_index=2, employee_id="employee")
        ).encode(),
        serialize_runtime_step_event_jsonl(
            prior_success_event(
                event_type="step_failed",
                step_id="step",
                step_index=2,
                employee_id="employee",
                next_status="failed",
                failure_category="api_error",
                response_id=None,
                output_text=None,
                message="safe",
            )
        ).encode(),
        (
            serialize_runtime_step_event_jsonl(prior_success_event()).encode()
            + serialize_runtime_step_event_jsonl(
                prior_success_event(
                    step_id="step", step_index=2, employee_id="employee"
                )
            ).encode()
        ),
    ],
    ids=[
        "wrong-workflow",
        "wrong-step",
        "wrong-index",
        "wrong-employee",
        "completed-history-mismatch",
        "running-step-succeeded",
        "running-step-failed",
        "invalid-event-order",
    ],
)
def test_strict_history_rejects_before_phase_30(
    tmp_path: Path, event_bytes: bytes
) -> None:
    assert_history_rejected_before_delegation(tmp_path, event_bytes)


def test_first_step_with_empty_history_delegates_once(tmp_path: Path) -> None:
    state_path, events_path, _, _ = setup(tmp_path, first_running(), b"")
    calls = 0

    def persist(*args: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls
        calls += 1
        return persist_executed_step_transition(*args)  # type: ignore[arg-type]

    persist_executed_result_transition_reentry(
        first_success(),
        workflow(),
        state_path,
        events_path,
        persistence_function=persist,
    )
    assert calls == 1


def test_later_step_with_empty_history_rejects_before_delegation(
    tmp_path: Path,
) -> None:
    state_path, events_path, state_bytes, event_bytes = setup(tmp_path, running(), b"")
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
    assert calls == 0
    assert state_path.read_bytes() == state_bytes
    assert events_path.read_bytes() == event_bytes
    assert str(state_path) not in str(caught.value)


@pytest.mark.parametrize(
    "event_bytes",
    [
        serialize_runtime_step_event_jsonl(prior_success_event()).encode(),
        (
            serialize_runtime_step_event_jsonl(prior_success_event()).encode()
            + serialize_runtime_step_event_jsonl(prior_success_event()).encode()
            + serialize_runtime_step_event_jsonl(prior_success_event()).encode()
        ),
    ],
    ids=["too-few", "too-many"],
)
def test_completed_events_must_match_count_before_delegation(
    tmp_path: Path, event_bytes: bytes
) -> None:
    assert_history_rejected_before_delegation(tmp_path, event_bytes)


def test_step_two_with_matching_duplicate_history_delegates_once(
    tmp_path: Path,
) -> None:
    state_path, events_path, _, previous_events = setup(tmp_path)
    calls = 0

    def persist(*args: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls
        calls += 1
        return persist_executed_step_transition(*args)  # type: ignore[arg-type]

    persist_executed_result_transition_reentry(
        success(), workflow(), state_path, events_path, persistence_function=persist
    )
    assert calls == 1


@pytest.mark.parametrize(
    "result,status,event",
    [(success(), "succeeded", "step_succeeded"), (failure(), "failed", "step_failed")],
)
def test_valid_result_delegates_once_and_returns_exact_result(
    tmp_path: Path, result: object, status: str, event: str
) -> None:
    state_path, events_path, _, previous_events = setup(tmp_path)
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
    assert actual.event_bytes_appended == len(events_path.read_bytes()) - len(
        previous_events
    )
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
    assert caught.value.detail.classification == "state_data"
