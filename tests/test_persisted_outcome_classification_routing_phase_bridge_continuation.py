"""Focused Phase 72 boundary tests."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError,
    PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError,
    PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError,
    WorkflowProgressionDecision,
    route_persisted_outcome_classification_routing_phase_bridge_continuation,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class ResultSubclass(WorkflowExecutionPersistenceResult):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class IntSubclass(int):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "x"},
            ],
        }
    )


def setup(
    tmp_path: Path, status: str = "succeeded"
) -> tuple[
    Path, Path, WorkflowExecutionPersistenceResult, WorkflowDefinition, bytes, bytes
]:
    definition = workflow()
    state_model = WorkflowExecutionState(
        "w",
        status,
        "one",
        1,
        "a",
        ("one",) if status == "succeeded" else (),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "w",
        "one",
        1,
        "a",
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "out" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]
    state_bytes = serialize_workflow_execution_state_json(state_model).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    state.write_bytes(state_bytes)
    events.write_bytes(event_bytes)
    return (
        state,
        events,
        WorkflowExecutionPersistenceResult(
            state, events, len(state_bytes), len(event_bytes)
        ),
        definition,
        state_bytes,
        event_bytes,
    )


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete", "w", "one", 1, "a", None, None, None, "last_step_succeeded"
    )


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_failure", "w", "one", 1, "a", "api_error"
    )


def rewrite_json(path: Path, field: str, value: object) -> None:
    data = json.loads(path.read_text())
    data[field] = value
    path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False) + "\n")


def result_with_current_sizes(
    state: Path, events: Path
) -> WorkflowExecutionPersistenceResult:
    return WorkflowExecutionPersistenceResult(
        state, events, state.stat().st_size, events.stat().st_size
    )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_valid_result_delegates_once_with_exact_identity_and_maps_outcome(
    tmp_path: Path, status: str
) -> None:
    state, events, result, definition, before_state, before_events = setup(
        tmp_path, status
    )
    expected = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "w",
        "one",
        1,
        "a",
        None if status == "succeeded" else "api_error",
    )
    calls: list[tuple[object, ...]] = []

    def phase65(*args: object) -> PersistedExecutionOutcome:
        calls.append(args)
        assert args == (result, definition, state, events)
        return expected

    returned = route_persisted_outcome_classification_routing_phase_bridge_continuation(
        result, definition, state, events, phase65_function=phase65
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("value", [completion(), failure()])
def test_terminal_stop_routes_do_not_call_phase65(
    tmp_path: Path, value: object
) -> None:
    state, events, _, definition, before_state, before_events = setup(
        tmp_path,
        "succeeded" if isinstance(value, WorkflowProgressionDecision) else "failed",
    )
    calls = 0

    def phase65(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            value, definition, state, events, phase65_function=phase65
        )
        is value
    )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "prepare_next_step"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("next_step_id", "next"),
        ("next_step_index", 2),
        ("next_employee_id", "b"),
        ("reason", "other"),
    ],
)
def test_workflow_complete_stop_route_checks_each_field(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, _, definition, _, _ = setup(tmp_path, "succeeded")
    supplied = replace(completion(), **{field: value})
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            supplied, definition, state, events
        )
    assert caught.value.detail.classification == "completion_contract"


def test_workflow_complete_stop_route_checks_terminal_match(tmp_path: Path) -> None:
    state, events, _, definition, _, _ = setup(tmp_path, "succeeded")
    rewrite_json(state, "workflow_id", "other")
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            completion(), definition, state, events
        )
    assert caught.value.detail.classification == "terminal_contract"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "persisted_success"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", "timeout"),
    ],
)
def test_persisted_failure_stop_route_checks_each_field(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, _, definition, _, _ = setup(tmp_path, "failed")
    supplied = replace(failure(), **{field: value})
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            supplied, definition, state, events
        )
    assert caught.value.detail.classification == "failure_contract"


def test_persisted_failure_stop_route_checks_terminal_match(tmp_path: Path) -> None:
    state, events, _, definition, _, _ = setup(tmp_path, "failed")
    rewrite_json(state, "last_failure_category", "timeout")
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            failure(), definition, state, events
        )
    assert caught.value.detail.classification == "terminal_contract"


def test_exact_input_types_and_target_identity_are_checked_before_dependency(
    tmp_path: Path,
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path)
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            ResultSubclass(
                state, events, result.state_bytes_written, result.event_bytes_appended
            ),
            definition,
            state,
            events,
            phase65_function=lambda *_: object(),
        )
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result,
            SimpleNamespace(id="w"),
            state,
            events,
            phase65_function=lambda *_: object(),
        )
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result,
            definition,
            Path(str(state)),
            events,
            phase65_function=lambda *_: object(),
        )
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result,
            definition,
            state,
            Path(str(events)),
            phase65_function=lambda *_: object(),
        )
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, state, phase65_function=lambda *_: object()
        )


def test_persisted_success_is_not_a_direct_stop_route(tmp_path: Path) -> None:
    state, events, _, definition, _, _ = setup(tmp_path, "failed")
    supplied = PersistedExecutionOutcome("persisted_success", "w", "one", 1, "a", None)
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            supplied, definition, state, events
        )
    assert caught.value.detail.classification == "failure_contract"


@pytest.mark.parametrize("which", ["state", "events"])
def test_persistence_result_path_identity_is_required(
    tmp_path: Path, which: str
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path)
    supplied_state = Path(str(state)) if which == "state" else state
    supplied_events = Path(str(events)) if which == "events" else events
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            WorkflowExecutionPersistenceResult(
                state, events, result.state_bytes_written, result.event_bytes_appended
            ),
            definition,
            supplied_state,
            supplied_events,
            phase65_function=lambda *_: object(),
        )
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state_bytes_written", True),
        ("state_bytes_written", 0),
        ("state_bytes_written", -1),
        ("state_bytes_written", IntSubclass(1)),
        ("event_bytes_appended", True),
        ("event_bytes_appended", 0),
        ("event_bytes_appended", -1),
        ("event_bytes_appended", IntSubclass(1)),
    ],
)
def test_persistence_result_every_byte_count_type_and_value_is_checked(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path)
    bad = replace(result, **{field: value})
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            bad, definition, state, events
        )
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_state_byte_count_mismatch_is_checked_independently(
    tmp_path: Path, status: str
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path, status)
    bad = replace(result, state_bytes_written=result.state_bytes_written + 1)
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            bad, definition, state, events
        )
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_event_byte_count_mismatch_is_checked_independently(
    tmp_path: Path, status: str
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path, status)
    bad = replace(result, event_bytes_appended=result.event_bytes_appended + 1)
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            bad, definition, state, events
        )
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize(
    ("field", "succeeded_value", "failed_value"),
    [
        ("workflow_id", "other", "other"),
        ("status", "failed", "succeeded"),
        ("current_step_id", "other", "other"),
        ("current_step_index", 2, 2),
        ("current_employee_id", "other", "other"),
        ("completed_step_ids", [], ["one"]),
        ("last_failure_category", "api_error", None),
    ],
)
def test_succeeded_and_failed_terminal_state_fields_are_each_checked(
    tmp_path: Path, field: str, succeeded_value: object, failed_value: object
) -> None:
    for status, value in (("succeeded", succeeded_value), ("failed", failed_value)):
        case_path = tmp_path / status
        case_path.mkdir()
        state, events, _, definition, _, _ = setup(case_path, status)
        rewrite_json(state, field, value)
        bad = result_with_current_sizes(state, events)
        with pytest.raises(
            PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
        ):
            route_persisted_outcome_classification_routing_phase_bridge_continuation(
                bad, definition, state, events
            )


@pytest.mark.parametrize(
    "field",
    [
        "event_type",
        "workflow_id",
        "step_id",
        "step_index",
        "employee_id",
        "previous_status",
        "next_status",
        "provider",
        "failure_category",
        "response_id",
        "request_id",
        "output_text",
        "message",
    ],
)
def test_succeeded_and_failed_terminal_event_fields_are_each_checked(
    tmp_path: Path, field: str
) -> None:
    values = {
        "workflow_id": "other",
        "step_id": "other",
        "step_index": 2,
        "employee_id": "other",
        "previous_status": "ready",
        "provider": True,
        "response_id": 1,
        "request_id": True,
        "output_text": 1,
        "message": 1,
    }
    for status in ("succeeded", "failed"):
        case_path = tmp_path / status
        case_path.mkdir()
        state, events, _, definition, _, _ = setup(case_path, status)
        status_values = {
            "event_type": "step_failed" if status == "succeeded" else "step_succeeded",
            "next_status": "failed" if status == "succeeded" else "succeeded",
            "failure_category": "api_error" if status == "succeeded" else None,
            **values,
        }
        rewrite_json(events, field, status_values[field])
        bad = result_with_current_sizes(state, events)
        with pytest.raises(
            PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
        ):
            route_persisted_outcome_classification_routing_phase_bridge_continuation(
                bad, definition, state, events
            )


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_non_regular_targets_are_rejected(
    tmp_path: Path, kind: str
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path)
    target = state if kind == "missing" else events
    if kind == "missing":
        target.unlink()
    else:
        events.unlink()
        events.mkdir()
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events
        )
    assert caught.value.detail.classification == (
        "state_target" if target == state else "event_target"
    )


@pytest.mark.parametrize("which", ["state", "events"])
def test_state_and_event_oserrors_are_classified_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path)
    target = state if which == "state" else events
    original = Path.is_file

    def fail(path: Path) -> bool:
        if path == target:
            raise OSError("unreadable")
        return original(path)

    monkeypatch.setattr(Path, "is_file", fail)
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events
        )
    assert caught.value.detail.classification == (
        "state_target" if which == "state" else "event_target"
    )


@pytest.mark.parametrize("which", ["state", "events"])
def test_capture_oserrors_are_classified_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path)
    target = state if which == "state" else events
    original = Path.read_bytes

    def fail(path: Path) -> bytes:
        if path == target:
            raise OSError("unreadable")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", fail)
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events
        )
    assert caught.value.detail.classification == (
        "state_target" if which == "state" else "event_target"
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
def test_every_persisted_outcome_field_is_checked(tmp_path: Path, field: str) -> None:
    state, events, result, definition, _, _ = setup(tmp_path, "failed")
    expected = failure()
    values = {
        "outcome": "persisted_success",
        "workflow_id": "other",
        "current_step_id": "other",
        "current_step_index": 2,
        "current_employee_id": "other",
        "failure_category": "timeout",
    }
    bad = replace(expected, **{field: values[field]})
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events, phase65_function=lambda *_: bad
        )
    assert caught.value.detail.classification == "outcome_contract"


@pytest.mark.parametrize(
    "field",
    [
        "outcome",
        "workflow_id",
        "current_step_id",
        "current_employee_id",
        "failure_category",
    ],
)
def test_persisted_outcome_exact_string_types_are_required(
    tmp_path: Path, field: str
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path, "failed")
    expected = failure()
    bad = replace(
        expected, **{field: type("StrSubclass", (str,), {})(getattr(expected, field))}
    )
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events, phase65_function=lambda *_: bad
        )


def test_persisted_outcome_exact_index_type_is_required(tmp_path: Path) -> None:
    state, events, result, definition, _, _ = setup(tmp_path, "failed")
    bad = replace(failure(), current_step_index=True)
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events, phase65_function=lambda *_: bad
        )


@pytest.mark.parametrize(
    "bad",
    [
        object(),
        OutcomeSubclass("persisted_success", "w", "one", 1, "a", None),
        failure(),
    ],
)
def test_malformed_dependency_return_is_rejected_once_and_restored(
    tmp_path: Path, bad: object
) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path)
    calls = 0

    def phase65(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events, phase65_function=phase65
        )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize("mutate", ["state", "events", "both"])
def test_dependency_mutation_is_compensated_and_rollback_attempts_both(
    tmp_path: Path, mutate: str
) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path)
    calls = 0

    def phase65(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutate in ("state", "both"):
            state.write_bytes(b"changed-state")
        if mutate in ("events", "both"):
            events.write_bytes(b"changed-events")
        return object()

    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ):
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events, phase65_function=phase65
        )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
def test_dependency_errors_are_safe_and_compensated_without_retry(
    tmp_path: Path, kind: str
) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path)
    calls = 0
    safe = PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError(
        "terminal_contract"
    )

    def phase65(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed")
        events.write_bytes(b"changed")
        if kind == "safe":
            raise safe
        raise RuntimeError("secret")

    with pytest.raises(
        (
            PersistedOutcomeClassificationRoutingPhaseBridgeContinuationError,
            PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError,
        )
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events, phase65_function=phase65
        )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"


def test_safe_dependency_error_identity_is_preserved_without_target_mutation(
    tmp_path: Path,
) -> None:
    state, events, result, definition, before_state, before_events = setup(tmp_path)
    safe = PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError(
        "terminal_contract"
    )
    calls = 0

    def phase65(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise safe

    with pytest.raises(
        PersistedTerminalOutcomeClassificationRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events, phase65_function=phase65
        )
    assert caught.value is safe and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_is_dependency_rollback_and_attempts_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    state, events, result, definition, _, _ = setup(tmp_path)
    attempts: list[Path] = []

    def phase65(*_: object) -> object:
        state.write_bytes(b"changed")
        events.write_bytes(b"changed")
        return object()

    original = Path.write_bytes

    def restore(path: Path, contents: bytes) -> int:
        if contents != b"changed":
            attempts.append(path)
        if contents != b"changed" and failed in ("state", "both") and path == state:
            raise OSError("state rollback")
        if contents != b"changed" and failed in ("events", "both") and path == events:
            raise OSError("events rollback")
        return original(path, contents)

    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(
        PersistedOutcomeClassificationRoutingPhaseBridgeContinuationCompatibilityError
    ) as caught:
        route_persisted_outcome_classification_routing_phase_bridge_continuation(
            result, definition, state, events, phase65_function=phase65
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts.count(state) == 1 and attempts.count(events) == 1
