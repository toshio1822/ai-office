"""Focused Phase 50 bridge tests using injected Phase 43 fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionPersistenceBridgeCompatibilityError,
    PersistedExecutionOutcome,
    WorkflowProgressionDecision,
    route_executed_result_transition_persistence_bridge_reentry,
)
from ai_office.engine.executed_result_transition_routing_reentry import (
    ExecutedResultTransitionRoutingCompatibilityError,
    ExecutedResultTransitionRoutingError,
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


def success() -> StepRuntimeExecutionSuccess:
    return StepRuntimeExecutionSuccess(
        "workflow",
        "step",
        1,
        "employee",
        ModelInvocationSuccess(
            "openai", "response", "request", "done", ("out",), "out"
        ),
    )


def failure() -> StepRuntimeExecutionFailure:
    return StepRuntimeExecutionFailure(
        "workflow",
        "step",
        1,
        "employee",
        ModelInvocationFailure(
            "openai", "api_error", "safe", "request", None, None, None
        ),
    )


def setup(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    before = serialize_workflow_execution_state_json(
        WorkflowExecutionState("workflow", "running", "step", 1, "employee", (), None)
    ).encode()
    state.write_bytes(before)
    events.write_bytes(b"")
    return state, events, before, b""


def phase43_fake(
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    _workflow: WorkflowDefinition,
    state: Path,
    events: Path,
) -> WorkflowExecutionPersistenceResult:
    """Explicit fake Phase 43 persistence; never calls Phase 36."""
    success_result = type(result) is StepRuntimeExecutionSuccess
    invocation = result.invocation_result
    next_state = WorkflowExecutionState(
        "workflow",
        "succeeded" if success_result else "failed",
        "step",
        1,
        "employee",
        ("step",) if success_result else (),
        None if success_result else invocation.category,
    )
    event = RuntimeStepEvent(
        "step_succeeded" if success_result else "step_failed",
        "workflow",
        "step",
        1,
        "employee",
        "running",
        next_state.status,
        invocation.provider,
        None if success_result else invocation.category,
        invocation.response_id if success_result else None,
        invocation.request_id,
        invocation.text if success_result else None,
        None if success_result else invocation.message,
    )
    state_bytes = serialize_workflow_execution_state_json(next_state).encode()
    event_bytes = serialize_runtime_step_event_jsonl(event).encode()
    events.write_bytes(events.read_bytes() + event_bytes)
    state.write_bytes(state_bytes)
    return WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(event_bytes)
    )


def terminal_from_runtime(
    state: Path,
    events: Path,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
) -> None:
    phase43_fake(result, workflow(), state, events)


@pytest.mark.parametrize("result", [success(), failure()])
def test_runtime_routes_once_with_exact_arguments_and_same_result(
    tmp_path: Path, result: object
) -> None:
    state, events, _, _ = setup(tmp_path)
    supplied_workflow = workflow()
    calls = 0
    expected: WorkflowExecutionPersistenceResult | None = None

    def phase43(*args: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls, expected
        calls += 1
        assert (
            args[0] is result
            and args[1] is supplied_workflow
            and args[2] is state
            and args[3] is events
        )
        expected = phase43_fake(*args)  # type: ignore[arg-type]
        return expected

    returned = route_executed_result_transition_persistence_bridge_reentry(
        result,
        supplied_workflow,
        state,
        events,
        transition_routing_function=phase43,
    )
    assert returned is expected and calls == 1


@pytest.mark.parametrize("runtime", [success(), failure()])
def test_malformed_phase43_result_is_compensated(
    tmp_path: Path, runtime: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)

    def malformed(*_: object) -> object:
        state.write_bytes(b"bad")
        events.write_bytes(b"bad\n")
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            runtime, workflow(), state, events, transition_routing_function=malformed
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_phase43_safe_error_identity_is_preserved_after_rollback(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    expected = ExecutedResultTransitionRoutingCompatibilityError("result_type")

    def raises(*_: object) -> object:
        state.write_bytes(b"changed")
        raise expected

    with pytest.raises(ExecutedResultTransitionRoutingError) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            success(), workflow(), state, events, transition_routing_function=raises
        )
    assert caught.value is expected
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_persisted_success_is_rejected_without_phase43_or_writes(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    value = PersistedExecutionOutcome(
        "persisted_success", "workflow", "step", 1, "employee", None
    )
    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            value, workflow(), state, events, transition_routing_function=unexpected
        )
    assert caught.value.detail.classification == "failure_contract" and calls == 0
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_workflow_complete_is_read_only_and_returns_same_object(tmp_path: Path) -> None:
    state, events, _, _ = setup(tmp_path)
    terminal_from_runtime(state, events, success())
    before_state, before_events = state.read_bytes(), events.read_bytes()
    value = WorkflowProgressionDecision(
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

    def unexpected(*_: object) -> object:
        raise AssertionError

    returned = route_executed_result_transition_persistence_bridge_reentry(
        value, workflow(), state, events, transition_routing_function=unexpected
    )
    assert returned is value
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_persisted_failure_is_read_only_and_returns_same_object(tmp_path: Path) -> None:
    state, events, _, _ = setup(tmp_path)
    terminal_from_runtime(state, events, failure())
    before_state, before_events = state.read_bytes(), events.read_bytes()
    value = PersistedExecutionOutcome(
        "persisted_failure", "workflow", "step", 1, "employee", "api_error"
    )

    def unexpected(*_: object) -> object:
        raise AssertionError

    returned = route_executed_result_transition_persistence_bridge_reentry(
        value, workflow(), state, events, transition_routing_function=unexpected
    )
    assert returned is value
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


class SuccessSubclass(StepRuntimeExecutionSuccess):
    pass


class FailureSubclass(StepRuntimeExecutionFailure):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class PersistenceSubclass(WorkflowExecutionPersistenceResult):
    pass


def assert_prevalidation(
    tmp_path: Path,
    result: object,
    *,
    supplied_workflow: object | None = None,
    supplied_state: object | None = None,
    supplied_events: object | None = None,
    dependency: object = phase43_fake,
    classification: str,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def counted(*args: object) -> object:
        nonlocal calls
        calls += 1
        return None if dependency is None else dependency(*args)  # type: ignore[operator]

    function = counted if dependency is None or callable(dependency) else dependency
    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            result,
            workflow() if supplied_workflow is None else supplied_workflow,
            state if supplied_state is None else supplied_state,
            events if supplied_events is None else supplied_events,
            transition_routing_function=function,  # type: ignore[arg-type]
        )
    assert caught.value.detail.classification == classification
    assert str(caught.value) == (
        "executed-result transition persistence bridge inputs are incompatible"
    )
    assert calls == 0
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


@pytest.mark.parametrize(
    ("result", "classification"),
    [
        (object(), "result_type"),
        (SuccessSubclass(*success().__dict__.values()), "result_type"),
        (FailureSubclass(*failure().__dict__.values()), "result_type"),
        (
            type("CompatibleSuccess", (), success().__dict__)(),
            "result_type",
        ),
        (
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
            "result_type",
        ),
        (type("CompatibleDecision", (), {})(), "result_type"),
        (
            OutcomeSubclass(
                "persisted_failure", "workflow", "step", 1, "employee", "api_error"
            ),
            "result_type",
        ),
        (type("CompatibleOutcome", (), {})(), "result_type"),
    ],
)
def test_exact_top_level_types_are_required(
    tmp_path: Path, result: object, classification: str
) -> None:
    assert_prevalidation(
        tmp_path, result, dependency=None, classification=classification
    )


@pytest.mark.parametrize(
    ("workflow_value", "state_value", "events_value", "dependency", "classification"),
    [
        (
            WorkflowSubclass(**workflow().__dict__),
            None,
            None,
            None,
            "workflow_definition",
        ),
        (type("WorkflowLike", (), {})(), None, None, None, "workflow_definition"),
        (None, "not-a-path", None, None, "state_target"),
        (None, None, "not-a-path", None, "event_target"),
        (None, "same", "same", None, "target_conflict"),
        (None, None, None, object(), "persistence_contract"),
    ],
)
def test_input_contract_prevalidation(
    tmp_path: Path,
    workflow_value: object | None,
    state_value: object | None,
    events_value: object | None,
    dependency: object,
    classification: str,
) -> None:
    state, events, _, _ = setup(tmp_path)
    if state_value == "same":
        state_value = events_value = state
    assert_prevalidation(
        tmp_path,
        success(),
        supplied_workflow=workflow_value,
        supplied_state=state_value,
        supplied_events=events_value,
        dependency=dependency,
        classification=classification,
    )


@pytest.mark.parametrize(
    "result",
    [
        replace(success(), workflow_id="other"),
        replace(success(), step_id="other"),
        replace(success(), step_index=2),
        replace(success(), employee_id="other"),
        replace(success(), invocation_result=object()),
        replace(failure(), workflow_id="other"),
        replace(failure(), step_id="other"),
        replace(failure(), step_index=2),
        replace(failure(), employee_id="other"),
        replace(failure(), invocation_result=object()),
    ],
)
def test_runtime_contract_fields_are_prevalidated(
    tmp_path: Path, result: object
) -> None:
    assert_prevalidation(
        tmp_path, result, dependency=None, classification="runtime_contract"
    )


@pytest.mark.parametrize(
    "result",
    [
        replace(
            success(),
            invocation_result=replace(success().invocation_result, provider="other"),
        ),
        replace(
            success(),
            invocation_result=replace(success().invocation_result, response_id=""),
        ),
        replace(
            success(),
            invocation_result=replace(success().invocation_result, request_id=""),
        ),
        replace(
            success(),
            invocation_result=replace(
                success().invocation_result, text_parts=("different",)
            ),
        ),
        replace(
            success(),
            invocation_result=replace(success().invocation_result, text=object()),
        ),
        replace(
            failure(),
            invocation_result=replace(failure().invocation_result, provider="other"),
        ),
        replace(
            failure(),
            invocation_result=replace(failure().invocation_result, message=""),
        ),
        replace(
            failure(),
            invocation_result=replace(failure().invocation_result, request_id=""),
        ),
        replace(
            failure(),
            invocation_result=replace(failure().invocation_result, status_code="bad"),
        ),
        replace(
            failure(),
            invocation_result=replace(
                failure().invocation_result, provider_error_type=1
            ),
        ),
        replace(
            failure(),
            invocation_result=replace(
                failure().invocation_result, provider_error_code=1
            ),
        ),
    ],
)
def test_runtime_provider_request_response_output_and_failure_fields_are_prevalidated(
    tmp_path: Path, result: object
) -> None:
    assert_prevalidation(
        tmp_path, result, dependency=None, classification="runtime_contract"
    )


@pytest.mark.parametrize(
    "state_value",
    [
        WorkflowExecutionState("workflow", "ready", "step", 1, "employee", (), None),
        WorkflowExecutionState(
            "workflow", "failed", "step", 1, "employee", (), "api_error"
        ),
        WorkflowExecutionState(
            "workflow", "running", "step", 1, "employee", (), "api_error"
        ),
        WorkflowExecutionState(
            "workflow", "running", "step", 1, "employee", ("step",), None
        ),
    ],
)
def test_running_state_contract_is_prevalidated(
    tmp_path: Path, state_value: WorkflowExecutionState
) -> None:
    state, events, _, _ = setup(tmp_path)
    state.write_bytes(serialize_workflow_execution_state_json(state_value).encode())
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return None

    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            success(), workflow(), state, events, transition_routing_function=dependency
        )
    assert caught.value.detail.classification == "runtime_contract" and calls == 0


@pytest.mark.parametrize(
    "change",
    [
        {"decision": "prepare_next_step"},
        {"workflow_id": "other"},
        {"current_step_id": "other"},
        {"current_step_index": 2},
        {"current_employee_id": "other"},
        {"next_step_id": "step"},
        {"next_step_index": 1},
        {"next_employee_id": "employee"},
        {"reason": "wrong"},
    ],
)
def test_completion_fields_are_prevalidated(
    tmp_path: Path, change: dict[str, object]
) -> None:
    value = replace(
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
        ),
        **change,
    )
    assert_prevalidation(
        tmp_path, value, dependency=None, classification="completion_contract"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"outcome": "persisted_success"},
        {"workflow_id": "other"},
        {"current_step_id": "other"},
        {"current_step_index": 2},
        {"current_employee_id": "other"},
        {"failure_category": None},
        {"failure_category": "wrong"},
    ],
)
def test_failure_fields_are_prevalidated(
    tmp_path: Path, change: dict[str, object]
) -> None:
    value = replace(
        PersistedExecutionOutcome(
            "persisted_failure", "workflow", "step", 1, "employee", "api_error"
        ),
        **change,
    )
    assert_prevalidation(
        tmp_path, value, dependency=None, classification="failure_contract"
    )


def assert_persistence_rejected(
    tmp_path: Path,
    fake: object,
    *,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | None = None,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def counted(*args: object) -> object:
        nonlocal calls
        calls += 1
        return fake(*args)  # type: ignore[operator]

    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            success() if result is None else result,
            workflow(),
            state,
            events,
            transition_routing_function=counted,
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        PersistenceSubclass(Path("state"), Path("events"), 1, 1),
        type("PersistenceLike", (), {"state_path": Path("x")})(),
    ],
)
def test_exact_phase43_return_type_is_required(
    tmp_path: Path, returned: object
) -> None:
    def fake(*args: object) -> object:
        phase43_fake(*args)  # type: ignore[arg-type]
        return returned

    assert_persistence_rejected(tmp_path, fake)


@pytest.mark.parametrize(
    "factory",
    [
        lambda state, events: WorkflowExecutionPersistenceResult(events, state, 1, 1),
        lambda state, events: WorkflowExecutionPersistenceResult(
            state, events, True, 1
        ),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, "1", 1),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 1.0, 1),
        lambda state, events: WorkflowExecutionPersistenceResult(
            state, events, None, 1
        ),
        lambda state, events: WorkflowExecutionPersistenceResult(
            state, events, 1, True
        ),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 1, "1"),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 1, 1.0),
        lambda state, events: WorkflowExecutionPersistenceResult(
            state, events, 1, None
        ),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 0, 1),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 1, 0),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, -1, 1),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 1, -1),
        lambda state, events: WorkflowExecutionPersistenceResult(
            state, events, 999, 999
        ),
    ],
)
def test_phase43_return_fields_are_required(tmp_path: Path, factory: object) -> None:
    def fake(*args: object) -> object:
        phase43_fake(*args)  # type: ignore[arg-type]
        state, events = args[2], args[3]
        return factory(state, events)  # type: ignore[operator]

    assert_persistence_rejected(tmp_path, fake)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state, events: state.write_bytes(b"replace"),
        lambda state, events: state.unlink(),
        lambda state, events: state.write_bytes(b""),
        lambda state, events: state.write_bytes(state.read_bytes() + b"extra"),
        lambda state, events: events.write_bytes(b"replace"),
        lambda state, events: events.unlink(),
        lambda state, events: events.write_bytes(b""),
        lambda state, events: events.write_bytes(events.read_bytes() + b"extra"),
        lambda state, events: (
            state.write_bytes(b"replace"),
            events.write_bytes(b"replace"),
        ),
    ],
)
def test_partial_phase43_side_effects_are_compensated(
    tmp_path: Path, mutation: object
) -> None:
    def fake(_result: object, _workflow: object, state: Path, events: Path) -> object:
        mutation(state, events)  # type: ignore[operator]
        return object()

    assert_persistence_rejected(tmp_path, fake)


@pytest.mark.parametrize("result", [success(), failure()])
def test_final_state_event_counts_and_original_prefix(
    tmp_path: Path, result: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def fake(*args: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls
        calls += 1
        return phase43_fake(*args)  # type: ignore[arg-type]

    returned = route_executed_result_transition_persistence_bridge_reentry(
        result, workflow(), state, events, transition_routing_function=fake
    )
    assert type(returned) is WorkflowExecutionPersistenceResult and calls == 1
    assert returned.state_bytes_written == len(state.read_bytes())
    assert returned.event_bytes_appended == len(events.read_bytes()) - len(
        before_events
    )
    assert events.read_bytes().startswith(before_events)
    assert state.read_bytes() != before_state and events.read_bytes() != before_events


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state, events: None,
        lambda state, events: state.write_bytes(b"replace"),
        lambda state, events: state.unlink(),
        lambda state, events: state.write_bytes(b""),
        lambda state, events: state.write_bytes(state.read_bytes() + b"extra"),
        lambda state, events: events.write_bytes(b"replace"),
        lambda state, events: events.unlink(),
        lambda state, events: events.write_bytes(b""),
        lambda state, events: events.write_bytes(events.read_bytes() + b"extra"),
        lambda state, events: (
            state.write_bytes(b"replace"),
            events.write_bytes(b"replace"),
        ),
    ],
)
def test_safe_phase43_errors_preserve_identity_and_compensate(
    tmp_path: Path, mutation: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    expected = ExecutedResultTransitionRoutingCompatibilityError("result_type")
    calls = 0

    def fake(_result: object, _workflow: object, state: Path, events: Path) -> object:
        nonlocal calls
        calls += 1
        mutation(state, events)  # type: ignore[operator]
        raise expected

    with pytest.raises(ExecutedResultTransitionRoutingError) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            success(), workflow(), state, events, transition_routing_function=fake
        )
    assert caught.value is expected and calls == 1
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state, events: None,
        lambda state, events: state.write_bytes(b"replace"),
        lambda state, events: state.unlink(),
        lambda state, events: state.write_bytes(b""),
        lambda state, events: state.write_bytes(state.read_bytes() + b"extra"),
        lambda state, events: events.write_bytes(b"replace"),
        lambda state, events: events.unlink(),
        lambda state, events: events.write_bytes(b""),
        lambda state, events: events.write_bytes(events.read_bytes() + b"extra"),
        lambda state, events: (
            state.write_bytes(b"replace"),
            events.write_bytes(b"replace"),
        ),
    ],
)
def test_unexpected_phase43_errors_are_sanitized_and_compensated(
    tmp_path: Path, mutation: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def fake(_result: object, _workflow: object, state: Path, events: Path) -> object:
        nonlocal calls
        calls += 1
        mutation(state, events)  # type: ignore[operator]
        raise RuntimeError("request=response output failure /secret/path raw-bytes")

    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            success(), workflow(), state, events, transition_routing_function=fake
        )
    assert caught.value.detail.classification == "dependency_error" and calls == 1
    assert state.read_bytes() == before_state and events.read_bytes() == before_events
    assert "secret" not in str(caught.value) and "path" not in str(caught.value)


@pytest.mark.parametrize("outcome", ["return", "safe", "unexpected"])
@pytest.mark.parametrize("rollback_failure", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_and_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    rollback_failure: str,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    original_write = Path.write_bytes
    restore_attempts: list[Path] = []

    def write_bytes(path: Path, data: bytes) -> int:
        restoring = (path == state and data == before_state) or (
            path == events and data == before_events
        )
        if restoring:
            restore_attempts.append(path)
            if rollback_failure == "both" or rollback_failure == path.name.removesuffix(
                ".json"
            ).removesuffix(".jsonl"):
                raise OSError("private rollback detail")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    calls = 0
    safe_error = ExecutedResultTransitionRoutingCompatibilityError("result_type")

    def fake(_result: object, _workflow: object, state: Path, events: Path) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"mutation")
        events.write_bytes(b"mutation")
        if outcome == "safe":
            raise safe_error
        if outcome == "unexpected":
            raise RuntimeError("secret traceback path")
        return object()

    with pytest.raises(
        ExecutedResultTransitionPersistenceBridgeCompatibilityError
    ) as caught:
        route_executed_result_transition_persistence_bridge_reentry(
            success(), workflow(), state, events, transition_routing_function=fake
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert state in restore_attempts and events in restore_attempts
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)


@pytest.mark.parametrize("terminal", ["complete", "failure"])
def test_terminal_routes_do_not_write_or_call_phase43(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: str
) -> None:
    state, events, _, _ = setup(tmp_path)
    runtime = success() if terminal == "complete" else failure()
    terminal_from_runtime(state, events, runtime)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    value: WorkflowProgressionDecision | PersistedExecutionOutcome
    if terminal == "complete":
        value = WorkflowProgressionDecision(
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
    else:
        value = PersistedExecutionOutcome(
            "persisted_failure", "workflow", "step", 1, "employee", "api_error"
        )
    original_write = Path.write_bytes
    writes = 0

    def write_bytes(path: Path, data: bytes) -> int:
        nonlocal writes
        writes += 1
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_executed_result_transition_persistence_bridge_reentry(
            value, workflow(), state, events, transition_routing_function=dependency
        )
        is value
    )
    assert calls == writes == 0
    assert state.read_bytes() == before_state and events.read_bytes() == before_events
