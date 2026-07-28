"""Focused Phase 58 contract tests using injected Phase 51 fakes only."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedTerminalOutcomeClassificationBridgeCompatibilityError,
    PersistedTerminalOutcomeClassificationBridgeError,
    PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError,
    WorkflowProgressionDecision,
    route_persisted_terminal_outcome_classification_phase_bridge_reentry,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class PersistenceSubclass(WorkflowExecutionPersistenceResult):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


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


def setup(
    tmp_path: Path, status: str = "succeeded"
) -> tuple[Path, Path, WorkflowExecutionPersistenceResult, bytes, bytes]:
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state = WorkflowExecutionState(
        "workflow",
        status,
        "step",
        1,
        "employee",
        ("step",) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    event = RuntimeStepEvent(
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
    state_bytes = serialize_workflow_execution_state_json(state).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return (
        state_path,
        events_path,
        WorkflowExecutionPersistenceResult(
            state_path, events_path, len(state_bytes), len(event_bytes)
        ),
        state_bytes,
        event_bytes,
    )


def test_persistence_result_delegates_once_with_identity_and_returns_identity(
    tmp_path: Path,
) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)
    expected = PersistedExecutionOutcome(
        "persisted_success", "workflow", "step", 1, "employee", None
    )
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PersistedExecutionOutcome:
        calls.append(args)
        assert args == (persisted, workflow(), state, events)
        return expected

    returned = route_persisted_terminal_outcome_classification_phase_bridge_reentry(
        persisted, workflow(), state, events, phase51_function=fake
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_terminal_stop_routes_return_supplied_object_without_phase51(
    tmp_path: Path, status: str
) -> None:
    state, events, _, before_state, before_events = setup(tmp_path, status)
    supplied: object = (
        WorkflowProgressionDecision(
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
        if status == "succeeded"
        else PersistedExecutionOutcome(
            "persisted_failure", "workflow", "step", 1, "employee", "api_error"
        )
    )
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            supplied, workflow(), state, events, phase51_function=unexpected
        )
        is supplied
    )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    "value",
    [
        object(),
        OutcomeSubclass(
            "persisted_failure", "workflow", "step", 1, "employee", "api_error"
        ),
        DecisionSubclass(
            "workflow_complete",
            "workflow",
            "step",
            1,
            "employee",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
    ],
)
def test_exact_top_level_types_are_required(tmp_path: Path, value: object) -> None:
    state, events, _, before_state, before_events = setup(tmp_path)
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            value, workflow(), state, events
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_persisted_success_is_not_a_stop_input(tmp_path: Path) -> None:
    state, events, _, _, _ = setup(tmp_path)
    supplied = PersistedExecutionOutcome(
        "persisted_success", "workflow", "step", 1, "employee", None
    )
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            supplied, workflow(), state, events
        )
    assert caught.value.detail.classification == "failure_contract"


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
@pytest.mark.parametrize("count", [True, 0, -1, 1.0, "1", None, object()])
def test_invalid_counts_stop_before_phase51(
    tmp_path: Path, field: str, count: object
) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)
    invalid = replace(persisted, **{field: count})
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            invalid,
            workflow(),
            state,
            events,
            phase51_function=lambda *_: (_ for _ in ()).throw(AssertionError),
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_malformed_dependency_is_compensated_without_retry(tmp_path: Path) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)
    calls = 0

    def malformed(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed")
        events.write_bytes(b"changed\n")
        return object()

    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            persisted, workflow(), state, events, phase51_function=malformed
        )
    assert caught.value.detail.classification == "classification_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


def test_safe_phase51_error_identity_is_preserved_after_rollback(
    tmp_path: Path,
) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)
    expected = PersistedTerminalOutcomeClassificationBridgeCompatibilityError(
        "terminal_contract"
    )

    def raises(*_: object) -> object:
        state.write_bytes(b"changed")
        raise expected

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            persisted, workflow(), state, events, phase51_function=raises
        )
    assert caught.value is expected
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_unexpected_phase51_error_is_sanitized_and_targets_restored(
    tmp_path: Path,
) -> None:
    state, events, persisted, before_state, before_events = setup(tmp_path)

    def raises(*_: object) -> object:
        state.write_bytes(b"secret provider response")
        raise RuntimeError("path=/private/token=secret")

    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            persisted, workflow(), state, events, phase51_function=raises
        )
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
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


def persisted_failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_failure", "workflow", "step", 1, "employee", "api_error"
    )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_valid_success_and_failure_persistence_results_are_single_read_only_calls(
    tmp_path: Path, status: str
) -> None:
    state, events, result, before_state, before_events = setup(tmp_path, status)
    expected = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "workflow",
        "step",
        1,
        "employee",
        None if status == "succeeded" else "api_error",
    )
    calls: list[tuple[object, ...]] = []

    def dependency(*args: object) -> object:
        calls.append(args)
        return expected

    returned = route_persisted_terminal_outcome_classification_phase_bridge_reentry(
        result, workflow(), state, events, phase51_function=dependency
    )
    assert returned is expected
    assert len(calls) == 1
    assert calls[0][0] is result and calls[0][2] is state and calls[0][3] is events
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "value",
    [
        object(),
        PersistenceSubclass(Path("s"), Path("e"), 1, 1),
        OutcomeSubclass(
            "persisted_failure", "workflow", "step", 1, "employee", "api_error"
        ),
        DecisionSubclass(
            "workflow_complete",
            "workflow",
            "step",
            1,
            "employee",
            None,
            None,
            None,
            "last_step_succeeded",
        ),
        SimpleNamespace(outcome="persisted_failure", workflow_id="workflow"),
    ],
)
def test_wrong_subclass_and_attribute_compatible_results_make_zero_calls(
    tmp_path: Path, value: object
) -> None:
    state, events, _, before_state, before_events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            value, workflow(), state, events, phase51_function=dependency
        )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    "supplied",
    [
        object(),
        WorkflowSubclass.model_validate(workflow().model_dump()),
        SimpleNamespace(id="workflow"),
    ],
)
def test_wrong_workflow_types_make_zero_calls(tmp_path: Path, supplied: object) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            result, supplied, state, events, phase51_function=dependency
        )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    ("state_value", "events_value", "classification"),
    [
        (None, Path("events"), "state_target"),
        ("valid", None, "event_target"),
        (Path("missing"), Path("events"), "state_target"),
        ("valid", Path("missing"), "event_target"),
    ],
)
def test_wrong_and_missing_targets_are_separately_rejected(
    tmp_path: Path, state_value: object, events_value: object, classification: str
) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    actual_state = state if state_value == "valid" else state_value
    actual_events = events if events_value == "valid" else events_value
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            result, workflow(), actual_state, actual_events
        )
    assert caught.value.detail.classification == classification
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_directory_conflict_and_non_callable_dependency_are_rejected(
    tmp_path: Path,
) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    directory = tmp_path / "directory"
    directory.mkdir()
    for state_value, events_value, classification in (
        (directory, events, "state_target"),
        (state, state, "target_conflict"),
    ):
        with pytest.raises(
            PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
        ) as caught:
            route_persisted_terminal_outcome_classification_phase_bridge_reentry(
                result, workflow(), state_value, events_value
            )
        assert caught.value.detail.classification == classification
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            result, workflow(), state, events, phase51_function=object()
        )
    assert caught.value.detail.classification == "classification_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("which", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_target_operation_failures_are_separately_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    which: str,
    operation: str,
) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    target = state if which == "state" else events
    original = getattr(Path, operation)

    def fail(path: Path) -> object:
        if path == target:
            raise OSError("path=/secret")
        return original(path)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            result, workflow(), state, events
        )
    monkeypatch.undo()
    assert caught.value.detail.classification == (
        "state_target" if which == "state" else "event_target"
    )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("field", ["state_path", "events_path"])
def test_persistence_result_wrong_path_is_rejected(tmp_path: Path, field: str) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    invalid = replace(result, **{field: tmp_path / "other"})
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            invalid, workflow(), state, events
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
@pytest.mark.parametrize("count", [True, 0, -1, 1.0, "1", None, object()])
def test_every_invalid_count_type_is_rejected(
    tmp_path: Path, field: str, count: object
) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            replace(result, **{field: count}), workflow(), state, events
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("field", ["state_bytes_written", "event_bytes_appended"])
def test_positive_count_mismatch_is_rejected(tmp_path: Path, field: str) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    invalid = replace(result, **{field: getattr(result, field) + 1})
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            invalid, workflow(), state, events
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "field",
    [
        "decision",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "reason",
    ],
)
def test_malformed_completion_is_rejected(tmp_path: Path, field: str) -> None:
    state, events, _, before_state, before_events = setup(tmp_path)
    values = {
        "decision": "wrong",
        "workflow_id": "wrong",
        "current_step_id": "wrong",
        "current_step_index": 99,
        "current_employee_id": "wrong",
        "reason": "wrong",
    }
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            replace(completion(), **{field: values[field]}), workflow(), state, events
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "field",
    [
        "outcome",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "failure_category",
    ],
)
def test_malformed_persisted_failure_is_rejected(tmp_path: Path, field: str) -> None:
    state, events, _, before_state, before_events = setup(tmp_path, "failed")
    values = {
        "outcome": "persisted_success",
        "workflow_id": "wrong",
        "current_step_id": "wrong",
        "current_step_index": 99,
        "current_employee_id": "wrong",
        "failure_category": "wrong",
    }
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            replace(persisted_failure(), **{field: values[field]}),
            workflow(),
            state,
            events,
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_terminal_history_mismatch_and_direct_success_stop_before_dependency(
    tmp_path: Path,
) -> None:
    state, events, _, before_state, before_events = setup(tmp_path)
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            replace(completion(), current_step_id="wrong"), workflow(), state, events
        )
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ):
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            PersistedExecutionOutcome(
                "persisted_success", "workflow", "step", 1, "employee", None
            ),
            workflow(),
            state,
            events,
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "value",
    [
        object(),
        OutcomeSubclass("persisted_success", "workflow", "step", 1, "employee", None),
        SimpleNamespace(outcome="persisted_success"),
    ],
)
def test_phase51_wrong_return_types_are_rejected_without_retry(
    tmp_path: Path, value: object
) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return value

    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            result, workflow(), state, events, phase51_function=dependency
        )
    assert caught.value.detail.classification == "classification_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    "field",
    [
        "outcome",
        "workflow_id",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "failure_category",
    ],
)
def test_phase51_return_identity_fields_and_categories_are_validated(
    tmp_path: Path, field: str
) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    values = {
        "outcome": "persisted_failure",
        "workflow_id": "wrong",
        "current_step_id": "wrong",
        "current_step_index": 99,
        "current_employee_id": "wrong",
        "failure_category": "wrong",
    }
    malformed = replace(
        PersistedExecutionOutcome(
            "persisted_success", "workflow", "step", 1, "employee", None
        ),
        **{field: values[field]},
    )
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            result, workflow(), state, events, phase51_function=lambda *_: malformed
        )
    assert caught.value.detail.classification == "classification_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


MUTATIONS = [
    "none",
    "state-replace",
    "state-delete",
    "state-truncate",
    "state-append",
    "events-replace",
    "events-delete",
    "events-truncate",
    "events-append",
    "both",
]


def apply_mutation(
    mutation: str, state: Path, events: Path, state_bytes: bytes, event_bytes: bytes
) -> None:
    if mutation.startswith("state") or mutation == "both":
        if mutation == "state-delete":
            state.unlink()
        elif mutation == "state-truncate":
            state.write_bytes(b"")
        elif mutation == "state-append":
            state.write_bytes(state_bytes + b"raw-secret")
        else:
            state.write_bytes(b"changed-state")
    if mutation.startswith("events") or mutation == "both":
        if mutation == "events-delete":
            events.unlink()
        elif mutation == "events-truncate":
            events.write_bytes(b"")
        elif mutation == "events-append":
            events.write_bytes(event_bytes + b"raw-secret")
        else:
            events.write_bytes(b"changed-events")


@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
@pytest.mark.parametrize("mutation", MUTATIONS)
def test_dependency_error_return_and_mutation_matrix_is_compensated(
    tmp_path: Path, kind: str, mutation: str
) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    calls = 0
    safe_error = PersistedTerminalOutcomeClassificationBridgeCompatibilityError(
        "terminal_contract"
    )

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        apply_mutation(mutation, state, events, before_state, before_events)
        if kind == "safe":
            raise safe_error
        if kind == "unexpected":
            raise RuntimeError(
                "path=/secret workflow=workflow employee=employee "
                "provider=response output=raw traceback"
            )
        return object()

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeError
        if kind == "safe"
        else PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            result, workflow(), state, events, phase51_function=dependency
        )
    if kind == "safe":
        assert caught.value is safe_error
    elif kind == "unexpected":
        assert caught.value.detail.classification == "dependency_error"
    else:
        assert caught.value.detail.classification == "classification_contract"
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
    assert "raw-secret" not in str(caught.value)
    assert "/secret" not in str(caught.value)


@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_overrides_all_dependency_outcomes_and_attempts_both_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    failed_target: str,
) -> None:
    state, events, result, before_state, before_events = setup(tmp_path)
    attempts: list[Path] = []
    original_write = Path.write_bytes
    safe_error = PersistedTerminalOutcomeClassificationBridgeCompatibilityError(
        "terminal_contract"
    )

    def dependency(*_: object) -> object:
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        if kind == "safe":
            raise safe_error
        if kind == "unexpected":
            raise RuntimeError("traceback=/secret")
        return object()

    def write(path: Path, contents: bytes) -> int:
        if contents in (before_state, before_events):
            attempts.append(path)
            if (
                failed_target == "both"
                or (failed_target == "state" and path == state)
                or (failed_target == "events" and path == events)
            ):
                raise OSError("rollback failure")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", write)
    with pytest.raises(
        PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_phase_bridge_reentry(
            result, workflow(), state, events, phase51_function=dependency
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert state in attempts and events in attempts
