"""Focused Phase 57 contract tests using injected Phase 50 fakes only."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError,
    ExecutedResultTransitionPersistencePhaseBridgeError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_executed_result_transition_persistence_phase_bridge_reentry,
)
from ai_office.engine.executed_result_transition_persistence_bridge_reentry import (
    ExecutedResultTransitionPersistenceBridgeCompatibilityError,
    ExecutedResultTransitionPersistenceBridgeError,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class SuccessSubclass(StepRuntimeExecutionSuccess):
    pass


class FailureSubclass(StepRuntimeExecutionFailure):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class PersistenceResultSubclass(WorkflowExecutionPersistenceResult):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "step", "name": "S", "employee": "e", "instructions": "do"}
            ],
        }
    )


def success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "step",
        1,
        "e",
        ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"),
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "step",
        1,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", "q", 500, None, None),
    )


def setup(tmp_path: Path, status: str = "running") -> tuple[Path, Path, bytes, bytes]:
    state = WorkflowExecutionState(
        "w",
        status,
        "step",
        1,
        "e",
        ("step",) if status == "succeeded" else (),
        None if status != "failed" else "api_error",
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    if status == "succeeded":
        event = RuntimeStepEvent(
            "step_succeeded",
            "w",
            "step",
            1,
            "e",
            "running",
            "succeeded",
            "openai",
            None,
            "r",
            "q",
            "out",
            None,
        )
        events_path.write_text(serialize_runtime_step_event_jsonl(event))
    elif status == "failed":
        event = RuntimeStepEvent(
            "step_failed",
            "w",
            "step",
            1,
            "e",
            "running",
            "failed",
            "openai",
            "api_error",
            None,
            "q",
            None,
            "safe",
        )
        events_path.write_text(serialize_runtime_step_event_jsonl(event))
    else:
        events_path.write_text("")
    return state_path, events_path, state_path.read_bytes(), events_path.read_bytes()


def persist_fake(
    result: object, _workflow: object, state: Path, events: Path
) -> WorkflowExecutionPersistenceResult:
    invocation = result.invocation_result  # type: ignore[union-attr]
    ok = type(result) is StepRuntimeExecutionSuccess
    next_state = WorkflowExecutionState(
        "w",
        "succeeded" if ok else "failed",
        "step",
        1,
        "e",
        ("step",) if ok else (),
        None if ok else invocation.category,
    )
    event = RuntimeStepEvent(
        "step_succeeded" if ok else "step_failed",
        "w",
        "step",
        1,
        "e",
        "running",
        next_state.status,
        "openai",
        None if ok else invocation.category,
        invocation.response_id if ok else None,
        invocation.request_id,
        invocation.text if ok else None,
        None if ok else invocation.message,
    )
    state_bytes = serialize_workflow_execution_state_json(next_state).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    events.write_bytes(events.read_bytes() + event_bytes)
    state.write_bytes(state_bytes)
    return WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(event_bytes)
    )


@pytest.mark.parametrize("result", [success(), failure()])
def test_runtime_delegates_once_with_identity_and_exact_objects(
    tmp_path: Path, result: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    supplied_workflow = workflow()
    calls: list[tuple[object, ...]] = []
    expected: WorkflowExecutionPersistenceResult | None = None

    def fake(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert args[0] is result and args[1] is supplied_workflow
        assert args[2] is state and args[3] is events
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    returned = route_executed_result_transition_persistence_phase_bridge_reentry(
        result, supplied_workflow, state, events, phase50_function=fake
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) != (before_state, before_events)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_terminal_routes_return_identity_without_writes_or_dependency(
    tmp_path: Path, kind: str
) -> None:
    state, events, before_state, before_events = setup(
        tmp_path, "succeeded" if kind == "complete" else "failed"
    )
    result = (
        WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "step",
            1,
            "e",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        if kind == "complete"
        else PersistedExecutionOutcome(
            "persisted_failure", "w", "step", 1, "e", "api_error"
        )
    )
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow(), state, events, phase50_function=unexpected
        )
        is result
    )
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


def test_persisted_success_is_rejected_without_dependency(tmp_path: Path) -> None:
    state, events, before_state, before_events = setup(tmp_path, "succeeded")
    value = PersistedExecutionOutcome("persisted_success", "w", "step", 1, "e", None)
    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            value, workflow(), state, events
        )
    assert caught.value.detail.classification == "failure_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_malformed_dependency_is_compensated_without_retry(tmp_path: Path) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def malformed(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"bad")
        events.write_bytes(b"bad\n")
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=malformed
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


def test_safe_dependency_error_identity_is_preserved_after_rollback(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    expected = ExecutedResultTransitionPersistenceBridgeCompatibilityError(
        "runtime_contract"
    )

    def raises(*_: object) -> object:
        state.write_bytes(b"changed")
        raise expected

    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=raises
        )
    assert caught.value is expected
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def run_runtime(
    tmp_path: Path,
    result: object = None,
    dependency: object = None,
) -> tuple[Path, Path, bytes, bytes, object, object]:
    state, events, before_state, before_events = setup(tmp_path)
    value = success() if result is None else result
    function = persist_fake if dependency is None else dependency
    return state, events, before_state, before_events, value, function


@pytest.mark.parametrize(
    "value",
    [
        object(),
        SimpleNamespace(workflow_id="w", step_id="step", step_index=1, employee_id="e"),
    ],
)
def test_wrong_result_type_and_attribute_substitute_stop_before_dependency(
    tmp_path: Path, value: object
) -> None:
    state, events, before_state, before_events, _, _ = run_runtime(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            value, workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == "result_type"
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    "value",
    [SuccessSubclass(**success().__dict__), FailureSubclass(**failure().__dict__)],
)
def test_result_subclasses_are_rejected(tmp_path: Path, value: object) -> None:
    state, events, before_state, before_events, _, _ = run_runtime(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            value, workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == "result_type"
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    "supplied",
    [
        object(),
        WorkflowSubclass.model_validate(workflow().model_dump()),
        SimpleNamespace(id="w"),
    ],
)
def test_wrong_workflow_type_subclass_and_substitute_stop_before_dependency(
    tmp_path: Path, supplied: object
) -> None:
    state, events, before_state, before_events, result, _ = run_runtime(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, supplied, state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == "workflow_definition"
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    "which", ["state", "events", "conflict", "state_read", "events_read"]
)
def test_target_validation_and_events_specific_errors(
    tmp_path: Path, which: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events, before_state, before_events, result, _ = run_runtime(tmp_path)
    if which == "state":
        state.unlink()
    elif which == "events":
        events.unlink()
    elif which == "conflict":
        events = state
    elif which == "state_read":
        original = Path.is_file
        monkeypatch.setattr(
            Path,
            "is_file",
            lambda path: (
                (_ for _ in ()).throw(OSError()) if path == state else original(path)
            ),
        )
    else:
        original = Path.is_file
        monkeypatch.setattr(
            Path,
            "is_file",
            lambda path: (
                (_ for _ in ()).throw(OSError()) if path == events else original(path)
            ),
        )
    expected = (
        "target_conflict"
        if which == "conflict"
        else "event_target"
        if which in {"events", "events_read"}
        else "state_target"
    )
    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow(), state, events, phase50_function=persist_fake
        )
    assert caught.value.detail.classification == expected
    assert state.exists() or which == "state"
    if which != "conflict":
        assert before_events == events.read_bytes() if events.exists() else True
    _ = before_state


def test_event_read_bytes_failure_is_event_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events, before_state, before_events, result, _ = run_runtime(tmp_path)
    original = Path.read_bytes

    def failing_read(path: Path) -> bytes:
        if path == events:
            raise OSError("events unreadable")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", failing_read)
    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow(), state, events, phase50_function=persist_fake
        )
    assert caught.value.detail.classification == "event_target"
    assert original(state) == before_state and before_events == b""


def test_state_read_bytes_failure_is_state_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events, before_state, before_events, result, _ = run_runtime(tmp_path)
    original = Path.read_bytes

    def failing_read(path: Path) -> bytes:
        if path == state:
            raise OSError("state unreadable")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", failing_read)
    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow(), state, events, phase50_function=persist_fake
        )
    assert caught.value.detail.classification == "state_target"
    assert original(state) == before_state and original(events) == before_events


def test_non_callable_dependency_is_rejected_before_any_write(tmp_path: Path) -> None:
    state, events, before_state, before_events, result, _ = run_runtime(tmp_path)
    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow(), state, events, phase50_function=object()
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "result",
    [
        replace(success(), invocation_result=object()),
        replace(failure(), invocation_result=object()),
        replace(success(), workflow_id="other"),
        replace(success(), step_id="other"),
        replace(success(), step_index=True),
        replace(success(), employee_id="other"),
        SuccessSubclass(**success().__dict__),
    ],
)
def test_malformed_runtime_and_identity_mismatch_stop_before_dependency(
    tmp_path: Path, result: object
) -> None:
    state, events, before_state, before_events, _, _ = run_runtime(tmp_path)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    expected = "result_type" if type(result) is SuccessSubclass else "runtime_contract"
    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == expected
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize(
    "result, status, expected",
    [
        (
            replace(
                WorkflowProgressionDecision(
                    "workflow_complete",
                    "w",
                    "step",
                    1,
                    "e",
                    None,
                    None,
                    None,
                    "last_step_succeeded",
                ),
                reason="bad",
            ),
            "succeeded",
            "completion_contract",
        ),
        (
            replace(
                PersistedExecutionOutcome(
                    "persisted_failure", "w", "step", 1, "e", "api_error"
                ),
                outcome="persisted_success",
            ),
            "failed",
            "failure_contract",
        ),
        (
            replace(
                PersistedExecutionOutcome(
                    "persisted_failure", "w", "step", 1, "e", "api_error"
                ),
                current_step_index=2,
            ),
            "failed",
            "failure_contract",
        ),
    ],
)
def test_malformed_terminal_results_stop_before_dependency(
    tmp_path: Path, result: object, status: str, expected: str
) -> None:
    state, events, before_state, before_events = setup(tmp_path, status)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == expected
    assert calls == 0 and (state.read_bytes(), events.read_bytes()) == (
        before_state,
        before_events,
    )


@pytest.mark.parametrize("status", ["ready", "failed", "succeeded"])
def test_runtime_state_history_mismatch_stops_before_dependency(
    tmp_path: Path, status: str
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    if status != "ready":
        state.write_text(
            serialize_workflow_execution_state_json(
                WorkflowExecutionState(
                    "w",
                    status,
                    "step",
                    1,
                    "e",
                    ("step",) if status == "succeeded" else (),
                    None if status != "failed" else "api_error",
                )
            )
        )
    else:
        state.write_text(
            serialize_workflow_execution_state_json(
                WorkflowExecutionState("w", "ready", "step", 1, "e", (), None)
            )
        )
    changed_state = state.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert (
        calls == 0
        and state.read_bytes() == changed_state
        and events.read_bytes() == before_events
    )
    _ = before_state


def valid_result(
    tmp_path: Path,
) -> tuple[Path, Path, bytes, bytes, WorkflowExecutionPersistenceResult]:
    state, events, before_state, before_events = setup(tmp_path)
    holder: list[WorkflowExecutionPersistenceResult] = []

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        holder.append(value)
        return value

    route_executed_result_transition_persistence_phase_bridge_reentry(
        success(), workflow(), state, events, phase50_function=dependency
    )
    return state, events, before_state, before_events, holder[0]


@pytest.mark.parametrize("kind", ["completion", "failure"])
def test_terminal_state_history_mismatch_stops_before_dependency(
    tmp_path: Path, kind: str
) -> None:
    state, events, before_state, before_events = setup(
        tmp_path, "succeeded" if kind == "completion" else "failed"
    )
    events.write_bytes(b"invalid\n")
    before_bad_events = events.read_bytes()
    result = (
        WorkflowProgressionDecision(
            "workflow_complete",
            "w",
            "step",
            1,
            "e",
            None,
            None,
            None,
            "last_step_succeeded",
        )
        if kind == "completion"
        else PersistedExecutionOutcome(
            "persisted_failure", "w", "step", 1, "e", "api_error"
        )
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            result, workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (
        state.read_bytes() == before_state and events.read_bytes() == before_bad_events
    )
    _ = before_events


@pytest.mark.parametrize(
    "kind",
    [
        "object",
        "subclass",
        "substitute",
        "state_path",
        "events_path",
        "bool",
        "zero",
        "negative",
        "float",
        "string",
        "none",
        "object_count",
    ],
)
def test_malformed_phase50_return_and_count_fields_are_rejected_and_restored(
    tmp_path: Path, kind: str
) -> None:
    state, events, before_state, before_events = setup(tmp_path)

    def dependency(*args: object) -> object:
        valid = persist_fake(*args)  # type: ignore[arg-type]
        if kind == "object":
            return object()
        if kind == "subclass":
            return PersistenceResultSubclass(
                valid.state_path,
                valid.events_path,
                valid.state_bytes_written,
                valid.event_bytes_appended,
            )
        if kind == "substitute":
            return SimpleNamespace(**valid.__dict__)
        if kind == "state_path":
            return replace(valid, state_path=Path("other"))
        if kind == "events_path":
            return replace(valid, events_path=Path("other"))
        values = {
            "bool": True,
            "zero": 0,
            "negative": -1,
            "float": 1.0,
            "string": "1",
            "none": None,
            "object_count": object(),
        }
        return replace(valid, state_bytes_written=values[kind])

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "field, value",
    [
        (field, value)
        for field in ("state_bytes_written", "event_bytes_appended")
        for value in (True, 0, -1, 1.0, "1", None, object())
    ],
)
def test_every_count_field_rejects_non_positive_exact_integers(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)

    def dependency(*args: object) -> object:
        valid = persist_fake(*args)  # type: ignore[arg-type]
        return replace(valid, **{field: value})

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "mutation",
    [
        "no_transition",
        "wrong_final_state",
        "missing_event",
        "multiple_events",
        "invalid_event",
        "state_replace",
        "events_replace",
        "both_replace",
        "state_delete",
        "events_delete",
        "state_truncate",
        "events_truncate",
        "state_append",
        "events_append",
        "partial_state",
        "partial_events",
    ],
)
def test_phase50_invalid_transition_and_partial_side_effects_are_rejected(
    tmp_path: Path, mutation: str
) -> None:
    state, events, before_state, before_events = setup(tmp_path)

    def dependency(*args: object) -> object:
        valid = (
            persist_fake(*args)
            if mutation != "no_transition"
            else WorkflowExecutionPersistenceResult(state, events, 1, 1)
        )
        if mutation == "wrong_final_state":
            state.write_bytes(
                serialize_workflow_execution_state_json(
                    WorkflowExecutionState("w", "running", "step", 1, "e", (), None)
                ).encode()
            )
        elif mutation == "missing_event":
            events.write_bytes(before_events)
        elif mutation == "multiple_events":
            events.write_bytes(events.read_bytes() + events.read_bytes())
        elif mutation == "invalid_event":
            events.write_bytes(b"{}\n")
        elif mutation == "state_replace":
            state.write_bytes(b"replace")
        elif mutation == "events_replace":
            events.write_bytes(b"replace\n")
        elif mutation == "both_replace":
            state.write_bytes(b"replace")
            events.write_bytes(b"replace\n")
        elif mutation == "state_delete":
            state.unlink()
        elif mutation == "events_delete":
            events.unlink()
        elif mutation == "state_truncate":
            state.write_bytes(b"")
        elif mutation == "events_truncate":
            events.write_bytes(b"")
        elif mutation == "state_append":
            state.write_bytes(state.read_bytes() + b"extra")
        elif mutation == "events_append":
            events.write_bytes(events.read_bytes() + b"extra\n")
        elif mutation == "partial_state":
            state.write_bytes(state.read_bytes()[:4])
        elif mutation == "partial_events":
            events.write_bytes(events.read_bytes()[:4])
        return valid

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["none", "state", "events", "both"])
@pytest.mark.parametrize("error_kind", ["safe", "unexpected"])
def test_dependency_errors_preserve_safe_identity_or_sanitize_and_rollback(
    tmp_path: Path, mutation: str, error_kind: str
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    expected = ExecutedResultTransitionPersistenceBridgeCompatibilityError(
        "runtime_contract"
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed\n")
        if error_kind == "safe":
            raise expected
        raise RuntimeError(
            "/sensitive/path workflow-id provider-request response "
            "output failure-message raw-bytes"
        )

    exception_type = (
        ExecutedResultTransitionPersistenceBridgeError
        if error_kind == "safe"
        else ExecutedResultTransitionPersistencePhaseBridgeError
    )
    with pytest.raises(exception_type) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=dependency
        )
    if error_kind == "safe":
        assert caught.value is expected
    else:
        assert (
            type(caught.value)
            is ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
        )
        assert caught.value.detail.classification == "dependency_error"
        assert "sensitive" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_unchanged_dependency_error_performs_zero_restore_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events, _, _ = setup(tmp_path)
    original_write = Path.write_bytes
    writes: list[Path] = []

    def counting_write(path: Path, contents: bytes) -> int:
        writes.append(path)
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", counting_write)

    def dependency(*_: object) -> object:
        raise ExecutedResultTransitionPersistenceBridgeCompatibilityError(
            "runtime_contract"
        )

    with pytest.raises(ExecutedResultTransitionPersistenceBridgeError):
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=dependency
        )
    assert writes == []


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
@pytest.mark.parametrize("error_kind", ["safe", "unexpected"])
def test_rollback_attempts_both_targets_and_failure_overrides_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_target: str,
    error_kind: str,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    original_write = Path.write_bytes
    restore_attempts: list[Path] = []

    def dependency(*_: object) -> object:
        original_write(state, b"changed")
        if error_kind == "safe":
            raise ExecutedResultTransitionPersistenceBridgeCompatibilityError(
                "runtime_contract"
            )
        raise RuntimeError("dependency")

    def failing_write(path: Path, contents: bytes) -> int:
        restore_attempts.append(path)
        if path == state and failed_target in {"state", "both"}:
            raise OSError("state rollback")
        if path == events and failed_target in {"events", "both"}:
            raise OSError("events rollback")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", failing_write)
    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            success(), workflow(), state, events, phase50_function=dependency
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert state in restore_attempts and events in restore_attempts
    if failed_target == "state":
        assert events.read_bytes() == before_events
    elif failed_target == "events":
        assert state.read_bytes() == before_state


def test_public_errors_do_not_expose_sensitive_details() -> None:
    sensitive = (
        "/tmp/private/path workflow-id provider-request provider-response "
        "output-text failure-message raw-bytes traceback"
    )
    error = ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError(
        "dependency_error"
    )
    assert all(value not in str(error) for value in sensitive.split())
    assert all(value not in repr(error) for value in sensitive.split())


# ---------------------------------------------------------------------------
# Phase 160 (Issue #326): empty-output predecessor compatibility
# ---------------------------------------------------------------------------

_CHAIN_STEP_IDS = ("s1", "s2", "s3", "s4", "s5", "s6")
_SENTINEL = object()


def chain_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": step_id,
                    "name": step_id.upper(),
                    "employee": "e",
                    "instructions": step_id,
                }
                for step_id in _CHAIN_STEP_IDS
            ],
        }
    )


def chain_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "s6",
        6,
        "e",
        ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out"),
    )


def chain_failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "w",
        "s6",
        6,
        "e",
        ModelInvocationFailure("openai", "api_error", "safe", "q", 500, None, None),
    )


def chain_predecessor_event(
    step_id: str, position: int, output_text: object, request_id: object
) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        position,
        "e",
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{step_id}",
        request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def chain_setup(
    tmp_path: Path,
    *,
    empty_earlier: tuple[int, ...] = (),
    immediate_empty: bool = False,
    bad_output: object = _SENTINEL,
    bad_position: int = 2,
) -> tuple[Path, Path, bytes, bytes]:
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state = WorkflowExecutionState(
        "w", "running", "s6", 6, "e", ("s1", "s2", "s3", "s4", "s5"), None
    )
    state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    records = []
    for position, step_id in enumerate(_CHAIN_STEP_IDS[:5], 1):
        output: object = "output"
        if position in empty_earlier or (position == 5 and immediate_empty):
            output = ""
        if bad_output is not _SENTINEL and position == bad_position:
            output = bad_output
        request_id: object = None if position == 5 else f"request-{step_id}"
        records.append(
            serialize_runtime_step_event_jsonl(
                chain_predecessor_event(step_id, position, output, request_id)
            )
        )
    events_path.write_text("".join(records), encoding="utf-8")
    return state_path, events_path, state_path.read_bytes(), events_path.read_bytes()


def chain_persist_fake(
    result: object, _workflow: object, state: Path, events: Path
) -> WorkflowExecutionPersistenceResult:
    invocation = result.invocation_result  # type: ignore[union-attr]
    ok = type(result) is StepRuntimeExecutionSuccess
    next_state = WorkflowExecutionState(
        "w",
        "succeeded" if ok else "failed",
        "s6",
        6,
        "e",
        ("s1", "s2", "s3", "s4", "s5", "s6")
        if ok
        else ("s1", "s2", "s3", "s4", "s5"),
        None if ok else invocation.category,
    )
    event = RuntimeStepEvent(
        "step_succeeded" if ok else "step_failed",
        "w",
        "s6",
        6,
        "e",
        "running",
        next_state.status,
        "openai",
        None if ok else invocation.category,
        invocation.response_id if ok else None,
        invocation.request_id,
        invocation.text if ok else None,
        None if ok else invocation.message,
    )
    state_bytes = serialize_workflow_execution_state_json(next_state).encode("utf-8")
    event_bytes = serialize_runtime_step_event_jsonl(event).encode("utf-8")
    events.write_bytes(events.read_bytes() + event_bytes)
    state.write_bytes(state_bytes)
    return WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(event_bytes)
    )


def test_earlier_empty_output_delegates_once_to_phase50(tmp_path: Path) -> None:
    state, events, before_state, before_events = chain_setup(
        tmp_path, empty_earlier=(2,)
    )
    result = chain_success()
    supplied_workflow = chain_workflow()
    calls: list[tuple[object, ...]] = []
    expected: WorkflowExecutionPersistenceResult | None = None

    def fake(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert args[0] is result and args[1] is supplied_workflow
        assert args[2] is state and args[3] is events
        expected = chain_persist_fake(*args)  # type: ignore[arg-type]
        return expected

    returned = route_executed_result_transition_persistence_phase_bridge_reentry(
        result, supplied_workflow, state, events, phase50_function=fake
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) != (before_state, before_events)


def test_immediate_empty_output_delegates_once_to_phase50(tmp_path: Path) -> None:
    state, events, before_state, before_events = chain_setup(
        tmp_path, immediate_empty=True
    )
    result = chain_failure()
    supplied_workflow = chain_workflow()
    calls: list[tuple[object, ...]] = []
    expected: WorkflowExecutionPersistenceResult | None = None

    def fake(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert args[0] is result and args[1] is supplied_workflow
        assert args[2] is state and args[3] is events
        expected = chain_persist_fake(*args)  # type: ignore[arg-type]
        return expected

    returned = route_executed_result_transition_persistence_phase_bridge_reentry(
        result, supplied_workflow, state, events, phase50_function=fake
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) != (before_state, before_events)


def test_combined_earlier_and_immediate_empty_outputs_delegate_once(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = chain_setup(
        tmp_path, empty_earlier=(2, 3), immediate_empty=True
    )
    result = chain_success()
    supplied_workflow = chain_workflow()
    calls: list[tuple[object, ...]] = []
    expected: WorkflowExecutionPersistenceResult | None = None

    def fake(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert args[0] is result and args[1] is supplied_workflow
        assert args[2] is state and args[3] is events
        expected = chain_persist_fake(*args)  # type: ignore[arg-type]
        return expected

    returned = route_executed_result_transition_persistence_phase_bridge_reentry(
        result, supplied_workflow, state, events, phase50_function=fake
    )
    assert returned is expected and len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) != (before_state, before_events)


@pytest.mark.parametrize("bad", [None, 123, True])
def test_bad_predecessor_output_text_rejected_before_phase50(
    tmp_path: Path, bad: object
) -> None:
    state, events, before_state, before_events = chain_setup(
        tmp_path, bad_output=bad
    )
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(
        ExecutedResultTransitionPersistencePhaseBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_phase_bridge_reentry(
            chain_success(),
            chain_workflow(),
            state,
            events,
            phase50_function=unexpected,
        )
    assert caught.value.detail.classification == "runtime_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
