"""Phase 64 contract tests using injected Phase 57 fakes only."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionPersistencePhaseBridgeError,
    ExecutedResultTransitionPersistenceRoutingPhaseBridgeCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_executed_result_transition_persistence_routing_phase_bridge_reentry,
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
    )  # type: ignore[arg-type]
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    if status == "running":
        events_path.write_text("")
    else:
        event = RuntimeStepEvent(
            "step_succeeded" if status == "succeeded" else "step_failed",
            "w",
            "step",
            1,
            "e",
            "running",
            status,
            "openai",
            None if status == "succeeded" else "api_error",
            "r" if status == "succeeded" else None,
            "q",
            "out" if status == "succeeded" else None,
            None if status == "succeeded" else "safe",
        )
        events_path.write_text(serialize_runtime_step_event_jsonl(event))
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
    state.write_bytes(state_bytes)
    events.write_bytes(events.read_bytes() + event_bytes)
    return WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(event_bytes)
    )


def three_step_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "two"},
                {
                    "id": "three",
                    "name": "Three",
                    "employee": "e",
                    "instructions": "three",
                },
            ],
        }
    )


def three_step_success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "w",
        "three",
        3,
        "e",
        ModelInvocationSuccess("openai", "r3", "q3", "done", ("out",), "out"),
    )


def predecessor_event_with_output(
    step_id: str, step_index: int, output_text: object
) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        step_index,
        "e",
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{step_id}",
        f"request-{step_id}",
        output_text,  # type: ignore[arg-type]
        None,
    )


def setup_three_step_history(
    tmp_path: Path, outputs: tuple[object, object]
) -> tuple[Path, Path]:
    state = WorkflowExecutionState(
        "w", "running", "three", 3, "e", ("one", "two"), None
    )
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    first = serialize_runtime_step_event_jsonl(
        predecessor_event_with_output("one", 1, outputs[0])
    )
    second = serialize_runtime_step_event_jsonl(
        predecessor_event_with_output("two", 2, outputs[1])
    )
    events_path.write_text(first + second)
    return state_path, events_path


def persist_three_step(
    result: object, _workflow: object, state: Path, events: Path
) -> WorkflowExecutionPersistenceResult:
    invocation = result.invocation_result  # type: ignore[union-attr]
    ok = type(result) is StepRuntimeExecutionSuccess
    next_state = WorkflowExecutionState(
        "w",
        "succeeded" if ok else "failed",
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
        ("one", "two", "three") if ok else ("one", "two"),
        None if ok else invocation.category,
    )
    event = RuntimeStepEvent(
        "step_succeeded" if ok else "step_failed",
        "w",
        result.step_id,  # type: ignore[union-attr]
        result.step_index,  # type: ignore[union-attr]
        result.employee_id,  # type: ignore[union-attr]
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
    state.write_bytes(state_bytes)
    events.write_bytes(events.read_bytes() + event_bytes)
    return WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(event_bytes)
    )


def call(
    result: object,
    workflow_value: object,
    state: object,
    events: object,
    dependency: object,
) -> object:
    return route_executed_result_transition_persistence_routing_phase_bridge_reentry(
        result,
        workflow_value,
        state,
        events,
        phase57_function=dependency,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("result", [success(), failure()])
def test_runtime_routes_delegate_once_with_identity_and_return_identity(
    tmp_path: Path, result: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    supplied_workflow = workflow()
    calls = []
    expected: object = None

    def phase57(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(
            actual is expected_value
            for actual, expected_value in zip(
                args, (result, supplied_workflow, state, events), strict=True
            )
        )
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(result, supplied_workflow, state, events, phase57) is expected
    assert len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) != (before_state, before_events)


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_return_identity_without_dependency(
    tmp_path: Path, kind: str
) -> None:
    state, events, before_state, before_events = setup(
        tmp_path, "succeeded" if kind == "complete" else "failed"
    )
    result: object = (
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
    assert (
        call(result, workflow(), state, events, lambda *_: pytest.fail("called"))
        is result
    )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "value,classification",
    [
        (object(), "result_type"),
        (SimpleNamespace(workflow_id="w"), "result_type"),
        (PersistenceResultSubclass(Path("x"), Path("y"), 1, 1), "result_type"),
    ],
)
def test_result_types_reject_before_dependency(
    tmp_path: Path, value: object, classification: str
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(value, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == classification
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "value",
    [SuccessSubclass(**success().__dict__), FailureSubclass(**failure().__dict__)],
)
def test_runtime_subclasses_reject_before_dependency(
    tmp_path: Path, value: object
) -> None:
    state, events, _, _ = setup(tmp_path)
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(value, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "bad"),
        ("step_id", "bad"),
        ("step_index", 2),
        ("employee_id", "bad"),
    ],
)
def test_runtime_identity_rejects_before_dependency(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, _, _ = setup(tmp_path)
    result = replace(success(), **{field: value})
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(result, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "runtime_contract"


@pytest.mark.parametrize(
    "which", ["state", "events", "conflict", "state_read", "events_read"]
)
def test_target_validation_and_oserror_classification(
    tmp_path: Path, which: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, events, _, _ = setup(tmp_path)
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
    elif which == "events_read":
        original = Path.read_bytes
        monkeypatch.setattr(
            Path,
            "read_bytes",
            lambda path: (
                (_ for _ in ()).throw(OSError()) if path == events else original(path)
            ),
        )
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, lambda *_: pytest.fail("called"))
    expected = (
        "state_target"
        if which in ("state", "state_read")
        else "event_target"
        if which in ("events", "events_read")
        else "target_conflict"
    )
    assert caught.value.detail.classification == expected


@pytest.mark.parametrize("target", ["state", "events", "both"])
@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
def test_dependency_mutation_is_compensated(
    tmp_path: Path, target: str, operation: str
) -> None:
    state, events, before_state, before_events = setup(tmp_path)

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"private")
        else:
            path.write_bytes(b"private")

    def dependency(*_: object) -> object:
        if target in ("state", "both"):
            mutate(state)
        if target in ("events", "both"):
            mutate(events)
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
@pytest.mark.parametrize("mutate", [False, True])
def test_dependency_errors_preserve_safe_identity_or_sanitize(
    tmp_path: Path, kind: str, mutate: bool
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    expected = ExecutedResultTransitionPersistencePhaseBridgeError("private")

    def dependency(*_: object) -> object:
        if mutate:
            state.write_bytes(b"private")
            events.write_bytes(b"private")
        if kind == "safe":
            raise expected
        raise RuntimeError("private")

    error_type = (
        ExecutedResultTransitionPersistencePhaseBridgeError
        if kind == "safe"
        else ExecutedResultTransitionPersistenceRoutingPhaseBridgeCompatibilityError
    )
    with pytest.raises(error_type) as caught:
        call(success(), workflow(), state, events, dependency)
    if kind == "safe":
        assert caught.value is expected
    else:
        assert caught.value.detail.classification == "dependency_error"
        assert "private" not in str(caught.value)
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    state, events, _, _ = setup(tmp_path)
    original = Path.write_bytes
    attempts: list[Path] = []

    def dependency(*_: object) -> object:
        original(state, b"private")
        original(events, b"private")
        raise RuntimeError("private")

    def restore(path: Path, data: bytes) -> int:
        attempts.append(path)
        if (
            failed == "both"
            or (failed == "state" and path == state)
            or (failed == "events" and path == events)
        ):
            raise OSError("rollback")
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "dependency_rollback"
    assert state in attempts and events in attempts


@pytest.mark.parametrize("outputs", [("", "output")])
def test_earlier_predecessor_exact_empty_output_delegates_once(
    tmp_path: Path, outputs: tuple[object, object]
) -> None:
    state, events = setup_three_step_history(tmp_path, outputs)
    supplied_workflow = three_step_workflow()
    supplied_result = three_step_success()
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(
            actual is wanted
            for actual, wanted in zip(
                args, (supplied_result, supplied_workflow, state, events), strict=True
            )
        )
        expected = persist_three_step(*args)  # type: ignore[arg-type]
        return expected

    assert (
        call(supplied_result, supplied_workflow, state, events, dependency) is expected
    )
    assert len(calls) == 1


@pytest.mark.parametrize("outputs", [("output", "")])
def test_immediate_predecessor_exact_empty_output_delegates_once(
    tmp_path: Path, outputs: tuple[object, object]
) -> None:
    state, events = setup_three_step_history(tmp_path, outputs)
    supplied_workflow = three_step_workflow()
    supplied_result = three_step_success()
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(
            actual is wanted
            for actual, wanted in zip(
                args, (supplied_result, supplied_workflow, state, events), strict=True
            )
        )
        expected = persist_three_step(*args)  # type: ignore[arg-type]
        return expected

    assert (
        call(supplied_result, supplied_workflow, state, events, dependency) is expected
    )
    assert len(calls) == 1


@pytest.mark.parametrize("outputs", [("", "")])
def test_combined_earlier_and_immediate_empty_outputs_delegate_once(
    tmp_path: Path, outputs: tuple[object, object]
) -> None:
    state, events = setup_three_step_history(tmp_path, outputs)
    supplied_workflow = three_step_workflow()
    supplied_result = three_step_success()
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def dependency(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(
            actual is wanted
            for actual, wanted in zip(
                args, (supplied_result, supplied_workflow, state, events), strict=True
            )
        )
        expected = persist_three_step(*args)  # type: ignore[arg-type]
        return expected

    assert (
        call(supplied_result, supplied_workflow, state, events, dependency) is expected
    )
    assert len(calls) == 1


@pytest.mark.parametrize("bad_output", [None, 123, True])
def test_invalid_predecessor_output_is_rejected_before_phase57(
    tmp_path: Path, bad_output: object
) -> None:
    state, events = setup_three_step_history(tmp_path, (bad_output, "output"))
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        pytest.fail("Phase 57 must not be called")

    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCompatibilityError
    ) as caught:
        call(three_step_success(), three_step_workflow(), state, events, dependency)
    assert caught.value.detail.classification == "runtime_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == before
