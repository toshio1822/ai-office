"""Focused Phase 51 bridge tests using injected Phase 44 fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PersistedTerminalOutcomeClassificationBridgeCompatibilityError,
    WorkflowProgressionDecision,
    route_persisted_terminal_outcome_classification_bridge_reentry,
)
from ai_office.engine.persisted_terminal_outcome_classification_routing_reentry import (
    PersistedTerminalOutcomeClassificationRoutingCompatibilityError,
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
) -> tuple[Path, Path, WorkflowExecutionPersistenceResult]:
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
    )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_persistence_result_routes_once_with_identity_and_unchanged_targets(
    tmp_path: Path, status: str
) -> None:
    state, events, persisted = setup(tmp_path, status)
    supplied_workflow = workflow()
    expected = PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "workflow",
        "step",
        1,
        "employee",
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def phase44(*args: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        assert len(args) == 4
        assert args[0] is persisted
        assert args[1] is supplied_workflow
        assert args[2] is state
        assert args[3] is events
        return expected

    assert (
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            supplied_workflow,
            state,
            events,
            classification_routing_function=phase44,
        )
        is expected
    )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_terminal_stop_routes_return_same_object_without_phase44(
    tmp_path: Path,
) -> None:
    state, events, _ = setup(tmp_path)
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
    calls = 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_persisted_terminal_outcome_classification_bridge_reentry(
            decision,
            workflow(),
            state,
            events,
            classification_routing_function=unexpected,
        )
        is decision
    )
    assert calls == 0


def test_persisted_failure_stops_without_phase44(tmp_path: Path) -> None:
    state, events, _ = setup(tmp_path, "failed")
    outcome = PersistedExecutionOutcome(
        "persisted_failure", "workflow", "step", 1, "employee", "api_error"
    )
    assert (
        route_persisted_terminal_outcome_classification_bridge_reentry(
            outcome,
            workflow(),
            state,
            events,
            classification_routing_function=lambda *_: (_ for _ in ()).throw(
                AssertionError
            ),
        )
        is outcome
    )


@pytest.mark.parametrize(
    "result",
    [
        PersistenceSubclass(Path("a"), Path("b"), 1, 1),
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
        object(),
    ],
)
def test_exact_result_type_rejects_subclasses_before_phase44(
    tmp_path: Path, result: object
) -> None:
    state, events, _ = setup(tmp_path)
    calls = 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedTerminalOutcomeClassificationBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_bridge_reentry(
            result,
            workflow(),
            state,
            events,
            classification_routing_function=unexpected,
        )
    assert calls == 0


def test_direct_persisted_success_stop_input_rejects_before_phase44(
    tmp_path: Path,
) -> None:
    state, events, _ = setup(tmp_path)
    supplied = PersistedExecutionOutcome(
        "persisted_success", "workflow", "step", 1, "employee", None
    )
    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            supplied,
            workflow(),
            state,
            events,
            classification_routing_function=lambda *_: (_ for _ in ()).throw(
                AssertionError
            ),
        )
    assert caught.value.detail.classification == "failure_contract"


@pytest.mark.parametrize(
    "invalid",
    [object(), WorkflowExecutionPersistenceResult(Path("a"), Path("b"), 1, 1)],
)
def test_invalid_result_rejects_before_phase44(tmp_path: Path, invalid: object) -> None:
    state, events, _ = setup(tmp_path)
    calls = 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedTerminalOutcomeClassificationBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_bridge_reentry(
            invalid,
            workflow(),
            state,
            events,
            classification_routing_function=unexpected,
        )
    assert calls == 0


@pytest.mark.parametrize(
    "change",
    [
        lambda result: replace(result, state_bytes_written=True),
        lambda result: replace(result, event_bytes_appended=True),
        lambda result: replace(result, state_bytes_written=0),
        lambda result: replace(result, event_bytes_appended=0),
        lambda result: replace(result, state_path=result.events_path),
    ],
)
def test_bad_persistence_contract_rejects_before_phase44(
    tmp_path: Path, change: object
) -> None:
    state, events, persisted = setup(tmp_path)
    with pytest.raises(PersistedTerminalOutcomeClassificationBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_bridge_reentry(
            change(persisted),
            workflow(),
            state,
            events,
            classification_routing_function=lambda *_: (_ for _ in ()).throw(
                AssertionError
            ),  # type: ignore[operator]
        )


@pytest.mark.parametrize(
    ("definition", "state_value", "events_value", "function"),
    [
        (WorkflowSubclass(**workflow().__dict__), None, None, None),
        (None, "wrong", None, None),
        (None, None, "wrong", None),
        (None, "same", "same", None),
        (None, None, None, object()),
    ],
)
def test_top_level_prevalidation_has_zero_phase44_calls(
    tmp_path: Path,
    definition: object,
    state_value: object,
    events_value: object,
    function: object,
) -> None:
    state, events, persisted = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    actual_state = state if state_value is None else state_value
    actual_events = events if events_value is None else events_value
    if state_value == "same":
        actual_state = actual_events = state
    with pytest.raises(PersistedTerminalOutcomeClassificationBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow() if definition is None else definition,
            actual_state,
            actual_events,
            classification_routing_function=unexpected
            if function is None
            else function,  # type: ignore[arg-type]
        )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("missing", ["state", "events"])
def test_missing_target_rejects_before_phase44(tmp_path: Path, missing: str) -> None:
    state, events, persisted = setup(tmp_path)
    (state if missing == "state" else events).unlink()
    calls = 0

    def unexpected(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(PersistedTerminalOutcomeClassificationBridgeCompatibilityError):
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=unexpected,
        )
    assert calls == 0


@pytest.mark.parametrize("operation", ["replace", "delete", "append"])
def test_phase44_mutation_is_restored_without_retry(
    tmp_path: Path, operation: str
) -> None:
    state, events, persisted = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def phase44(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        if operation == "delete":
            events.unlink()
        elif operation == "append":
            events.write_bytes(events.read_bytes() + b"x")
        else:
            events.write_bytes(b"changed")
        return PersistedExecutionOutcome(
            "persisted_success", "workflow", "step", 1, "employee", None
        )

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=phase44,
        )
    assert caught.value.detail.classification == "classification_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_every_phase44_target_mutation_is_restored(
    tmp_path: Path, operation: str, target: str
) -> None:
    state, events, persisted = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def mutate(path: Path) -> None:
        if operation == "replace":
            path.write_bytes(b"replacement")
        elif operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        else:
            path.write_bytes(path.read_bytes() + b"append")

    def phase44(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        return PersistedExecutionOutcome(
            "persisted_success", "workflow", "step", 1, "employee", None
        )

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=phase44,
        )
    assert caught.value.detail.classification == "classification_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        OutcomeSubclass("persisted_success", "workflow", "step", 1, "employee", None),
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
        PersistedExecutionOutcome(
            "persisted_success", "workflow", "step", 1, "employee", "api_error"
        ),
    ],
)
def test_phase44_return_contract_rejects_wrong_exact_outcome(
    tmp_path: Path, returned: object
) -> None:
    state, events, persisted = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0

    def phase44(*_: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=phase44,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "classification_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


def test_dependency_error_identity_and_sanitization(tmp_path: Path) -> None:
    state, events, persisted = setup(tmp_path)
    safe = PersistedTerminalOutcomeClassificationRoutingCompatibilityError(
        "result_type"
    )
    with pytest.raises(
        PersistedTerminalOutcomeClassificationRoutingCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=lambda *_: (_ for _ in ()).throw(safe),
        )
    assert caught.value is safe
    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=lambda *_: (_ for _ in ()).throw(
                RuntimeError("sensitive output")
            ),
        )
    assert (
        caught.value.detail.classification == "dependency_error"
        and "output" not in str(caught.value)
    )


@pytest.mark.parametrize("error_kind", ["safe", "unexpected"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_dependency_errors_restore_every_mutated_target(
    tmp_path: Path, error_kind: str, target: str
) -> None:
    state, events, persisted = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = PersistedTerminalOutcomeClassificationRoutingCompatibilityError(
        "result_type"
    )

    def phase44(*_: object) -> PersistedExecutionOutcome:
        if target in {"state", "both"}:
            state.write_bytes(b"changed-state")
        if target in {"events", "both"}:
            events.write_bytes(b"changed-events")
        if error_kind == "safe":
            raise safe
        raise RuntimeError("provider request response output")

    expected = (
        PersistedTerminalOutcomeClassificationRoutingCompatibilityError
        if error_kind == "safe"
        else PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=phase44,
        )
    if error_kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("error_kind", ["safe", "unexpected"])
@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_safe_and_unexpected_errors_compensate_full_mutation_matrix(
    tmp_path: Path, error_kind: str, operation: str, target: str
) -> None:
    state, events, persisted = setup(tmp_path)
    before, calls = (state.read_bytes(), events.read_bytes()), 0
    safe = PersistedTerminalOutcomeClassificationRoutingCompatibilityError(
        "result_type"
    )

    def mutate(path: Path) -> None:
        if operation == "replace":
            path.write_bytes(b"replacement")
        elif operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        else:
            path.write_bytes(path.read_bytes() + b"append")

    def phase44(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        if error_kind == "safe":
            raise safe
        raise RuntimeError("provider request response output")

    expected = (
        PersistedTerminalOutcomeClassificationRoutingCompatibilityError
        if error_kind == "safe"
        else PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=phase44,
        )
    if error_kind == "safe":
        assert caught.value is safe
    else:
        assert caught.value.detail.classification == "dependency_error"
        assert "provider" not in str(caught.value)
        assert "output" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_target: str
) -> None:
    state, events, persisted = setup(tmp_path)
    originals = {state: state.read_bytes(), events: events.read_bytes()}
    original_write, restored = Path.write_bytes, []

    def fail_restore(path: Path, contents: bytes) -> int:
        if contents == originals.get(path):
            restored.append(path)
            if (
                failed_target == "both"
                or (failed_target == "state" and path == state)
                or (failed_target == "events" and path == events)
            ):
                raise OSError("sensitive provider output")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", fail_restore)

    def phase44(*_: object) -> PersistedExecutionOutcome:
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        raise RuntimeError("unexpected provider output")

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=phase44,
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert state in restored and events in restored


@pytest.mark.parametrize("dependency_kind", ["normal", "safe", "unexpected"])
@pytest.mark.parametrize("failed_restore", ["state", "events", "both"])
def test_rollback_failure_overrides_every_dependency_outcome_and_attempts_both_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency_kind: str,
    failed_restore: str,
) -> None:
    state, events, persisted = setup(tmp_path)
    originals = {state: state.read_bytes(), events: events.read_bytes()}
    original_write, restore_attempts, calls = Path.write_bytes, [], 0
    safe = PersistedTerminalOutcomeClassificationRoutingCompatibilityError(
        "result_type"
    )

    def fail_restore(path: Path, contents: bytes) -> int:
        if contents == originals.get(path):
            restore_attempts.append(path)
            if (
                failed_restore == "both"
                or (failed_restore == "state" and path == state)
                or (failed_restore == "events" and path == events)
            ):
                raise OSError("provider request response output")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", fail_restore)

    def phase44(*_: object) -> PersistedExecutionOutcome:
        nonlocal calls
        calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        if dependency_kind == "safe":
            raise safe
        if dependency_kind == "unexpected":
            raise RuntimeError("provider request response output")
        return PersistedExecutionOutcome(
            "persisted_failure", "workflow", "step", 1, "employee", "api_error"
        )

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            persisted,
            workflow(),
            state,
            events,
            classification_routing_function=phase44,
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert state in restore_attempts and events in restore_attempts
    assert "provider" not in str(caught.value)
    assert "output" not in str(caught.value)


# ---------------------------------------------------------------------------
# Phase 166: Phase-155 provenance compatibility (empty predecessor output_text)
# ---------------------------------------------------------------------------


_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_SENTINEL = object()


def six_step_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": step_id,
                    "name": step_id.upper(),
                    "employee": step_id[0],
                    "instructions": "do",
                }
                for step_id in _STEP_IDS
            ],
        }
    )


def predecessor_event(
    step_id: str,
    position: int,
    *,
    provider: str = "openai",
    request_id: object = _SENTINEL,
    output_text: object = "output",
) -> RuntimeStepEvent:
    resolved_request_id = (
        f"request-{step_id}" if request_id is _SENTINEL else request_id
    )
    return RuntimeStepEvent(
        "step_succeeded",
        "workflow",
        step_id,
        position,
        step_id[0],
        "running",
        "succeeded",
        provider,  # type: ignore[arg-type]
        None,
        f"response-{step_id}",
        resolved_request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def terminal_event(status: str, *, message: str = "safe failure") -> RuntimeStepEvent:
    if status == "succeeded":
        return RuntimeStepEvent(
            "step_succeeded",
            "workflow",
            "six",
            6,
            "s",
            "running",
            "succeeded",
            "openai",
            None,
            "response-six",
            "request-six",
            "output-six",
            None,
        )
    return RuntimeStepEvent(
        "step_failed",
        "workflow",
        "six",
        6,
        "s",
        "running",
        "failed",
        "openai",
        "api_error",
        None,
        "request-six",
        None,
        message,
    )


def setup_six(
    tmp_path: Path,
    status: str,
    *,
    earlier_empty: tuple[int, ...] = (2,),
    message: str = "safe failure",
) -> dict[str, object]:
    supplied_workflow = six_step_workflow()
    state = WorkflowExecutionState(
        "workflow",
        status,
        "six",
        6,
        "s",
        tuple(_STEP_IDS) if status == "succeeded" else tuple(_STEP_IDS[:5]),
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    events = [
        predecessor_event(
            step_id, position, output_text="" if position in earlier_empty else "output"
        )
        for position, step_id in enumerate(_STEP_IDS[:5], 1)
    ]
    events[4] = predecessor_event(
        "five", 5, provider="openai", request_id=None, output_text=""
    )
    events.append(terminal_event(status, message=message))
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    terminal_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode("utf-8")
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state_path, events_path, len(state_bytes), len(terminal_bytes)
    )
    return {
        "result": result,
        "workflow": supplied_workflow,
        "state_path": state_path,
        "events_path": events_path,
    }


def six_step_outcome(status: str) -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_success" if status == "succeeded" else "persisted_failure",
        "workflow",
        "six",
        6,
        "s",
        None if status == "succeeded" else "api_error",
    )


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_empty_predecessor_output_is_accepted_and_routes_once(
    tmp_path: Path, status: str
) -> None:
    values = setup_six(
        tmp_path,
        status,
        earlier_empty=(2,),
        message="" if status == "failed" else "safe failure",
    )
    expected = six_step_outcome(status)
    before = (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        values["events_path"].read_bytes(),  # type: ignore[union-attr]
    )
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PersistedExecutionOutcome:
        calls.append(args)
        assert args == (
            values["result"],
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
        return expected

    returned = route_persisted_terminal_outcome_classification_bridge_reentry(
        values["result"],  # type: ignore[arg-type]
        values["workflow"],  # type: ignore[arg-type]
        values["state_path"],  # type: ignore[arg-type]
        values["events_path"],  # type: ignore[arg-type]
        classification_routing_function=fake,
    )
    assert returned is expected
    assert len(calls) == 1
    assert (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        values["events_path"].read_bytes(),  # type: ignore[union-attr]
    ) == before


@pytest.mark.parametrize("status", ["succeeded", "failed"])
def test_multiple_earlier_empty_outputs_are_accepted_and_routes_once(
    tmp_path: Path, status: str
) -> None:
    values = setup_six(
        tmp_path,
        status,
        earlier_empty=(1, 3),
        message="" if status == "failed" else "safe failure",
    )
    expected = six_step_outcome(status)
    before = (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        values["events_path"].read_bytes(),  # type: ignore[union-attr]
    )
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PersistedExecutionOutcome:
        calls.append(args)
        assert args == (
            values["result"],
            values["workflow"],
            values["state_path"],
            values["events_path"],
        )
        return expected

    returned = route_persisted_terminal_outcome_classification_bridge_reentry(
        values["result"],  # type: ignore[arg-type]
        values["workflow"],  # type: ignore[arg-type]
        values["state_path"],  # type: ignore[arg-type]
        values["events_path"],  # type: ignore[arg-type]
        classification_routing_function=fake,
    )
    assert returned is expected
    assert len(calls) == 1
    assert (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        values["events_path"].read_bytes(),  # type: ignore[union-attr]
    ) == before


def test_none_predecessor_output_is_rejected_before_phase44(tmp_path: Path) -> None:
    values = setup_six(tmp_path, "succeeded")
    events_path = values["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    import json

    # Step two keeps its non-empty built-in request_id; only output_text becomes
    # None. The immediate step five keeps request_id None and empty output_text.
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("two", 2, output_text=None)
    )
    mutated = json.loads(replacement)
    assert mutated["request_id"] == "request-two"
    assert mutated["output_text"] is None
    events_path.write_text(  # type: ignore[union-attr]
        lines[0] + replacement + "".join(lines[2:]), encoding="utf-8"
    )
    before = (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        events_path.read_bytes(),  # type: ignore[union-attr]
    )
    calls = {"phase44": 0}

    def fail(*_: object) -> object:
        calls["phase44"] += 1
        pytest.fail("no dependency may be called")

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            values["result"],  # type: ignore[arg-type]
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
            classification_routing_function=fail,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase44": 0}
    assert (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        events_path.read_bytes(),  # type: ignore[union-attr]
    ) == before


def test_non_string_predecessor_output_is_rejected_before_phase44(
    tmp_path: Path,
) -> None:
    values = setup_six(tmp_path, "succeeded")
    events_path = values["events_path"]
    lines = events_path.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    import json

    # Immediate predecessor step five keeps request_id None and provider
    # "openai"; its output_text is mutated to a representative non-string.
    payload = json.loads(lines[4])
    assert payload["step_id"] == "five"
    assert payload["request_id"] is None
    assert payload["provider"] == "openai"
    assert payload["output_text"] == ""
    payload["output_text"] = 1
    lines[4] = json.dumps(payload, separators=(",", ":")) + "\n"
    events_path.write_text("".join(lines), encoding="utf-8")  # type: ignore[union-attr]
    before = (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        events_path.read_bytes(),  # type: ignore[union-attr]
    )
    calls = {"phase44": 0}

    def fail(*_: object) -> object:
        calls["phase44"] += 1
        pytest.fail("no dependency may be called")

    with pytest.raises(
        PersistedTerminalOutcomeClassificationBridgeCompatibilityError
    ) as caught:
        route_persisted_terminal_outcome_classification_bridge_reentry(
            values["result"],  # type: ignore[arg-type]
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
            classification_routing_function=fail,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == {"phase44": 0}
    assert (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        events_path.read_bytes(),  # type: ignore[union-attr]
    ) == before
