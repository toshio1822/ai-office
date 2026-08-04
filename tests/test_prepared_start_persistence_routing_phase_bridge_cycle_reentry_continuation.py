"""Focused Phase 76 boundary tests using injected Phase 76 fakes only."""

# ruff: noqa: E501

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_start_persistence_routing_phase_bridge_cycle_continuation import (
    PreparedStartPersistenceRoutingPhaseBridgeCycleContinuationError,
    route_prepared_start_persistence_routing_phase_bridge_cycle_continuation,
)
from ai_office.engine.prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation import (
    PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError,
    route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


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
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "two",
                    "instructions": "b",
                },
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


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "employee", "b", ("tool",)),
        WorkflowExecutionState(
            "workflow", "running", "second", 2, "two", ("first",), None
        ),
    )


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
    )


def targets(
    tmp_path: Path, status: str = "succeeded", index: int = 1
) -> tuple[Path, Path]:
    if index == 1:
        state = WorkflowExecutionState(
            "workflow",
            status,
            "first",
            1,
            "one",
            ("first",) if status == "succeeded" else (),
            None if status == "succeeded" else "api_error",
        )
        events = (event(status, 1),)
    else:
        state = WorkflowExecutionState(
            "workflow",
            status,
            "second",
            2,
            "two",
            ("first", "second") if status == "succeeded" else ("first",),
            None if status == "succeeded" else "api_error",
        )
        events = (event("succeeded", 1), event(status, 2))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events),
        encoding="utf-8",
    )
    return state_path, events_path


def completion() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete",
        "workflow",
        "second",
        2,
        "two",
        None,
        None,
        None,
        "last_step_succeeded",
    )


def failure() -> PersistedExecutionOutcome:
    return PersistedExecutionOutcome(
        "persisted_failure", "workflow", "first", 1, "one", "api_error"
    )


class StringSubclass(str):
    pass


class IntegerSubclass(int):
    pass


class StartSubclass(PreparedStepExecutionStart):
    pass


class RequestSubclass(ModelInvocationRequest):
    pass


class StateSubclass(WorkflowExecutionState):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


class ResultSubclass(RunningStatePersistenceResult):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class DecisionSubclass(WorkflowProgressionDecision):
    pass


class OutcomeSubclass(PersistedExecutionOutcome):
    pass


def write_valid_state(state: Path, value: PreparedStepExecutionStart) -> int:
    contents = serialize_workflow_execution_state_json(value.running_state).encode()
    state.write_bytes(contents)
    return len(contents)


def test_valid_start_delegates_once_with_identity_and_returns_exact_result(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person = start(), workflow(), employee()
    calls = 0
    expected = RunningStatePersistenceResult(
        len(serialize_workflow_execution_state_json(supplied.running_state).encode())
    )

    def phase76(*args: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        assert all(
            actual is expected
            for actual, expected in zip(
                args, (supplied, definition, person, state, events), strict=True
            )
        )
        write_valid_state(state, supplied)
        return expected

    result = route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
        supplied, definition, person, state, events, phase76_function=phase76
    )
    assert result is expected and calls == 1 and events.read_bytes()


def test_phase76_public_signature_uses_canonical_argument_order() -> None:
    parameters = tuple(
        inspect.signature(
            route_prepared_start_persistence_routing_phase_bridge_cycle_continuation
        ).parameters
    )
    assert parameters[:5] == (
        "result",
        "workflow",
        "employee",
        "state_path",
        "events_path",
    )


@pytest.mark.parametrize(
    "result, status, index", [(completion(), "succeeded", 2), (failure(), "failed", 1)]
)
def test_stop_routes_return_same_object_without_phase76(
    tmp_path: Path, result, status: str, index: int
) -> None:
    state, events = targets(tmp_path, status, index)
    before = state.read_bytes(), events.read_bytes()
    returned = route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
        result,
        workflow(),
        None,
        state,
        events,
        phase76_function=lambda *_: pytest.fail("must not call"),
    )
    assert returned is result and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("value", [object(), SimpleNamespace(request=object())])
def test_result_type_is_strict(tmp_path: Path, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            value, workflow(), employee(), state, events
        )
    assert error.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "bad_workflow, bad_employee",
    [
        (WorkflowSubclass.model_validate(workflow().model_dump()), employee()),
    ],
)
def test_workflow_and_stop_employee_contracts_are_strict(
    tmp_path: Path, bad_workflow, bad_employee
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), bad_workflow, bad_employee, state, events
        )
    assert error.value.detail.classification == "workflow_definition"

    stop_dir = tmp_path / "stop"
    stop_dir.mkdir()
    state, events = targets(stop_dir)
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            completion(), workflow(), employee(), state, events
        )
    assert error.value.detail.classification == "completion_contract"


