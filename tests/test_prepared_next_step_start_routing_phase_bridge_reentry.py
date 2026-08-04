"""Focused Phase 61 boundary tests using injected Phase 54 fakes only."""

from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedNextStepStartRoutingPhaseBridgeCompatibilityError,
    PreparedStepExecutionStart,
    PreparedStepStartPhaseBridgeCompatibilityError,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_next_step_start_routing_phase_bridge_reentry,
)
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


def start() -> PreparedStepExecutionStart:
    return PreparedStepExecutionStart(
        ModelInvocationRequest("model", "employee", "b", ("tool",)),
        WorkflowExecutionState(
            "workflow", "running", "second", 2, "two", ("first",), None
        ),
    )


class StringSubclass(str):
    pass


class PreparedSubclass(PreparedWorkflowStep):
    pass


class WorkflowSubclass(WorkflowDefinition):
    pass


class EmployeeSubclass(EmployeeDefinition):
    pass


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
    tmp_path: Path, *, status: str = "succeeded", index: int = 1
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
    )  # type: ignore[arg-type]
    events = [event("succeeded", 1)] if index == 2 else []
    events.append(event(status, index))
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_text(serialize_workflow_execution_state_json(state))
    events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(item) for item in events)
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


def test_prepared_route_delegates_once_with_exact_identity_and_returns_start(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    supplied, definition, person, expected = prepared(), workflow(), employee(), start()
    calls = 0

    def phase54(*args: object) -> PreparedStepExecutionStart:
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
    assert (
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            supplied, definition, person, state, events, phase54_function=phase54
        )
        is expected
    )
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize(
    ("result", "status", "index"),
    [(completion(), "succeeded", 2), (failure(), "failed", 1)],
)
def test_stop_routes_return_supplied_object_without_phase54(
    tmp_path: Path,
    result: WorkflowProgressionDecision | PersistedExecutionOutcome,
    status: str,
    index: int,
) -> None:
    state, events = targets(tmp_path, status=status, index=index)
    before = state.read_bytes(), events.read_bytes()

    def phase54(*_: object) -> object:
        raise AssertionError("Phase 54 must not run")

    assert (
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            result, workflow(), None, state, events, phase54_function=phase54
        )
        is result
    )
    assert (state.read_bytes(), events.read_bytes()) == before


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
def test_prepared_input_fields_are_strict_and_prevalidated(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    values = {
        "workflow_id": "other",
        "step_id": "other",
        "step_index": True,
        "employee_id": "one",
        "employee_instructions": "other",
        "step_instructions": "other",
        "model": "other",
        "allowed_tool_names": ["tool"],
    }
    calls = 0

    def phase54(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            replace(prepared(), **{field: values[field]}),
            workflow(),
            employee(),
            state,
            events,
            phase54_function=phase54,
        )
    assert caught.value.detail.classification == "prepared_step_contract"
    assert calls == 0


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "step_id",
        "employee_id",
        "employee_instructions",
        "step_instructions",
        "model",
    ],
)
def test_prepared_input_string_subclasses_are_rejected(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    value = replace(prepared(), **{field: StringSubclass(getattr(prepared(), field))})
    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            value, workflow(), employee(), state, events
        )
    assert caught.value.detail.classification == "prepared_step_contract"


@pytest.mark.parametrize(
    ("result", "definition", "person", "classification"),
    [
        (
            PreparedSubclass(*prepared().__dict__.values()),
            workflow(),
            employee(),
            "result_type",
        ),
        (
            prepared(),
            WorkflowSubclass.model_validate(workflow().model_dump()),
            employee(),
            "workflow_definition",
        ),
        (
            prepared(),
            workflow(),
            EmployeeSubclass.model_validate(employee().model_dump()),
            "employee_contract",
        ),
    ],
)
def test_prepared_route_requires_exact_model_types(
    tmp_path: Path,
    result: object,
    definition: object,
    person: object,
    classification: str,
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase54(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            result, definition, person, state, events, phase54_function=phase54
        )
    assert caught.value.detail.classification == classification
    assert calls == 0


def test_prepared_input_tool_names_require_exact_tuple_and_string_types(
    tmp_path: Path,
) -> None:
    state, events = targets(tmp_path)
    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            replace(prepared(), allowed_tool_names=(StringSubclass("tool"),)),
            workflow(),
            employee(),
            state,
            events,
        )
    assert caught.value.detail.classification == "prepared_step_contract"


