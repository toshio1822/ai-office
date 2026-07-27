"""Tests for Phase 43 with injected Phase 36 fakes only."""

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
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
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

    def phase36(*args: object):
        nonlocal calls, expected
        calls += 1
        assert args == (result, workflow(), state, events)
        expected = persist_executed_result_transition_reentry(*args)
        return expected

    assert (
        route_executed_result_transition_reentry(
            result, workflow(), state, events, transition_reentry_function=phase36
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

    def phase36(*_: object) -> object:
        nonlocal calls
        calls += 1
        valid_persistence(state, events, success())
        return returned

    with pytest.raises(ExecutedResultTransitionRoutingCompatibilityError) as caught:
        route_executed_result_transition_reentry(
            success(), workflow(), state, events, transition_reentry_function=phase36
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

    def phase36(*_: object) -> object:
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
            transition_reentry_function=phase36,
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