@pytest.mark.parametrize(
    "value",
    [
        DecisionSubclass(*completion().__dict__.values()),
        OutcomeSubclass(*failure().__dict__.values()),
        SimpleNamespace(decision="prepare_next_step"),
        SimpleNamespace(outcome="persisted_success"),
    ],
)
def test_unsupported_progression_and_terminal_substitutes_are_rejected(
    tmp_path: Path, value: object
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            value, workflow(), None, state, events
        )
    assert error.value.detail.classification in {
        "result_type",
        "completion_contract",
        "failure_contract",
    }


def test_exact_persisted_success_is_rejected_without_phase76_call(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path, "succeeded", 1)
    result = PersistedExecutionOutcome(
        "persisted_success", "workflow", "first", 1, "one", None
    )
    calls = 0

    def phase76(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 76 must not be called")

    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            result, workflow(), None, state, events, phase76_function=phase76
        )
    assert error.value.detail.classification == "failure_contract"
    assert calls == 0


@pytest.mark.parametrize(
    "bad_employee",
    [
        None,
        EmployeeSubclass.model_validate(employee().model_dump()),
        employee().model_copy(update={"id": "wrong"}),
        employee().model_copy(update={"id": 2}),
        employee().model_copy(update={"id": StringSubclass("two")}),
    ],
)
def test_start_requires_exact_matching_employee(tmp_path: Path, bad_employee) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), bad_employee, state, events
        )
    assert error.value.detail.classification == "employee_contract"


@pytest.mark.parametrize(
    "field, value",
    [
        ("id", StringSubclass("two")),
        ("name", StringSubclass("Two")),
        ("role", StringSubclass("role")),
        ("instructions", StringSubclass("employee")),
        ("model", StringSubclass("model")),
        ("allowed_tools", [StringSubclass("tool")]),
        ("allowed_tools", ("tool",)),
    ],
)
def test_employee_all_fields_require_exact_builtin_types(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path)
    bad_employee = employee().model_copy(update={field: value})
    calls = 0

    def phase76(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 76 must not be called")

    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), bad_employee, state, events, phase76_function=phase76
        )
    assert error.value.detail.classification == "employee_contract"
    assert calls == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("model", "wrong"),
        ("system_instructions", "wrong"),
        ("task_instructions", "wrong"),
        ("allowed_tools", ("wrong",)),
        ("model", StringSubclass("model")),
        ("system_instructions", StringSubclass("employee")),
        ("task_instructions", StringSubclass("b")),
        ("allowed_tools", (StringSubclass("tool"),)),
    ],
)
def test_request_fields_are_exact_and_prevalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path)
    bad = replace(start(), request=replace(start().request, **{field: value}))
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            bad, workflow(), employee(), state, events
        )
    assert error.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "field, value",
    [
        ("workflow_id", "wrong"),
        ("current_step_id", "wrong"),
        ("current_employee_id", "wrong"),
        ("status", StringSubclass("running")),
        ("current_step_index", IntegerSubclass(2)),
        ("completed_step_ids", ["first"]),
        ("last_failure_category", "api_error"),
    ],
)
def test_running_state_fields_are_exact_and_prevalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path)
    bad = replace(
        start(), running_state=replace(start().running_state, **{field: value})
    )
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            bad, workflow(), employee(), state, events
        )
    expected = "employee_contract" if field == "current_employee_id" else "start_contract"
    assert error.value.detail.classification == expected


@pytest.mark.parametrize(
    "bad",
    [
        StartSubclass(start().request, start().running_state),
        PreparedStepExecutionStart(
            RequestSubclass("model", "employee", "b", ("tool",)), start().running_state
        ),
        PreparedStepExecutionStart(
            start().request,
            StateSubclass(
                start().running_state.workflow_id,
                start().running_state.status,
                start().running_state.current_step_id,
                start().running_state.current_step_index,
                start().running_state.current_employee_id,
                start().running_state.completed_step_ids,
                start().running_state.last_failure_category,
            ),
        ),
    ],
)
def test_start_models_must_be_exact(
    tmp_path: Path, bad: PreparedStepExecutionStart
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            bad, workflow(), employee(), state, events
        )
    assert error.value.detail.classification in {"result_type", "start_contract"}


@pytest.mark.parametrize("mutation", ["missing", "partial", "unrelated", "invalid"])
def test_invalid_persistence_effects_restore_both_targets(
    tmp_path: Path, mutation: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def phase76(*args: object):
        if mutation == "partial":
            state.write_bytes(b"partial")
        elif mutation == "unrelated":
            events.write_bytes(b"unrelated")
        elif mutation == "invalid":
            state.write_bytes(
                serialize_workflow_execution_state_json(
                    WorkflowExecutionState(
                        "workflow", "running", "wrong", 2, "two", ("first",), None
                    )
                ).encode()
            )
        return SimpleNamespace(state_bytes_written=1)

    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), employee(), state, events, phase76_function=phase76
        )
    assert (
        error.value.detail.classification == "persistence_contract"
        and (state.read_bytes(), events.read_bytes()) == before
    )


