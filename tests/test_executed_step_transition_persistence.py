"""Tests for the explicit Phase 30 executed-step persistence boundary."""

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

import ai_office.runtime.executed_step_transition_persistence as persistence_module
from ai_office.engine import (
    ExecutedStepTransitionPersistenceCompatibilityError,
    persist_executed_step_transition,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionResult,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
    WorkflowExecutionTransition,
    transition_workflow_execution_from_step_result,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceError,
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def running_state(**changes: object) -> WorkflowExecutionState:
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
            "openai", "response", "request", "completed", (), "output"
        ),
    }
    values.update(changes)
    return StepRuntimeExecutionSuccess(**values)  # type: ignore[arg-type]


def failure(**changes: object) -> StepRuntimeExecutionFailure:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "step_id": "step",
        "step_index": 2,
        "employee_id": "employee",
        "invocation_result": ModelInvocationFailure(
            "openai", "api_error", "safe failure", "request", None, None, None
        ),
    }
    values.update(changes)
    return StepRuntimeExecutionFailure(**values)  # type: ignore[arg-type]


def targets(tmp_path: Path) -> WorkflowExecutionPersistenceTargets:
    return WorkflowExecutionPersistenceTargets(
        tmp_path / "state.json", tmp_path / "events.jsonl"
    )


def write_running_state(path: Path, state: WorkflowExecutionState) -> bytes:
    contents = serialize_workflow_execution_state_json(state).encode()
    path.write_bytes(contents)
    return contents


@pytest.mark.parametrize(
    ("result", "status", "event_type"),
    [(success(), "succeeded", "step_succeeded"), (failure(), "failed", "step_failed")],
)
def test_exact_runtime_result_transitions_and_persists_one_event(
    tmp_path: Path,
    result: StepRuntimeExecutionResult,
    status: str,
    event_type: str,
) -> None:
    value_targets = targets(tmp_path)
    write_running_state(value_targets.state_path, running_state())
    actual = persist_executed_step_transition(
        result, value_targets.state_path, value_targets.events_path
    )

    history = load_workflow_execution_history(value_targets)
    assert isinstance(actual, WorkflowExecutionPersistenceResult)
    assert actual.state_path == value_targets.state_path
    assert actual.events_path == value_targets.events_path
    assert actual.state_bytes_written == len(value_targets.state_path.read_bytes())
    assert actual.event_bytes_appended == len(
        serialize_runtime_step_event_jsonl(history.events[0]).encode()
    )
    assert history.state.status == status
    assert len(history.events) == 1
    assert history.events[0].event_type == event_type
    assert history.events[0].workflow_id == result.workflow_id


def test_public_contract_has_only_business_inputs() -> None:
    signature = inspect.signature(persist_executed_step_transition)

    assert tuple(signature.parameters) == ("result", "state_path", "events_path")
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in signature.parameters.values()
    )


@pytest.mark.parametrize(
    "removed_keyword", ["transition_function", "persistence_function"]
)
def test_removed_owner_substitution_keywords_are_not_accepted(
    removed_keyword: str,
) -> None:
    with pytest.raises(TypeError):
        persist_executed_step_transition(
            success(), Path("state"), Path("events"), **{removed_keyword: object()}
        )


def test_returns_the_canonical_persistence_result(tmp_path: Path) -> None:
    value_targets = targets(tmp_path)
    write_running_state(value_targets.state_path, running_state())
    actual = persist_executed_step_transition(
        success(), value_targets.state_path, value_targets.events_path
    )
    assert actual == WorkflowExecutionPersistenceResult(
        value_targets.state_path,
        value_targets.events_path,
        len(value_targets.state_path.read_bytes()),
        actual.event_bytes_appended,
    )


def test_rejects_malformed_persistence_result_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value_targets = targets(tmp_path)
    original = write_running_state(value_targets.state_path, running_state())
    malformed = WorkflowExecutionPersistenceResult(
        value_targets.state_path, value_targets.events_path, 0, 1
    )
    monkeypatch.setattr(
        persistence_module,
        "persist_workflow_execution_transition",
        lambda *args, **kwargs: malformed,
    )

    with pytest.raises(ExecutedStepTransitionPersistenceCompatibilityError) as error:
        persist_executed_step_transition(
            success(), value_targets.state_path, value_targets.events_path
        )
    assert error.value.detail.classification == "persistence_contract"
    assert value_targets.state_path.read_bytes() == original
    assert not value_targets.events_path.exists()


@pytest.mark.parametrize(
    "result",
    [
        success(workflow_id="other"),
        success(step_id="other"),
        success(step_index=3),
        success(employee_id="other"),
    ],
    ids=["workflow", "step", "index", "employee"],
)
def test_result_identity_mismatch_rejects_without_mutation(
    tmp_path: Path, result: StepRuntimeExecutionSuccess
) -> None:
    value_targets = targets(tmp_path)
    original = write_running_state(value_targets.state_path, running_state())

    with pytest.raises(ExecutedStepTransitionPersistenceCompatibilityError) as error:
        persist_executed_step_transition(
            result, value_targets.state_path, value_targets.events_path
        )
    assert error.value.detail.classification == "state_identity"
    assert value_targets.state_path.read_bytes() == original
    assert not value_targets.events_path.exists()


