"""Tests for Phase 44 using injected Phase 37 classification fakes only."""

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedTerminalOutcomeClassificationRoutingCompatibilityError,
    WorkflowProgressionDecision,
    route_persisted_terminal_outcome_classification_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": "step",
                    "name": "S",
                    "employee": "employee",
                    "instructions": "do",
                }
            ],
        }
    )


def state(status: str = "succeeded") -> WorkflowExecutionState:
    return WorkflowExecutionState(
        "workflow",
        status,
        "step",
        1,
        "employee",
        ("step",) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]


def event(status: str = "succeeded") -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "workflow",
        "step",
        1,
        "employee",
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "out" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]


def setup(
    tmp_path: Path, status: str = "succeeded"
) -> tuple[Path, Path, WorkflowExecutionPersistenceResult]:
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_bytes = serialize_workflow_execution_state_json(state(status)).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event(status)).encode()
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return (
        state_path,
        events_path,
        WorkflowExecutionPersistenceResult(
            state_path, events_path, len(state_bytes), len(event_bytes)
        ),
    )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_routes_once_and_returns_exact_classification(
    tmp_path: Path, status: str
) -> None:
    state_path, events_path, persisted = setup(tmp_path, status)
    expected = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "workflow",
        "step",
        1,
        "employee",
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    before = (state_path.read_bytes(), events_path.read_bytes())
    calls = 0

    def classify(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert args == (workflow(), state_path, events_path)
        return expected

    assert (
        route_persisted_terminal_outcome_classification_reentry(
            persisted,
            workflow(),
            state_path,
            events_path,
            classification_function=classify,
        )
        is expected
    )
    assert calls == 1 and (state_path.read_bytes(), events_path.read_bytes()) == before


def test_completion_stops_unchanged_without_classification(tmp_path: Path) -> None:
    state_path, events_path, _ = setup(tmp_path)
    decision = WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "step",
        1,
        "employee",
        None,
        None,
        None,
        "last_step_succeeded",
    )
    before = (state_path.read_bytes(), events_path.read_bytes())
    calls = 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_terminal_outcome_classification_reentry(
            decision,
            workflow(),
            state_path,
            events_path,
            classification_function=unexpected,
        )
        is decision
    )
    assert calls == 0 and (state_path.read_bytes(), events_path.read_bytes()) == before


@pytest.mark.parametrize(
    "value", [object(), WorkflowExecutionPersistenceResult(Path("a"), Path("b"), 1, 1)]
)
def test_invalid_result_rejects_before_classification(
    tmp_path: Path, value: object
) -> None:
    state_path, events_path, _ = setup(tmp_path)
    calls = 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedTerminalOutcomeClassificationRoutingCompatibilityError):
        route_persisted_terminal_outcome_classification_reentry(
            value,
            workflow(),
            state_path,
            events_path,
            classification_function=unexpected,
        )
    assert calls == 0


def test_changed_classification_target_is_restored(tmp_path: Path) -> None:
    state_path, events_path, persisted = setup(tmp_path)
    before = (state_path.read_bytes(), events_path.read_bytes())

    def changed(*_: object) -> PersistedExecutionOutcome:
        events_path.write_bytes(b"changed")
        return PersistedExecutionOutcome(
            "persisted_success", "workflow", "step", 1, "employee", None
        )

    with pytest.raises(
        PersistedTerminalOutcomeClassificationRoutingCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_reentry(
            persisted,
            workflow(),
            state_path,
            events_path,
            classification_function=changed,
        )
    assert caught.value.detail.classification == "dependency_error"
    assert (state_path.read_bytes(), events_path.read_bytes()) == before


@pytest.mark.parametrize(
    "invalid",
    [
        lambda state, events, value: WorkflowExecutionPersistenceResult(
            events, state, 1, 1
        ),
        lambda state, events, value: WorkflowExecutionPersistenceResult(
            state, events, True, 1
        ),
        lambda state, events, value: WorkflowExecutionPersistenceResult(
            state, events, 1, True
        ),
        lambda state, events, value: WorkflowExecutionPersistenceResult(
            state, events, 0, 1
        ),
        lambda state, events, value: WorkflowExecutionPersistenceResult(
            state, events, 1, -1
        ),
        lambda state, events, value: WorkflowExecutionPersistenceResult(
            state, events, "1", 1
        ),
        lambda state, events, value: WorkflowExecutionPersistenceResult(
            state, events, 1, 999
        ),
    ],
)
def test_invalid_persistence_result_rejects_before_phase37(
    tmp_path: Path, invalid: object
) -> None:
    state_path, events_path, persisted = setup(tmp_path)
    calls = 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedTerminalOutcomeClassificationRoutingCompatibilityError):
        route_persisted_terminal_outcome_classification_reentry(
            invalid(state_path, events_path, persisted),  # type: ignore[operator]
            workflow(),
            state_path,
            events_path,
            classification_function=unexpected,
        )
    assert calls == 0


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_phase37_mutations_are_compensated_without_retry(
    tmp_path: Path, operation: str, target: str
) -> None:
    state_path, events_path, persisted = setup(tmp_path)
    before = (state_path.read_bytes(), events_path.read_bytes())
    calls = 0

    def mutate(path: Path) -> None:
        if operation == "replace":
            path.write_bytes(b"replacement")
        elif operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        else:
            path.write_bytes(path.read_bytes() + b"append")

    def changed(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            mutate(state_path)
        if target in {"events", "both"}:
            mutate(events_path)
        return PersistedExecutionOutcome(
            "persisted_success", "workflow", "step", 1, "employee", None
        )

    with pytest.raises(
        PersistedTerminalOutcomeClassificationRoutingCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_reentry(
            persisted,
            workflow(),
            state_path,
            events_path,
            classification_function=changed,
        )
    assert caught.value.detail.classification == "dependency_error"
    assert calls == 1 and (state_path.read_bytes(), events_path.read_bytes()) == before


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "step", 1, "employee", "api_error"
        ),
        PersistedExecutionOutcome(
            "persisted_success", "other", "step", 1, "employee", None
        ),
        PersistedExecutionOutcome(
            "persisted_success", "workflow", "other", 1, "employee", None
        ),
        PersistedExecutionOutcome(
            "persisted_success", "workflow", "step", 2, "employee", None
        ),
        PersistedExecutionOutcome(
            "persisted_success", "workflow", "step", 1, "other", None
        ),
    ],
)
def test_invalid_phase37_return_is_rejected_and_compensated(
    tmp_path: Path, returned: object
) -> None:
    state_path, events_path, persisted = setup(tmp_path)
    before = (state_path.read_bytes(), events_path.read_bytes())
    calls = 0

    def classify(*_: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    with pytest.raises(
        PersistedTerminalOutcomeClassificationRoutingCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_reentry(
            persisted,
            workflow(),
            state_path,
            events_path,
            classification_function=classify,
        )
    assert caught.value.detail.classification == "classification_contract"
    assert calls == 1 and (state_path.read_bytes(), events_path.read_bytes()) == before