def test_valid_persistence_result_and_exact_state_bytes_are_accepted(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)

    def phase76(*args: object) -> RunningStatePersistenceResult:
        value = args[0]
        assert isinstance(value, PreparedStepExecutionStart)
        return RunningStatePersistenceResult(write_valid_state(state, value))

    returned = route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
        start(), workflow(), employee(), state, events, phase76_function=phase76
    )
    assert isinstance(returned, RunningStatePersistenceResult)


@pytest.mark.parametrize("count", [0, -1, True, len(serialize_workflow_execution_state_json(start().running_state).encode()) + 1])
def test_persistence_result_count_is_exact(tmp_path: Path, count: object) -> None:
    state, events = targets(tmp_path)

    def phase76(*args: object) -> RunningStatePersistenceResult:
        write_valid_state(state, args[0])
        return RunningStatePersistenceResult(count)

    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), employee(), state, events, phase76_function=phase76
        )
    assert error.value.detail.classification == "persistence_contract"


def test_dependency_can_mutate_both_targets_then_malformed_return_is_compensated(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def phase76(*args: object):
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return SimpleNamespace(state_bytes_written=1)

    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), employee(), state, events, phase76_function=phase76
        )
    assert error.value.detail.classification == "persistence_contract"
    assert (state.read_bytes(), events.read_bytes()) == before


def test_missing_and_nonregular_targets_are_rejected_separately(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    state.unlink()
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), employee(), state, events
        )
    assert error.value.detail.classification == "state_target"

    state.mkdir()
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), employee(), state, events
        )
    assert error.value.detail.classification == "state_target"


def test_malformed_return_type_and_result_subclass_are_rejected(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(),
            workflow(),
            employee(),
            state,
            events,
            phase76_function=lambda *_: ResultSubclass(1),
        )
    assert error.value.detail.classification == "persistence_contract"


def test_safe_error_identity_and_unexpected_error_are_safe(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = PreparedStartPersistenceRoutingPhaseBridgeCycleContinuationError("private")

    def safe_dependency(*args: object):
        state.write_bytes(b"private")
        raise safe

    with pytest.raises(PreparedStartPersistenceRoutingPhaseBridgeCycleContinuationError) as caught:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(),
            workflow(),
            employee(),
            state,
            events,
            phase76_function=safe_dependency,
        )
    assert caught.value is safe and (state.read_bytes(), events.read_bytes()) == before

    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as caught:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(),
            workflow(),
            employee(),
            state,
            events,
            phase76_function=lambda *_: (_ for _ in ()).throw(RuntimeError("private")),
        )
    assert (
        caught.value.detail.classification == "dependency_error"
        and "private" not in str(caught.value)
    )


@pytest.mark.parametrize("which", ["state", "events"])
def test_target_is_file_oserror_is_separate(
    tmp_path: Path, monkeypatch, which: str
) -> None:
    state, events = targets(tmp_path)
    target = state if which == "state" else events
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda self: (_ for _ in ()).throw(OSError()) if self == target else True,
    )
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), employee(), state, events
        )
    assert error.value.detail.classification == (
        "state_target" if which == "state" else "event_target"
    )


@pytest.mark.parametrize("which", ["state", "events"])
def test_target_read_bytes_oserror_is_separate(
    tmp_path: Path, monkeypatch, which: str
) -> None:
    state, events = targets(tmp_path)
    target = state if which == "state" else events
    original = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: (
            (_ for _ in ()).throw(OSError()) if self == target else original(self)
        ),
    )
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), employee(), state, events
        )
    assert error.value.detail.classification == (
        "state_target" if which == "state" else "event_target"
    )


@pytest.mark.parametrize("failing", ["state", "events", "both"])
def test_rollback_failures_attempt_both_and_do_not_retry(
    tmp_path: Path, monkeypatch, failing: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    original_write = Path.write_bytes
    calls = 0

    def phase76(*args: object):
        nonlocal calls
        calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        raise RuntimeError("private")

    def write_bytes(self: Path, data: bytes) -> int:
        if self == state and data == before[0] and failing in {"state", "both"}:
            raise OSError()
        if self == events and data == before[1] and failing in {"events", "both"}:
            raise OSError()
        return original_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    with pytest.raises(
        PreparedStartPersistenceRoutingPhaseBridgeCycleReentryContinuationError
    ) as error:
        route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation(
            start(), workflow(), employee(), state, events, phase76_function=phase76
        )
    assert error.value.detail.classification == "dependency_rollback" and calls == 1
