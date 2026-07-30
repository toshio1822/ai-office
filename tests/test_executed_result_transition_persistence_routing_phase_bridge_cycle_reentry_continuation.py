"""Phase 85 cycle reentry continuation boundary contract tests."""

# ruff: noqa: E501

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationError,
    ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_executed_result_transition_persistence_routing_phase_bridge_cycle_reentry_continuation,
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


def setup(tmp_path: Path, status: str = "running") -> tuple[Path, Path]:
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
    return state_path, events_path


def two_step_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "e", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "e", "instructions": "two"},
            ],
        }
    )


def predecessor_event(step_id: str, step_index: int) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded", "w", step_id, step_index, "e", "running", "succeeded",
        "openai", None, f"response-{step_id}", f"request-{step_id}", "output", None
    )


def setup_two_step_history(tmp_path: Path, mode: str) -> tuple[Path, Path]:
    state = WorkflowExecutionState("w", "running", "two", 2, "e", ("one",), None)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    first = serialize_runtime_step_event_jsonl(predecessor_event("one", 1))
    second = serialize_runtime_step_event_jsonl(predecessor_event("two", 2))
    events_path.write_text({"valid": first, "missing": "", "duplicate": first + first, "reordered": second + first}[mode])
    return state_path, events_path


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


def call(
    result: object,
    workflow_value: object,
    state: object,
    events: object,
    dependency: object,
) -> object:
    return (
        route_executed_result_transition_persistence_routing_phase_bridge_cycle_reentry_continuation(
            result, workflow_value, state, events, phase78_function=dependency
        )
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("result", [success(), failure()])
def test_valid_routes_delegate_once_and_return_exact_dependency_object(
    tmp_path: Path, result: object
) -> None:
    state, events = setup(tmp_path)
    supplied_workflow = workflow()
    calls: list[tuple[object, ...]] = []
    expected: object = None

    def phase64(*args: object) -> object:
        nonlocal expected
        calls.append(args)
        assert all(
            actual is wanted
            for actual, wanted in zip(
                args, (result, supplied_workflow, state, events), strict=True
            )
        )
        expected = persist_fake(*args)  # type: ignore[arg-type]
        return expected

    assert call(result, supplied_workflow, state, events, phase64) is expected
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["complete", "failure"])
def test_stop_routes_return_identity_without_dependency(
    tmp_path: Path, kind: str
) -> None:
    state, events = setup(tmp_path, "succeeded" if kind == "complete" else "failed")
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
    before = state.read_bytes(), events.read_bytes()
    assert (
        call(result, workflow(), state, events, lambda *_: pytest.fail("called"))
        is result
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "value",
    [
        object(),
        SimpleNamespace(workflow_id="w"),
        PersistenceResultSubclass(Path("x"), Path("y"), 1, 1),
    ],
)
def test_invalid_result_types_reject_before_dependency(
    tmp_path: Path, value: object
) -> None:
    state, events = setup(tmp_path)
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(value, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "value",
    [SuccessSubclass(**success().__dict__), FailureSubclass(**failure().__dict__)],
)
def test_runtime_subclasses_reject_before_dependency(
    tmp_path: Path, value: object
) -> None:
    state, events = setup(tmp_path)
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(value, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "wrong"),
        ("step_id", "wrong"),
        ("step_index", True),
        ("step_index", 2),
        ("employee_id", "wrong"),
    ],
)
def test_runtime_result_fields_are_revalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = setup(tmp_path)
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(
            replace(success(), **{field: value}),
            workflow(),
            state,
            events,
            lambda *_: pytest.fail("called"),
        )
    assert caught.value.detail.classification == "runtime_contract"


def test_running_state_and_predecessor_history_are_revalidated(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    state.write_text(
        serialize_workflow_execution_state_json(
            WorkflowExecutionState("w", "succeeded", "step", 1, "e", ("step",), None)
        )
    )
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "runtime_contract"


@pytest.mark.parametrize("mode", ["missing", "duplicate", "reordered"])
def test_missing_duplicate_and_reordered_predecessor_history_is_rejected(
    tmp_path: Path, mode: str
) -> None:
    state, events = setup_two_step_history(tmp_path, mode)
    result = StepRuntimeExecutionSuccess(
        "w", "two", 2, "e", ModelInvocationSuccess("openai", "r", "q", "done", ("out",), "out")
    )
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(result, two_step_workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "runtime_contract"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value, state, events: replace(value, state_path=Path("wrong")),
        lambda value, state, events: replace(value, events_path=Path("wrong")),
        lambda value, state, events: replace(value, state_bytes_written=True),
        lambda value, state, events: replace(value, event_bytes_appended=True),
    ],
)
def test_persistence_result_paths_and_byte_counts_are_exact(
    tmp_path: Path, mutator: object
) -> None:
    state, events = setup(tmp_path)

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        return mutator(value, state, events)  # type: ignore[operator]

    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize("field", ["state_path", "events_path"])
def test_equal_but_nonidentical_persistence_path_is_rejected(
    tmp_path: Path, field: str
) -> None:
    state, events = setup(tmp_path)

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        replacement = Path(str(state if field == "state_path" else events))
        return replace(value, **{field: replacement})

    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"


