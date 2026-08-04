"""Focused Phase 68 boundary tests using injected Phase 61 fakes only."""

# ruff: noqa: E501

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.next_step_preparation import PreparedWorkflowStep
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_next_step_start_routing_phase_bridge_continuation import (
    PreparedNextStepStartRoutingPhaseBridgeContinuationError,
    route_prepared_next_step_start_routing_phase_bridge_continuation,
)
from ai_office.engine.prepared_next_step_start_routing_phase_bridge_reentry import (
    PreparedNextStepStartRoutingPhaseBridgeError,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
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


def prepared() -> PreparedWorkflowStep:
    return PreparedWorkflowStep(
        "workflow", "second", 2, "two", "employee", "b", "model", ("tool",)
    )


def started() -> PreparedStepExecutionStart:
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
    definition = workflow()
    step = definition.steps[index - 1]
    completed = (
        tuple(item.id for item in definition.steps[:index])
        if status == "succeeded"
        else tuple(item.id for item in definition.steps[: index - 1])
    )
    state = WorkflowExecutionState(
        "workflow",
        status,
        step.id,
        index,
        step.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    events = [event("succeeded", 1)] if index == 2 else []
    events.append(event(status, index))
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


class PreparedSubclass(PreparedWorkflowStep):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


def test_prepared_route_delegates_once_with_exact_identity_and_returns_start(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person, expected = (
        prepared(),
        workflow(),
        employee(),
        started(),
    )
    calls = 0

    def phase61(*args: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        assert args == (supplied, definition, person, state, events)
        assert all(
            actual is expected_arg
            for actual, expected_arg in zip(
                args, (supplied, definition, person, state, events), strict=True
            )
        )
        return expected

    before = state.read_bytes(), events.read_bytes()
    result = route_prepared_next_step_start_routing_phase_bridge_continuation(
        supplied, definition, person, state, events, phase61_function=phase61
    )
    assert result is expected and calls == 1
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    "result, status, index", [(completion(), "succeeded", 2), (failure(), "failed", 1)]
)
def test_stop_routes_return_same_object_without_phase61(
    tmp_path: Path, result, status: str, index: int
) -> None:
    state, events = targets(tmp_path, status, index)
    before = state.read_bytes(), events.read_bytes()

    def phase61(*args: object) -> object:
        raise AssertionError("Phase 61 must not run")

    returned = route_prepared_next_step_start_routing_phase_bridge_continuation(
        result, workflow(), None, state, events, phase61_function=phase61
    )
    assert returned is result and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("value", [object(), SimpleNamespace(step_id="second")])
def test_result_type_is_strict(tmp_path: Path, value: object) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            value, workflow(), None, state, events
        )
    assert error.value.detail.classification == "result_type"


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "step_id",
        "step_index",
        "employee_id",
        "employee_instructions",
        "step_instructions",
        "model",
        "allowed_tool_names",
    ],
)
def test_every_prepared_field_is_strict_and_prevalidated(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    values = {
        "workflow_id": "wrong",
        "step_id": "wrong",
        "step_index": True,
        "employee_id": "wrong",
        "employee_instructions": "wrong",
        "step_instructions": "wrong",
        "model": "wrong",
        "allowed_tool_names": ["tool"],
    }
    calls = 0

    def phase61(*args: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    bad = replace(prepared(), **{field: values[field]})
    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            bad, workflow(), employee(), state, events, phase61_function=phase61
        )
    expected = "employee_contract" if field == "employee_id" else "prepared_step_contract"
    assert error.value.detail.classification == expected and calls == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("workflow_id", StringSubclass("workflow")),
        ("step_id", StringSubclass("second")),
        ("step_index", IntegerSubclass(2)),
        ("employee_id", StringSubclass("two")),
        ("employee_instructions", StringSubclass("employee")),
        ("step_instructions", StringSubclass("b")),
        ("model", StringSubclass("model")),
        ("allowed_tool_names", (StringSubclass("tool"),)),
    ],
)
def test_prepared_fields_require_builtin_types(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            replace(prepared(), **{field: value}), workflow(), employee(), state, events
        )
    assert error.value.detail.classification == "prepared_step_contract"


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
def test_prepared_route_requires_exact_matching_employee(
    tmp_path: Path, bad_employee
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            prepared(), workflow(), bad_employee, state, events
        )
    assert error.value.detail.classification == "employee_contract"


@pytest.mark.parametrize(
    "field, value",
    [
        ("model", "wrong"),
        ("system_instructions", "wrong"),
        ("task_instructions", "wrong"),
        ("allowed_tools", ("wrong",)),
    ],
)
def test_returned_request_fields_are_strict(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path)
    bad = replace(started(), request=replace(started().request, **{field: value}))

    def phase61(*args: object) -> PreparedStepExecutionStart:
        return bad

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            prepared(), workflow(), employee(), state, events, phase61_function=phase61
        )
    assert error.value.detail.classification == "start_contract"


@pytest.mark.parametrize(
    "field, value",
    [
        ("workflow_id", "wrong"),
        ("status", "succeeded"),
        ("current_step_id", "wrong"),
        ("current_step_index", True),
        ("current_employee_id", "wrong"),
        ("completed_step_ids", ["first"]),
        ("last_failure_category", "api_error"),
    ],
)
def test_returned_running_state_fields_are_strict(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events = targets(tmp_path)
    bad = replace(
        started(), running_state=replace(started().running_state, **{field: value})
    )

    def phase61(*args: object) -> PreparedStepExecutionStart:
        return bad

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            prepared(), workflow(), employee(), state, events, phase61_function=phase61
        )
    assert error.value.detail.classification == "start_contract"


def test_malformed_return_with_both_mutations_is_restored_without_retry(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    calls = 0

    def phase61(*args: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return object()

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            prepared(), workflow(), employee(), state, events, phase61_function=phase61
        )
    assert error.value.detail.classification == "start_contract"
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


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
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            prepared(), workflow(), employee(), state, events
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
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            prepared(), workflow(), employee(), state, events
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
    calls = 0
    original_write = Path.write_bytes

    def phase61(*args: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(b"changed-state")
        events.write_bytes(b"changed-events")
        return object()

    def write_bytes(self: Path, data: bytes) -> int:
        if self == state and data == before[0] and failing in {"state", "both"}:
            raise OSError()
        if self == events and data == before[1] and failing in {"events", "both"}:
            raise OSError()
        return original_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            prepared(), workflow(), employee(), state, events, phase61_function=phase61
        )
    assert error.value.detail.classification == "dependency_rollback" and calls == 1
    assert state.read_bytes() == (
        b"changed-state" if failing in {"state", "both"} else before[0]
    )
    assert events.read_bytes() == (
        b"changed-events" if failing in {"events", "both"} else before[1]
    )


def test_safe_error_identity_is_preserved_after_compensation(tmp_path: Path) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = PreparedNextStepStartRoutingPhaseBridgeError("safe")

    def phase61(*args: object):
        state.write_bytes(b"changed")
        raise safe

    with pytest.raises(PreparedNextStepStartRoutingPhaseBridgeError) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            prepared(), workflow(), employee(), state, events, phase61_function=phase61
        )
    assert error.value is safe and (state.read_bytes(), events.read_bytes()) == before


def test_unexpected_error_is_sanitized(tmp_path: Path) -> None:
    state, events = targets(tmp_path)

    def phase61(*args: object):
        raise RuntimeError("secret")

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeContinuationError
    ) as error:
        route_prepared_next_step_start_routing_phase_bridge_continuation(
            prepared(), workflow(), employee(), state, events, phase61_function=phase61
        )
    assert error.value.detail.classification == "dependency_error"