@pytest.mark.parametrize(
    ("state", "classification"),
    [
        (running_state(status="ready"), "state_status"),
        (running_state(status="succeeded"), "state_status"),
        (
            running_state(status="failed", last_failure_category="api_error"),
            "state_status",
        ),
        (running_state(last_failure_category="api_error"), "state_identity"),
    ],
)
def test_non_running_persisted_state_rejects_without_mutation(
    tmp_path: Path, state: WorkflowExecutionState, classification: str
) -> None:
    value_targets = targets(tmp_path)
    original = write_running_state(value_targets.state_path, state)

    with pytest.raises(ExecutedStepTransitionPersistenceCompatibilityError) as error:
        persist_executed_step_transition(
            success(), value_targets.state_path, value_targets.events_path
        )
    assert error.value.detail.classification == classification
    assert value_targets.state_path.read_bytes() == original
    assert not value_targets.events_path.exists()


@pytest.mark.parametrize(
    ("contents", "classification"),
    [(None, "state_target"), (b"not json", "state_data"), (b"\xff", "state_data")],
)
def test_missing_or_malformed_state_rejects_safely(
    tmp_path: Path, contents: bytes | None, classification: str
) -> None:
    value_targets = targets(tmp_path)
    if contents is not None:
        value_targets.state_path.write_bytes(contents)
    with pytest.raises(ExecutedStepTransitionPersistenceCompatibilityError) as error:
        persist_executed_step_transition(
            success(), value_targets.state_path, value_targets.events_path
        )
    assert error.value.detail.classification == classification
    assert str(value_targets.state_path) not in str(error.value)
    assert not value_targets.events_path.exists()


@pytest.mark.parametrize(
    ("result", "state_path", "events_path", "classification"),
    [
        (object(), Path("state"), Path("events"), "result_type"),
        (success(), "state", Path("events"), "state_target"),
        (success(), Path("state"), "events", "event_target"),
        (success(), Path("same"), Path("same"), "target_conflict"),
    ],
)
def test_invalid_explicit_inputs_reject_before_state_load(
    result: object, state_path: object, events_path: object, classification: str
) -> None:
    with pytest.raises(ExecutedStepTransitionPersistenceCompatibilityError) as error:
        persist_executed_step_transition(result, state_path, events_path)
    assert error.value.detail.classification == classification
    assert "state" not in str(error.value)


def test_canonical_transition_contract_rejects_malformed_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value_targets = targets(tmp_path)
    original = write_running_state(value_targets.state_path, running_state())

    def malformed_transition(
        state: WorkflowExecutionState, result: StepRuntimeExecutionResult
    ) -> WorkflowExecutionTransition:
        value = transition_workflow_execution_from_step_result(state, result)
        return replace(value, event=replace(value.event, step_id="other"))

    monkeypatch.setattr(
        persistence_module,
        "transition_workflow_execution_from_step_result",
        malformed_transition,
    )

    with pytest.raises(ExecutedStepTransitionPersistenceCompatibilityError) as error:
        persist_executed_step_transition(
            success(), value_targets.state_path, value_targets.events_path
        )
    assert error.value.detail.classification == "transition_contract"
    assert value_targets.state_path.read_bytes() == original
    assert not value_targets.events_path.exists()


@pytest.mark.parametrize(
    ("result", "event_changes"),
    [
        (success(), {"provider": "other"}),
        (success(), {"response_id": "other"}),
        (success(), {"request_id": "other"}),
        (success(), {"output_text": "other"}),
        (failure(), {"provider": "other"}),
        (failure(), {"request_id": "other"}),
        (failure(), {"message": "other"}),
    ],
    ids=[
        "success-provider",
        "success-response-id",
        "success-request-id",
        "success-output-text",
        "failure-provider",
        "failure-request-id",
        "failure-message",
    ],
)
def test_runtime_event_payload_mismatch_rejects_before_persistence(
    tmp_path: Path,
    result: StepRuntimeExecutionResult,
    event_changes: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value_targets = targets(tmp_path)
    original_state = write_running_state(value_targets.state_path, running_state())
    original_events = b'{"old":true}\n'
    value_targets.events_path.write_bytes(original_events)

    def malformed_transition(
        state: WorkflowExecutionState, runtime_result: StepRuntimeExecutionResult
    ) -> WorkflowExecutionTransition:
        value = transition_workflow_execution_from_step_result(state, runtime_result)
        return replace(value, event=replace(value.event, **event_changes))

    monkeypatch.setattr(
        persistence_module,
        "transition_workflow_execution_from_step_result",
        malformed_transition,
    )

    with pytest.raises(ExecutedStepTransitionPersistenceCompatibilityError) as error:
        persist_executed_step_transition(
            result, value_targets.state_path, value_targets.events_path
        )
    assert error.value.detail.classification == "transition_contract"
    assert value_targets.state_path.read_bytes() == original_state
    assert value_targets.events_path.read_bytes() == original_events


def test_canonical_persistence_errors_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value_targets = targets(tmp_path)
    original = write_running_state(value_targets.state_path, running_state())
    expected = WorkflowExecutionPersistenceError("safe")
    calls = 0

    def fail(*_args: object, **_kwargs: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls
        calls += 1
        raise expected

    monkeypatch.setattr(
        persistence_module, "persist_workflow_execution_transition", fail
    )

    with pytest.raises(WorkflowExecutionPersistenceError) as error:
        persist_executed_step_transition(
            success(), value_targets.state_path, value_targets.events_path
        )
    assert error.value is expected
    assert calls == 1
    assert value_targets.state_path.read_bytes() == original
    assert not value_targets.events_path.exists()