@pytest.mark.parametrize(
    "result,field,value",
    [
        (success(), "provider", "other"),
        (success(), "response_id", None),
        (success(), "request_id", True),
        (success(), "status", None),
        (success(), "text_parts", ["out"]),
        (success(), "text", "wrong"),
        (failure(), "provider", "other"),
        (failure(), "category", "unknown"),
        (failure(), "message", None),
        (failure(), "request_id", True),
        (failure(), "status_code", True),
        (failure(), "provider_error_type", True),
        (failure(), "provider_error_code", True),
    ],
)
def test_runtime_payload_fields_and_exact_types_are_revalidated(
    tmp_path: Path, result: object, field: str, value: object
) -> None:
    state, events = setup(tmp_path)
    invalid = replace(
        result,
        invocation_result=replace(result.invocation_result, **{field: value}),  # type: ignore[union-attr]
    )
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(invalid, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "runtime_contract"


def test_direct_persisted_success_is_rejected(tmp_path: Path) -> None:
    state, events = setup(tmp_path, "succeeded")
    result = PersistedExecutionOutcome("persisted_success", "w", "step", 1, "e", None)
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(result, workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == "failure_contract"


def test_extra_history_event_is_rejected_and_restored(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*args: object) -> object:
        value = persist_fake(*args)  # type: ignore[arg-type]
        events.write_bytes(events.read_bytes() + events.read_bytes())
        return value

    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("name", ["workflow", "state_path", "events_path"])
def test_top_level_and_target_contracts_reject_before_dependency(
    tmp_path: Path, name: str
) -> None:
    state, events = setup(tmp_path)
    if name == "workflow":
        with pytest.raises(
            ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
        ) as caught:
            call(
                success(),
                WorkflowSubclass.model_validate(workflow().model_dump()),
                state,
                events,
                lambda *_: pytest.fail("called"),
            )
        assert caught.value.detail.classification == "workflow_definition"
        return
    path = state if name == "state_path" else events
    path.unlink()
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == (
        "state_target" if name == "state_path" else "event_target"
    )


@pytest.mark.parametrize("target", ["state", "events"])
def test_directory_target_is_rejected_before_dependency(
    tmp_path: Path, target: str
) -> None:
    state, events = setup(tmp_path)
    path = state if target == "state" else events
    path.unlink()
    path.mkdir()
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )


@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
@pytest.mark.parametrize("target", ["state", "events"])
def test_target_oserror_is_classified_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, target: str
) -> None:
    state, events = setup(tmp_path)
    original = getattr(Path, operation)
    target_path = state if target == "state" else events

    def fail(path: Path, *args: object, **kwargs: object) -> object:
        if path == target_path:
            raise OSError("synthetic")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail)
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )


@pytest.mark.parametrize(
    "dependency_result",
    [object(), WorkflowExecutionPersistenceResult(Path("wrong"), Path("wrong"), 1, 1)],
)
def test_malformed_persistence_return_is_rejected_and_restored(
    tmp_path: Path, dependency_result: object
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*_: object) -> object:
        state.write_bytes(b"mutated")
        events.write_bytes(b"mutated")
        return dependency_result

    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_invalid_transition_is_compensated(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def dependency(*_: object) -> object:
        state.write_bytes(
            serialize_workflow_execution_state_json(
                WorkflowExecutionState("w", "failed", "step", 1, "e", (), "api_error")
            ).encode()
        )
        return WorkflowExecutionPersistenceResult(
            state, events, len(state.read_bytes()), 1
        )

    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_safe_error_identity_and_unexpected_error_sanitization(tmp_path: Path) -> None:
    state, events = setup(tmp_path)
    safe = ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationError("private")
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationError
    ) as caught:
        call(
            success(), workflow(), state, events, lambda *_: (_ for _ in ()).throw(safe)
        )
    assert caught.value is safe
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(
            success(),
            workflow(),
            state,
            events,
            lambda *_: (_ for _ in ()).throw(RuntimeError("private")),
        )
    assert (
        caught.value.detail.classification == "dependency_error"
        and "private" not in str(caught.value)
    )


def test_safe_error_identity_is_preserved_after_successful_compensation(
    tmp_path: Path,
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationError(
        "private"
    )

    def dependency(*_: object) -> object:
        state.write_bytes(b"mutated")
        events.write_bytes(b"mutated")
        raise safe

    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleContinuationError
    ) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value is safe
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_mutations_are_compensated_and_dependency_is_called_once(
    tmp_path: Path, target: str
) -> None:
    state, events = setup(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if target in {"state", "both"}:
            state.write_bytes(b"mutated")
        if target in {"events", "both"}:
            events.write_bytes(b"mutated")
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ):
        call(success(), workflow(), state, events, dependency)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failures_attempt_both_targets_and_do_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    state, events = setup(tmp_path)
    original_write = Path.write_bytes
    attempts: list[Path] = []
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        original_write(state, b"mutated")
        original_write(events, b"mutated")
        return object()

    def restore(path: Path, data: bytes) -> int:
        attempts.append(path)
        if (
            failed == "both"
            or (failed == "state" and path == state)
            or (failed == "events" and path == events)
        ):
            raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", restore)
    with pytest.raises(
        ExecutedResultTransitionPersistenceRoutingPhaseBridgeCycleReentryContinuationCompatibilityError
    ) as caught:
        call(success(), workflow(), state, events, dependency)
    assert caught.value.detail.classification == "dependency_rollback"
    assert attempts.count(state) == 1 and attempts.count(events) == 1 and calls == 1

