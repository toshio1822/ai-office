"""Tests for Phase 43 with injected transition reentry fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ExecutedResultTransitionReentryCompatibilityError,
    ExecutedResultTransitionRoutingCompatibilityError,
    WorkflowProgressionDecision,
    persist_executed_result_transition_reentry,
    route_executed_result_transition_reentry,
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


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class PersistenceSubclass(WorkflowExecutionPersistenceResult):
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


def success(**changes: object) -> StepRuntimeExecutionSuccess:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "step_id": "step",
        "step_index": 1,
        "employee_id": "employee",
        "invocation_result": ModelInvocationSuccess(
            "openai", "response", "request", "done", ("out",), "out"
        ),
    }
    values.update(changes)
    return StepRuntimeExecutionSuccess(**values)  # type: ignore[arg-type]


def failure(**changes: object) -> StepRuntimeExecutionFailure:
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "step_id": "step",
        "step_index": 1,
        "employee_id": "employee",
        "invocation_result": ModelInvocationFailure(
            "openai", "api_error", "safe", "request", None, None, None
        ),
    }
    values.update(changes)
    return StepRuntimeExecutionFailure(**values)  # type: ignore[arg-type]


def setup(tmp_path: Path) -> tuple[Path, Path, bytes, bytes]:
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    before = serialize_workflow_execution_state_json(
        WorkflowExecutionState("workflow", "running", "step", 1, "employee", (), None)
    ).encode()
    state.write_bytes(before)
    events.write_bytes(b"")
    return state, events, before, b""


def completion(**changes: object) -> WorkflowProgressionDecision:
    values: dict[str, object] = {
        "decision": "workflow_complete",
        "workflow_id": "workflow",
        "current_step_id": "step",
        "current_step_index": 1,
        "current_employee_id": "employee",
        "next_step_id": None,
        "next_step_index": None,
        "next_employee_id": None,
        "reason": "last_step_succeeded",
    }
    values.update(changes)
    return WorkflowProgressionDecision(**values)  # type: ignore[arg-type]


def assert_prevalidation_rejected(
    state: Path,
    events: Path,
    result: object,
    workflow_value: object = None,
    *,
    state_value: object | None = None,
    events_value: object | None = None,
    dependency: object = lambda *_: None,
    classification: str | None = None,
) -> None:
    before_state = state.read_bytes() if state.is_file() else None
    before_events = events.read_bytes() if events.is_file() else None
    calls = 0

    def counted(*_: object) -> object:
        nonlocal calls
        calls += 1
        return None

    function = counted if dependency is None else dependency
    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            result,
            workflow() if workflow_value is None else workflow_value,
            state if state_value is None else state_value,
            events if events_value is None else events_value,
            transition_reentry_function=function,  # type: ignore[arg-type]
        )
    if classification is not None:
        assert caught.value.detail.classification == classification
    assert calls == 0
    if before_state is not None:
        assert state.read_bytes() == before_state
    if before_events is not None:
        assert events.read_bytes() == before_events


@pytest.mark.parametrize("result", [success(), failure()])
def test_runtime_result_routes_once_and_returns_exact_persistence_object(
    tmp_path: Path, result: object
) -> None:
    state, events, _, _ = setup(tmp_path)
    calls = 0
    expected = None

    def transition_reentry_fake(*args: object):
        nonlocal calls, expected
        calls += 1
        assert args == (result, workflow(), state, events)
        expected = persist_executed_result_transition_reentry(*args)
        return expected

    assert (
        route_executed_result_transition_reentry(
            result,
            workflow(),
            state,
            events,
            transition_reentry_function=transition_reentry_fake,
        )
        is expected
    )
    assert calls == 1


def test_completion_returns_same_object_without_dependency_or_write(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
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

    def unexpected(*_: object):
        nonlocal calls
        calls += 1
        raise AssertionError

    assert (
        route_executed_result_transition_reentry(
            decision, workflow(), state, events, transition_reentry_function=unexpected
        )
        is decision
    )
    assert calls == 0
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_malformed_return_is_compensated(tmp_path: Path) -> None:
    state, events, before_state, before_events = setup(tmp_path)

    def invalid(*_: object):
        state.write_bytes(b"bad")
        events.write_bytes(b"bad\n")
        return object()

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            success(), workflow(), state, events, transition_reentry_function=invalid
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def test_safe_dependency_error_is_preserved_after_compensation(tmp_path: Path) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    expected = ExecutedResultTransitionReentryCompatibilityError("result_type")

    def invalid(*_: object):
        state.write_bytes(b"bad")
        events.write_bytes(b"bad\n")
        raise expected

    with pytest.raises(ExecutedResultTransitionReentryCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            success(), workflow(), state, events, transition_reentry_function=invalid
        )
    assert caught.value is expected
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


@pytest.mark.parametrize(
    ("decision", "target_kind"),
    [
        (completion(decision="prepare_next_step"), "missing"),
        (completion(reason="wrong"), "broken"),
    ],
)
def test_completion_contract_precedes_target_validation(
    tmp_path: Path, decision: WorkflowProgressionDecision, target_kind: str
) -> None:
    state, events, _, _ = setup(tmp_path)
    if target_kind == "missing":
        state.unlink()
    else:
        events.write_bytes(b"not valid json\n")
    calls = 0

    def unexpected(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            decision, workflow(), state, events, transition_reentry_function=unexpected
        )
    assert caught.value.detail.classification == "completion_contract"
    assert calls == 0


@pytest.mark.parametrize(
    ("result", "workflow_value", "state_value", "events_value", "dependency", "kind"),
    [
        (
            SuccessSubclass(*success().__dict__.values()),
            None,
            None,
            None,
            None,
            "result_type",
        ),
        (
            FailureSubclass(*failure().__dict__.values()),
            None,
            None,
            None,
            None,
            "result_type",
        ),
        (
            DecisionSubclass(*completion().__dict__.values()),
            None,
            None,
            None,
            None,
            "result_type",
        ),
        (
            type("Compatible", (), success().__dict__)(),
            None,
            None,
            None,
            None,
            "result_type",
        ),
        (
            success(),
            WorkflowSubclass(**workflow().__dict__),
            None,
            None,
            None,
            "workflow_definition",
        ),
        (success(), None, "not-a-path", None, None, "state_target"),
        (success(), None, None, "not-a-path", None, "event_target"),
        (success(), None, "same", "same", None, "target_conflict"),
        (success(), None, None, None, object(), "persistence_contract"),
    ],
)
def test_exact_type_and_top_level_prevalidation(
    tmp_path: Path,
    result: object,
    workflow_value: object,
    state_value: object,
    events_value: object,
    dependency: object,
    kind: str,
) -> None:
    state, events, _, _ = setup(tmp_path)
    if state_value == "same":
        state_value = events_value = state
    assert_prevalidation_rejected(
        state,
        events,
        result,
        workflow_value,
        state_value=state_value,
        events_value=events_value,
        dependency=dependency,
        classification=kind,
    )


@pytest.mark.parametrize(
    "result",
    [
        success(workflow_id="other"),
        success(step_id="other"),
        success(step_index=2),
        success(employee_id="other"),
        replace(success(), invocation_result=object()),
        failure().__class__("workflow", "step", 1, "employee", object()),
    ],
)
def test_runtime_identity_and_payload_prevalidation(
    tmp_path: Path, result: object
) -> None:
    state, events, _, _ = setup(tmp_path)
    assert_prevalidation_rejected(
        state, events, result, dependency=None, classification="result_contract"
    )


@pytest.mark.parametrize(
    "state_value",
    [
        WorkflowExecutionState("workflow", "ready", "step", 1, "employee", (), None),
        WorkflowExecutionState(
            "workflow", "failed", "step", 1, "employee", (), "api_error"
        ),
        WorkflowExecutionState("other", "running", "step", 1, "employee", (), None),
        WorkflowExecutionState("workflow", "running", "other", 1, "employee", (), None),
        WorkflowExecutionState("workflow", "running", "step", 1, "other", (), None),
        WorkflowExecutionState(
            "workflow", "running", "step", 1, "employee", ("step",), None
        ),
    ],
)
def test_persisted_running_contract_prevalidation(
    tmp_path: Path, state_value: WorkflowExecutionState
) -> None:
    state, events, _, _ = setup(tmp_path)
    state.write_bytes(serialize_workflow_execution_state_json(state_value).encode())
    assert_prevalidation_rejected(
        state, events, success(), dependency=None, classification="result_contract"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": "other"},
        {"current_step_id": "other"},
        {"current_step_index": 2},
        {"current_employee_id": "other"},
        {"next_step_id": "next"},
        {"next_step_index": 2},
        {"next_employee_id": "other"},
        {"reason": "wrong"},
    ],
)
def test_completion_field_prevalidation(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events, _, _ = setup(tmp_path)
    assert_prevalidation_rejected(
        state,
        events,
        completion(**changes),
        dependency=None,
        classification="completion_contract",
    )


def test_missing_targets_and_invalid_history_reject_without_dependency(
    tmp_path: Path,
) -> None:
    state, events, _, _ = setup(tmp_path)
    state.unlink()
    assert_prevalidation_rejected(
        state, events, success(), dependency=None, classification="state_target"
    )
    state, events, _, _ = setup(tmp_path)
    events.unlink()
    assert_prevalidation_rejected(
        state, events, success(), dependency=None, classification="event_target"
    )
    state, events, _, _ = setup(tmp_path)
    state.write_bytes(b"bad")
    assert_prevalidation_rejected(
        state, events, success(), dependency=None, classification="result_contract"
    )
    state, events, _, _ = setup(tmp_path)
    events.write_bytes(b"bad\n")
    assert_prevalidation_rejected(
        state, events, success(), dependency=None, classification="result_contract"
    )


def valid_persistence(
    state: Path, events: Path, result: object
) -> WorkflowExecutionPersistenceResult:
    return persist_executed_result_transition_reentry(result, workflow(), state, events)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "returned",
    [object(), PersistenceSubclass(Path("a"), Path("b"), 1, 1)],
)
def test_wrong_persistence_return_types_are_compensated(
    tmp_path: Path, returned: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def transition_reentry_fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        valid_persistence(state, events, success())
        return returned

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            success(),
            workflow(),
            state,
            events,
            transition_reentry_function=transition_reentry_fake,
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert (
        calls == 1
        and state.read_bytes() == before_state
        and events.read_bytes() == before_events
    )


@pytest.mark.parametrize(
    "returned",
    [
        lambda state, events: WorkflowExecutionPersistenceResult(events, state, 1, 1),
        lambda state, events: WorkflowExecutionPersistenceResult(
            state, events, True, 1
        ),
        lambda state, events: WorkflowExecutionPersistenceResult(
            state, events, 1, True
        ),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 0, 1),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 1, 0),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, -1, 1),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 1, -1),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, "1", 1),
        lambda state, events: WorkflowExecutionPersistenceResult(state, events, 1, "1"),
        lambda state, events: WorkflowExecutionPersistenceResult(
            state, events, 999, 999
        ),
    ],
)
def test_persistence_result_fields_are_validated_and_compensated(
    tmp_path: Path, returned: object
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def transition_reentry_fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        valid_persistence(state, events, success())
        return returned(state, events)  # type: ignore[operator]

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            success(),
            workflow(),
            state,
            events,
            transition_reentry_function=transition_reentry_fake,
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


def _mutate(path: Path, mode: str) -> None:
    if mode == "replace":
        path.write_bytes(b"replaced")
    elif mode == "delete":
        path.unlink()
    elif mode == "truncate":
        path.write_bytes(b"")
    else:
        path.write_bytes(path.read_bytes() + b"append")


def terminal_state(
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    **changes: object,
) -> WorkflowExecutionState:
    success_result = type(result) is StepRuntimeExecutionSuccess
    values: dict[str, object] = {
        "workflow_id": "workflow",
        "status": "succeeded" if success_result else "failed",
        "current_step_id": "step",
        "current_step_index": 1,
        "current_employee_id": "employee",
        "completed_step_ids": ("step",) if success_result else (),
        "last_failure_category": None if success_result else "api_error",
    }
    values.update(changes)
    return WorkflowExecutionState(**values)  # type: ignore[arg-type]


def terminal_event(
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    **changes: object,
) -> RuntimeStepEvent:
    success_result = type(result) is StepRuntimeExecutionSuccess
    values: dict[str, object] = {
        "event_type": "step_succeeded" if success_result else "step_failed",
        "workflow_id": "workflow",
        "step_id": "step",
        "step_index": 1,
        "employee_id": "employee",
        "previous_status": "running",
        "next_status": "succeeded" if success_result else "failed",
        "provider": "openai",
        "failure_category": None if success_result else "api_error",
        "response_id": "response" if success_result else None,
        "request_id": "request",
        "output_text": "out" if success_result else None,
        "message": None if success_result else "safe",
    }
    values.update(changes)
    return RuntimeStepEvent(**values)  # type: ignore[arg-type]


def corrupting_reentry(
    state: Path,
    events: Path,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    final_state: WorkflowExecutionState | bytes,
    final_events: bytes | None = None,
) -> WorkflowExecutionPersistenceResult:
    state_bytes = (
        serialize_workflow_execution_state_json(final_state).encode()
        if isinstance(final_state, WorkflowExecutionState)
        else final_state
    )
    event_bytes = (
        serialize_runtime_step_event_jsonl(terminal_event(result)).encode()
        if final_events is None
        else final_events
    )
    state.write_bytes(state_bytes)
    events.write_bytes(event_bytes)
    return WorkflowExecutionPersistenceResult(
        state, events, len(state_bytes), len(event_bytes)
    )


def assert_final_contract_rejected(
    tmp_path: Path,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    state_value: WorkflowExecutionState | bytes,
    event_bytes: bytes | None = None,
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def fake(*_: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls
        calls += 1
        return corrupting_reentry(state, events, result, state_value, event_bytes)

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            result, workflow(), state, events, transition_reentry_function=fake
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


@pytest.mark.parametrize("result", [success(), failure()])
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "running"},
        {"status": "failed"},
        {"status": "succeeded"},
        {"workflow_id": "other"},
        {"current_step_id": "other"},
        {"current_step_index": 2},
        {"current_employee_id": "other"},
        {"completed_step_ids": ()},
        {"completed_step_ids": ("step", "step")},
        {"completed_step_ids": ("step", "other")},
        {"last_failure_category": "api_error"},
        {"last_failure_category": "transport_error"},
    ],
)
def test_final_state_contract_rejection_restores_both_targets(
    tmp_path: Path,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    changes: dict[str, object],
) -> None:
    effective = changes
    if type(result) is StepRuntimeExecutionSuccess and changes == {
        "status": "succeeded"
    }:
        effective = {"status": "failed"}
    elif type(result) is StepRuntimeExecutionFailure and changes == {
        "status": "failed"
    }:
        effective = {"status": "succeeded"}
    elif type(result) is StepRuntimeExecutionFailure and changes == {
        "completed_step_ids": ()
    }:
        effective = {"completed_step_ids": ("step",)}
    elif type(result) is StepRuntimeExecutionFailure and changes == {
        "last_failure_category": "api_error"
    }:
        effective = {"last_failure_category": "transport_error"}
    state = terminal_state(result, **effective)
    assert_final_contract_rejected(tmp_path, result, state)


@pytest.mark.parametrize("result", [success(), failure()])
@pytest.mark.parametrize(
    "changes",
    [
        {"event_type": "step_failed"},
        {"workflow_id": "other"},
        {"step_id": "other"},
        {"step_index": 2},
        {"employee_id": "other"},
        {"previous_status": "ready"},
        {"next_status": "running"},
        {"provider": "other"},
        {"request_id": "other"},
        {"response_id": "other"},
        {"output_text": "other"},
        {"failure_category": "transport_error"},
        {"message": "other"},
    ],
)
def test_final_event_contract_rejection_restores_both_targets(
    tmp_path: Path,
    result: StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure,
    changes: dict[str, object],
) -> None:
    # Skip mutations that equal the valid branch-specific field value.
    if type(result) is StepRuntimeExecutionSuccess and changes in (
        {"failure_category": "transport_error"},
        {"message": "other"},
    ):
        assert_final_contract_rejected(
            tmp_path,
            result,
            terminal_state(result),
            serialize_runtime_step_event_jsonl(
                terminal_event(result, **changes)
            ).encode(),
        )
    elif type(result) is StepRuntimeExecutionFailure and changes in (
        {"response_id": "other"},
        {"output_text": "other"},
    ):
        assert_final_contract_rejected(
            tmp_path,
            result,
            terminal_state(result),
            serialize_runtime_step_event_jsonl(
                terminal_event(result, **changes)
            ).encode(),
        )
    elif type(result) is StepRuntimeExecutionFailure and changes == {
        "event_type": "step_failed"
    }:
        assert_final_contract_rejected(
            tmp_path,
            result,
            terminal_state(result),
            serialize_runtime_step_event_jsonl(
                terminal_event(result, event_type="step_succeeded")
            ).encode(),
        )
    else:
        assert_final_contract_rejected(
            tmp_path,
            result,
            terminal_state(result),
            serialize_runtime_step_event_jsonl(
                terminal_event(result, **changes)
            ).encode(),
        )


@pytest.mark.parametrize(
    "mode",
    [
        "state-only",
        "event-only",
        "prefix-replaced",
        "multiple-events",
        "bad-state",
        "history-mismatch",
    ],
)
def test_missing_or_invalid_final_transition_is_compensated(
    tmp_path: Path, mode: str
) -> None:
    result = success()
    good_state = terminal_state(result)
    good_event = serialize_runtime_step_event_jsonl(terminal_event(result)).encode()
    if mode == "state-only":
        events = b""
        state = good_state
    elif mode == "event-only":
        events = good_event
        state = serialize_workflow_execution_state_json(
            WorkflowExecutionState(
                "workflow", "running", "step", 1, "employee", (), None
            )
        ).encode()
    elif mode == "prefix-replaced":
        events = b"replaced\n"
        state = good_state
    elif mode == "multiple-events":
        events = good_event + good_event
        state = good_state
    elif mode == "bad-state":
        events = good_event
        state = b"bad"
    else:
        events = good_event
        state = terminal_state(result, completed_step_ids=())
    assert_final_contract_rejected(tmp_path, result, state, events)


def test_replaced_nonempty_original_event_prefix_is_compensated(tmp_path: Path) -> None:
    state, events = tmp_path / "state.json", tmp_path / "events.jsonl"
    two_step_workflow = WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "first", "name": "F", "employee": "one", "instructions": "a"},
                {
                    "id": "step",
                    "name": "S",
                    "employee": "employee",
                    "instructions": "b",
                },
            ],
        }
    )
    running = WorkflowExecutionState(
        "workflow", "running", "step", 2, "employee", ("first",), None
    )
    prior = RuntimeStepEvent(
        "step_succeeded",
        "workflow",
        "first",
        1,
        "one",
        "running",
        "succeeded",
        "openai",
        None,
        "response",
        "request",
        "out",
        None,
    )
    before_state = serialize_workflow_execution_state_json(running).encode()
    before_events = serialize_runtime_step_event_jsonl(prior).encode()
    state.write_bytes(before_state)
    events.write_bytes(before_events)
    result = StepRuntimeExecutionSuccess(
        "workflow", "step", 2, "employee", success().invocation_result
    )
    calls = 0

    def fake(*_: object) -> WorkflowExecutionPersistenceResult:
        nonlocal calls
        calls += 1
        state_bytes = serialize_workflow_execution_state_json(
            WorkflowExecutionState(
                "workflow", "succeeded", "step", 2, "employee", ("first", "step"), None
            )
        ).encode()
        replacement = serialize_runtime_step_event_jsonl(
            RuntimeStepEvent(
                "step_succeeded",
                "workflow",
                "step",
                2,
                "employee",
                "running",
                "succeeded",
                "openai",
                None,
                "response",
                "request",
                "out",
                None,
            )
        ).encode()
        state.write_bytes(state_bytes)
        events.write_bytes(replacement)
        return WorkflowExecutionPersistenceResult(
            state, events, len(state_bytes), len(replacement)
        )

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            result, two_step_workflow, state, events, transition_reentry_function=fake
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


@pytest.mark.parametrize("mode", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both", "delete-and-other"])
def test_malformed_dependency_mutations_are_compensated_without_retry(
    tmp_path: Path, mode: str, target: str
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    calls = 0

    def invalid(*_: object) -> object:
        nonlocal calls
        calls += 1
        if target == "state":
            _mutate(state, mode)
        elif target == "events":
            _mutate(events, mode)
        elif target == "both":
            _mutate(state, mode)
            _mutate(events, mode)
        else:
            state.unlink()
            _mutate(events, mode)
        return object()

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            success(), workflow(), state, events, transition_reentry_function=invalid
        )
    assert caught.value.detail.classification == "persistence_contract"
    assert calls == 1
    assert state.read_bytes() == before_state and events.read_bytes() == before_events


@pytest.mark.parametrize("mode", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_safe_and_unexpected_dependency_errors_restore_mutations(
    tmp_path: Path, mode: str, target: str
) -> None:
    for safe in (True, False):
        state, events, before_state, before_events = setup(tmp_path)
        calls = 0
        expected = ExecutedResultTransitionReentryCompatibilityError("result_type")

        def failing(*_: object) -> object:
            nonlocal calls
            calls += 1
            if target in {"state", "both"}:
                _mutate(state, mode)
            if target in {"events", "both"}:
                _mutate(events, mode)
            if safe:
                raise expected
            raise RuntimeError("provider response output failure")

        if safe:
            with pytest.raises(
                ExecutedResultTransitionReentryCompatibilityError
            ) as caught:
                route_executed_result_transition_reentry(
                    success(),
                    workflow(),
                    state,
                    events,
                    transition_reentry_function=failing,
                )
            assert caught.value is expected
        else:
            with pytest.raises(
                ExecutedResultTransitionRoutingCompatibilityError
            ) as caught:
                route_executed_result_transition_reentry(
                    success(),
                    workflow(),
                    state,
                    events,
                    transition_reentry_function=failing,
                )
            assert caught.value.detail.classification == "dependency_error"
            assert "provider" not in str(caught.value)
            assert "output" not in str(caught.value)
        assert calls == 1
        assert (
            state.read_bytes() == before_state and events.read_bytes() == before_events
        )


@pytest.mark.parametrize("failed_target", ["state", "events"])
def test_rollback_failure_is_safe_and_attempts_both_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_target: str
) -> None:
    state, events, before_state, before_events = setup(tmp_path)
    original_write = Path.write_bytes
    restored: list[Path] = []

    def fail_one_restore(path: Path, data: bytes) -> int:
        if data == (before_state if path == state else before_events):
            restored.append(path)
            if path.name == f"{failed_target}.json" or (
                failed_target == "events" and path == events
            ):
                raise OSError("provider response output failure")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_one_restore)
    calls = 0

    def failing(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        raise RuntimeError("provider response output failure")

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            success(), workflow(), state, events, transition_reentry_function=failing
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert state in restored and events in restored
    assert str(state) not in str(caught.value)
    assert "provider" not in str(caught.value)
    assert "output" not in str(caught.value)
    assert "failure" not in str(caught.value)