@pytest.mark.parametrize("field", ["model", "system_instructions", "task_instructions"])
def test_return_request_string_fields_are_strict_without_retry(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase54(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        request = replace(
            start().request,
            **{field: StringSubclass(getattr(start().request, field))},
        )
        return PreparedStepExecutionStart(request, start().running_state)

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            prepared(), workflow(), employee(), state, events, phase54_function=phase54
        )
    assert caught.value.detail.classification == "start_contract"
    assert calls == 1


@pytest.mark.parametrize("value", [True, False, 2.0, "2"])
def test_return_running_step_index_requires_exact_int_without_retry(
    tmp_path: Path, value: object
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase54(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return PreparedStepExecutionStart(
            start().request,
            replace(start().running_state, current_step_index=value),  # type: ignore[arg-type]
        )

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            prepared(), workflow(), employee(), state, events, phase54_function=phase54
        )
    assert caught.value.detail.classification == "start_contract"
    assert calls == 1


@pytest.mark.parametrize("value", [["first"], (StringSubclass("first"),)])
def test_return_tuple_fields_require_exact_tuple_and_string_types(
    tmp_path: Path, value: object
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase54(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return PreparedStepExecutionStart(
            replace(start().request, allowed_tools=value),  # type: ignore[arg-type]
            replace(start().running_state, completed_step_ids=value),  # type: ignore[arg-type]
        )

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            prepared(), workflow(), employee(), state, events, phase54_function=phase54
        )
    assert caught.value.detail.classification == "start_contract"
    assert calls == 1


@pytest.mark.parametrize("field", ["request", "running_state"])
def test_return_requires_exact_request_and_running_state_models_without_retry(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    calls = 0

    def phase54(*_: object) -> object:
        nonlocal calls
        calls += 1
        return replace(start(), **{field: object()})

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            prepared(),
            workflow(),
            employee(),
            state,
            events,
            phase54_function=phase54,
        )
    assert caught.value.detail.classification == "start_contract"
    assert calls == 1


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "status",
        "current_step_id",
        "current_employee_id",
        "completed_step_ids",
        "last_failure_category",
    ],
)
def test_return_running_state_fields_are_strict_without_retry(
    tmp_path: Path, field: str
) -> None:
    state, events = targets(tmp_path)
    values = {
        "workflow_id": "other",
        "status": "succeeded",
        "current_step_id": "first",
        "current_employee_id": "one",
        "completed_step_ids": ("other",),
        "last_failure_category": "api_error",
    }
    calls = 0

    def phase54(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return PreparedStepExecutionStart(
            start().request,
            replace(start().running_state, **{field: values[field]}),  # type: ignore[arg-type]
        )

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            prepared(), workflow(), employee(), state, events, phase54_function=phase54
        )
    assert caught.value.detail.classification == "start_contract"
    assert calls == 1


@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
def test_dependency_errors_and_malformed_returns_are_safe_without_retry(
    tmp_path: Path, kind: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()
    safe = PreparedStepStartPhaseBridgeCompatibilityError("safe detail")
    calls = 0

    def phase54(*_: object) -> object:
        nonlocal calls
        calls += 1
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("secret dependency detail")
        return object()

    expected = (
        PreparedStepStartPhaseBridgeCompatibilityError
        if kind == "safe"
        else PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    )
    with pytest.raises(expected) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            prepared(), workflow(), employee(), state, events, phase54_function=phase54
        )
    if kind == "safe":
        assert caught.value is safe
    else:
        assert "secret" not in str(caught.value)
    assert calls == 1 and (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("operation", ["replace", "delete", "truncate", "append"])
@pytest.mark.parametrize("target", ["state", "events", "both"])
def test_dependency_mutations_are_compensated(
    tmp_path: Path, operation: str, target: str
) -> None:
    state, events = targets(tmp_path)
    before = state.read_bytes(), events.read_bytes()

    def mutate(path: Path) -> None:
        if operation == "delete":
            path.unlink()
        elif operation == "truncate":
            path.write_bytes(b"")
        elif operation == "append":
            path.write_bytes(path.read_bytes() + b"secret")
        else:
            path.write_bytes(b"secret")

    def phase54(*_: object) -> PreparedStepExecutionStart:
        if target in {"state", "both"}:
            mutate(state)
        if target in {"events", "both"}:
            mutate(events)
        return start()

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            prepared(), workflow(), employee(), state, events, phase54_function=phase54
        )
    assert caught.value.detail.classification == "start_contract"
    assert "secret" not in str(caught.value)
    assert (state.read_bytes(), events.read_bytes()) == before


@pytest.mark.parametrize("failed", ["state", "events", "both"])
@pytest.mark.parametrize("kind", ["safe", "unexpected", "malformed"])
def test_rollback_failures_attempt_both_targets_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str, kind: str
) -> None:
    state, events = targets(tmp_path)
    originals = {state: state.read_bytes(), events: events.read_bytes()}
    original_write = Path.write_bytes
    attempts: list[Path] = []
    calls = 0
    safe = PreparedStepStartPhaseBridgeCompatibilityError("safe detail")

    def fail_restore(path: Path, contents: bytes) -> int:
        if contents == originals[path]:
            attempts.append(path)
            if (
                failed == "both"
                or (failed == "state" and path == state)
                or (failed == "events" and path == events)
            ):
                raise OSError("secret dependency detail")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", fail_restore)

    def phase54(*_: object) -> object:
        nonlocal calls
        calls += 1
        original_write(state, b"changed")
        original_write(events, b"changed")
        if kind == "safe":
            raise safe
        if kind == "unexpected":
            raise RuntimeError("secret dependency detail")
        return object()

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            prepared(), workflow(), employee(), state, events, phase54_function=phase54
        )
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert attempts[:2] == [state, events]
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_non_regular_targets_are_rejected_before_phase54(
    tmp_path: Path, target: str, kind: str
) -> None:
    state, events = targets(tmp_path)
    selected = state if target == "state" else events
    selected.unlink()
    if kind == "directory":
        selected.mkdir()
    calls = 0

    def phase54(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        PreparedNextStepStartRoutingPhaseBridgeCompatibilityError
    ) as caught:
        route_prepared_next_step_start_routing_phase_bridge_reentry(
            prepared(), workflow(), employee(), state, events, phase54_function=phase54
        )
    assert caught.value.detail.classification == (
        "state_target" if target == "state" else "event_target"
    )
    assert calls == 0
