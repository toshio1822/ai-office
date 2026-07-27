"""Tests for Phase 48 using injected Phase 41 fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistenceBridgeCompatibilityError,
    PreparedStepExecutionStart,
    WorkflowProgressionDecision,
    route_prepared_start_persistence_bridge_reentry,
)
from ai_office.engine.prepared_start_persistence_routing_reentry import (
    PreparedStartPersistenceRoutingError,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class StartSubclass(PreparedStepExecutionStart):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class ResultSubclass(RunningStatePersistenceResult):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "test",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "one",
                    "instructions": "a",
                },  # noqa: E501
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "b",
                },  # noqa: E501
            ],
        }
    )


def employee() -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": "two",
            "name": "Two",
            "role": "role",
            "instructions": "employee",
            "model": "model",
            "allowed_tools": ["tool"],
        }
    )


def start(**changes: object) -> PreparedStepExecutionStart:
    request = ModelInvocationRequest("model", "employee", "b", ("tool",))
    running = WorkflowExecutionState(
        "workflow", "running", "second", 2, "two", ("first",), None
    )
    values = {"request": request, "running_state": running}
    values.update(changes)
    return PreparedStepExecutionStart(**values)  # type: ignore[arg-type]


def completion(**changes: object) -> WorkflowProgressionDecision:
    values: dict[str, object] = {
        "decision": "workflow_complete",
        "workflow_id": "workflow",
        "current_step_id": "second",
        "current_step_index": 2,
        "current_employee_id": "two",
        "next_step_id": None,
        "next_step_index": None,
        "next_employee_id": None,
        "reason": "last_step_succeeded",
    }
    values.update(changes)
    return WorkflowProgressionDecision(**values)  # type: ignore[arg-type]


def failure(**changes: object) -> PersistedExecutionOutcome:
    values: dict[str, object] = {
        "outcome": "persisted_failure",
        "workflow_id": "workflow",
        "current_step_id": "second",
        "current_step_index": 2,
        "current_employee_id": "two",
        "failure_category": "api_error",
    }
    values.update(changes)
    return PersistedExecutionOutcome(**values)  # type: ignore[arg-type]


def event(status: str, index: int) -> RuntimeStepEvent:
    step, person = ("first", "one") if index == 1 else ("second", "two")
    return RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        "workflow",
        step,
        index,
        person,
        "running",
        status,
        "openai",
        None if status == "succeeded" else "api_error",
        "response" if status == "succeeded" else None,
        "request",
        "output" if status == "succeeded" else None,
        None if status == "succeeded" else "safe",
    )  # type: ignore[arg-type]


def targets(
    tmp_path: Path, status: str = "succeeded", index: int = 1
) -> tuple[Path, Path]:  # noqa: E501
    step, person = ("first", "one") if index == 1 else ("second", "two")
    completed = ("first",) if index == 1 or status == "failed" else ("first", "second")
    state = WorkflowExecutionState(
        "workflow",
        status,
        step,
        index,
        person,
        completed,
        None if status == "succeeded" else "api_error",
    )  # type: ignore[arg-type]
    events = [event("succeeded", 1)] if index == 2 else []
    events.append(event(status, index))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
    )  # noqa: E501
    return state_path, events_path


def assert_error(
    error: pytest.ExceptionInfo[PreparedStartPersistenceBridgeCompatibilityError],
    classification: str,
) -> None:
    assert error.value.detail.classification == classification
    assert (
        str(error.value) == "prepared-start persistence bridge inputs are incompatible"
    )  # noqa: E501


def test_start_delegates_exact_objects_persists_only_running_state_and_returns_identity(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person = start(), workflow(), employee()
    expected = RunningStatePersistenceResult(
        len(serialize_workflow_execution_state_json(supplied.running_state).encode())
    )
    calls = 0
    event_before = events.read_bytes()

    def phase41(*args: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        actual_start, actual_workflow, actual_employee, actual_state, actual_events = (
            args  # noqa: E501
        )
        assert actual_start is supplied and actual_workflow is definition
        assert (
            actual_employee is person
            and actual_state is state
            and actual_events is events
        )  # noqa: E501
        state.write_text(
            serialize_workflow_execution_state_json(supplied.running_state)
        )
        return expected

    assert (
        route_prepared_start_persistence_bridge_reentry(
            supplied,
            definition,
            person,
            state,
            events,
            persistence_routing_function=phase41,  # noqa: E501
        )
        is expected
    )
    assert calls == 1
    assert state.read_text() == serialize_workflow_execution_state_json(
        supplied.running_state
    )  # noqa: E501
    assert events.read_bytes() == event_before


@pytest.mark.parametrize("route", ["completion", "failure"])
def test_terminal_routes_validate_strict_history_and_return_same_object(
    tmp_path: Path, route: str
) -> None:
    state, events = targets(
        tmp_path, "succeeded" if route == "completion" else "failed", 2
    )  # noqa: E501
    supplied: object = completion() if route == "completion" else failure()
    before = state.read_bytes(), events.read_bytes()
    assert (
        route_prepared_start_persistence_bridge_reentry(
            supplied,
            workflow(),
            employee(),
            state,
            events,
            persistence_routing_function=lambda *_: pytest.fail(
                "Phase 41 must not run"
            ),
        )
        is supplied
    )
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    ("result", "classification"),
    [
        (object(), "result_type"),
        (StartSubclass(start().request, start().running_state), "result_type"),
        (DecisionSubclass(**completion().__dict__), "result_type"),
        (OutcomeSubclass(**failure().__dict__), "result_type"),
        (
            PersistedExecutionOutcome(
                "persisted_success", "workflow", "second", 2, "two", None
            ),
            "failure_contract",
        ),  # noqa: E501
    ],
)
def test_result_prevalidation_rejects_without_calls_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: object, classification: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    original = Path.write_bytes
    writes: list[Path] = []
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda path, data: (writes.append(path), original(path, data))[1],
    )  # noqa: E501
    with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_bridge_reentry(
            result,
            workflow(),
            employee(),
            state,
            events,
            persistence_routing_function=lambda *_: pytest.fail("called"),
        )
    assert_error(caught, classification)
    assert writes == [] and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    (
        "workflow_value",
        "employee_value",
        "state_value",
        "events_value",
        "function",
        "classification",
    ),  # noqa: E501
    [
        (object(), employee(), None, None, lambda: None, "workflow_definition"),
        (
            workflow(),
            EmployeeSubclass(**employee().__dict__),
            None,
            None,
            lambda: None,
            "employee_contract",
        ),  # noqa: E501
        (workflow(), employee(), object(), None, lambda: None, "state_target"),
        (workflow(), employee(), None, object(), lambda: None, "event_target"),
        (workflow(), employee(), None, None, object(), "persistence_contract"),
    ],
)
def test_top_level_contracts_reject_before_target_writes(
    tmp_path: Path,
    workflow_value: object,
    employee_value: object,
    state_value: object,
    events_value: object,
    function: object,
    classification: str,
) -> None:
    state, events = targets(tmp_path)
    supplied_state = state if state_value is None else state_value
    supplied_events = events if events_value is None else events_value
    with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_bridge_reentry(
            start(),
            workflow_value,
            employee_value,
            supplied_state,
            supplied_events,
            persistence_routing_function=function,  # type: ignore[arg-type]
        )
    assert_error(caught, classification)


def test_conflicting_or_missing_targets_reject_before_phase41(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    for actual_state, actual_events, classification in (
        (state, state, "target_conflict"),
        (tmp_path / "missing", events, "state_target"),
        (state, tmp_path / "missing", "event_target"),
    ):
        with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
            route_prepared_start_persistence_bridge_reentry(
                start(),
                workflow(),
                employee(),
                actual_state,
                actual_events,
                persistence_routing_function=lambda *_: pytest.fail("called"),
            )
        assert_error(caught, classification)


@pytest.mark.parametrize(
    "changed",
    [
        {"status": "ready"},
        {"workflow_id": "other"},
        {"current_step_id": "first"},
        {"current_step_index": True},
        {"current_step_index": 1},
        {"current_step_index": 3},  # noqa: E501
        {"current_employee_id": "one"},
        {"completed_step_ids": ()},
        {"last_failure_category": "api_error"},
    ],
)
def test_malformed_start_rejects_before_phase41(
    tmp_path: Path, changed: dict[str, object]
) -> None:  # noqa: E501
    state, events = targets(tmp_path)
    bad_state = replace(start().running_state, **changed)
    with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_bridge_reentry(
            start(running_state=bad_state),
            workflow(),
            employee(),
            state,
            events,
            persistence_routing_function=lambda *_: pytest.fail("called"),
        )
    assert_error(caught, "start_contract")


@pytest.mark.parametrize(
    "field", ["model", "system_instructions", "task_instructions", "allowed_tools"]
)  # noqa: E501
def test_malformed_request_rejects_before_phase41(tmp_path: Path, field: str) -> None:
    state, events = targets(tmp_path)
    request = replace(
        start().request, **{field: "wrong" if field != "allowed_tools" else ()}
    )  # noqa: E501
    with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_bridge_reentry(
            start(request=request),
            workflow(),
            employee(),
            state,
            events,
            persistence_routing_function=lambda *_: pytest.fail("called"),
        )
    assert_error(caught, "start_contract")


@pytest.mark.parametrize("route", ["start", "completion", "failure"])
@pytest.mark.parametrize("mode", ["invalid_state", "invalid_events", "wrong_prefix"])
def test_history_mismatch_rejects_before_phase41(
    tmp_path: Path, route: str, mode: str
) -> None:  # noqa: E501
    status, index = ("succeeded", 1) if route == "start" else ("succeeded", 2)
    if route == "failure":
        status = "failed"
    state, events = targets(tmp_path, status, index)
    if mode == "invalid_state":
        state.write_text("{")
    elif mode == "invalid_events":
        events.write_text("{")
    else:
        state.write_text(
            serialize_workflow_execution_state_json(
                replace(
                    WorkflowExecutionState(
                        "workflow",
                        status,
                        "first" if index == 1 else "second",
                        index,  # noqa: E501
                        "one" if index == 1 else "two",
                        ("first",) if index == 1 else ("first", "second"),  # noqa: E501
                        None if status == "succeeded" else "api_error",
                    ),
                    completed_step_ids=(),
                )
            )
        )
    value: object = (
        start()
        if route == "start"
        else completion()
        if route == "completion"
        else failure()
    )  # noqa: E501
    with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_bridge_reentry(
            value,
            workflow(),
            employee(),
            state,
            events,
            persistence_routing_function=lambda *_: pytest.fail("called"),
        )
    assert_error(caught, "terminal_contract")


def test_malformed_completion_and_failure_are_classified_before_missing_targets(
    tmp_path: Path,
) -> None:  # noqa: E501
    state, events = tmp_path / "missing-state", tmp_path / "missing-events"
    for value, classification in (
        (completion(reason="wrong"), "completion_contract"),
        (failure(failure_category=None), "failure_contract"),
    ):
        with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
            route_prepared_start_persistence_bridge_reentry(
                value,
                workflow(),
                employee(),
                state,
                events,
                persistence_routing_function=lambda *_: pytest.fail("called"),
            )
        assert_error(caught, classification)


@pytest.mark.parametrize(
    "result",
    [
        object(),
        ResultSubclass(1),
        RunningStatePersistenceResult(True),
        RunningStatePersistenceResult(0),
        RunningStatePersistenceResult(-1),
    ],  # noqa: E501
)
def test_malformed_phase41_result_restores_originals(
    tmp_path: Path, result: object
) -> None:  # noqa: E501
    state, events = targets(tmp_path)
    supplied = start()
    before = state.read_bytes(), events.read_bytes()

    def phase41(*_: object) -> object:
        state.write_text(
            serialize_workflow_execution_state_json(supplied.running_state)
        )
        return result

    with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_bridge_reentry(
            supplied,
            workflow(),
            employee(),
            state,
            events,
            persistence_routing_function=phase41,  # noqa: E501
        )
    assert_error(caught, "persistence_contract")
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("mutation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events"])
def test_phase41_invalid_target_effects_restore_originals(
    tmp_path: Path, mutation: str, target: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def phase41(*_: object) -> RunningStatePersistenceResult:
        path = state if target == "state" else events
        if mutation == "replace":
            path.write_bytes(b"changed")
        elif mutation == "delete":
            path.unlink()
        elif mutation == "truncate":
            path.write_bytes(b"")
        else:
            path.write_bytes(path.read_bytes() + b"changed")
        return RunningStatePersistenceResult(1)

    with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_bridge_reentry(
            start(),
            workflow(),
            employee(),
            state,
            events,
            persistence_routing_function=phase41,  # noqa: E501
        )
    assert_error(caught, "persistence_contract")
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("kind", ["safe", "unexpected"])
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "state_replace",
        "state_delete",
        "state_truncate",
        "state_append",
        "events_replace",
        "events_delete",
        "events_truncate",
        "events_append",
        "both",
    ],
)
def test_dependency_errors_preserve_safe_identity_and_restore_targets(
    tmp_path: Path, kind: str, mutation: str | None
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = PreparedStartPersistenceRoutingError("internal secret")

    def phase41(*_: object) -> RunningStatePersistenceResult:
        if mutation == "state_replace":
            state.write_bytes(b"state secret")
        elif mutation == "state_delete":
            state.unlink()
        elif mutation == "state_truncate":
            state.write_bytes(b"")
        elif mutation == "state_append":
            state.write_bytes(state.read_bytes() + b"secret")
        elif mutation == "events_replace":
            events.write_bytes(b"events secret")
        elif mutation == "events_delete":
            events.unlink()
        elif mutation == "events_truncate":
            events.write_bytes(b"")
        elif mutation == "events_append":
            events.write_bytes(events.read_bytes() + b"secret")
        elif mutation == "both":
            state.write_bytes(b"state secret")
            events.unlink()
        if kind == "safe":
            raise safe
        raise RuntimeError("secret path request response output failure")

    if kind == "safe":
        with pytest.raises(PreparedStartPersistenceRoutingError) as caught:
            route_prepared_start_persistence_bridge_reentry(
                start(),
                workflow(),
                employee(),
                state,
                events,
                persistence_routing_function=phase41,  # noqa: E501
            )
        assert caught.value is safe
    else:
        with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
            route_prepared_start_persistence_bridge_reentry(
                start(),
                workflow(),
                employee(),
                state,
                events,
                persistence_routing_function=phase41,  # noqa: E501
            )
        assert_error(caught, "dependency_error")
        assert "secret" not in str(caught.value)
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_overrides_dependency_error_and_attempts_both_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_target: str
) -> None:
    state, events = targets(tmp_path)
    original_write = Path.write_bytes
    attempts: list[Path] = []

    def phase41(*_: object) -> RunningStatePersistenceResult:
        original_write(state, b"changed")
        original_write(events, b"changed")
        raise RuntimeError("private failure")

    def fail_restore(path: Path, contents: bytes) -> int:
        attempts.append(path)
        if failed_target in {
            path.name.removesuffix(".json").removesuffix(".jsonl"),
            "both",
        }:  # noqa: E501
            raise OSError("private path")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", fail_restore)
    with pytest.raises(PreparedStartPersistenceBridgeCompatibilityError) as caught:
        route_prepared_start_persistence_bridge_reentry(
            start(),
            workflow(),
            employee(),
            state,
            events,
            persistence_routing_function=phase41,  # noqa: E501
        )
    assert_error(caught, "dependency_rollback")
    assert state in attempts and events in attempts
    assert "private" not in str(caught.value)
