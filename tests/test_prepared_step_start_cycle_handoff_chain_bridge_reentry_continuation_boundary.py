"""Focused fake-only tests for the Phase 131 prepared-step start bridge."""

# ruff: noqa: E501,E701,E702

import inspect
import json
from dataclasses import dataclass, replace
from pathlib import Path
from pathlib import Path as _TestPath
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedStepStartCycleHandoffChainBridgeReentryContinuationCompatibilityError,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainReentryContinuationError as Phase124Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary import (
    route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary as phase124_public,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class PreparedChild(PreparedWorkflowStep):
    pass


class DecisionChild(WorkflowProgressionDecision):
    pass


class OutcomeChild(PersistedExecutionOutcome):
    pass


class WorkflowChild(WorkflowDefinition):
    pass


class StepChild(WorkflowStepDefinition):
    pass


class EmployeeChild(EmployeeDefinition):
    pass


class StartChild(PreparedStepExecutionStart):
    pass


@dataclass(frozen=True)
class RequestChild(ModelInvocationRequest):
    pass


@dataclass(frozen=True)
class StateChild(WorkflowExecutionState):
    pass


class IntChild(int):
    pass


class PathChild(type(Path())):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "test workflow",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "one instructions"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "two instructions"},
                {"id": "three", "name": "Three", "employee": "c", "instructions": "three instructions"},
                {"id": "four", "name": "Four", "employee": "d", "instructions": "four instructions"},
                {"id": "five", "name": "Five", "employee": "e", "instructions": "five instructions"},
            ],
        }
    )


def employee(index: int = 4) -> EmployeeDefinition:
    step = workflow().steps[index - 1]
    return EmployeeDefinition.model_validate(
        {
            "id": step.employee,
            "name": step.name,
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def prepared(index: int = 4, supplied_workflow: WorkflowDefinition | None = None) -> PreparedWorkflowStep:
    definition = workflow() if supplied_workflow is None else supplied_workflow
    step = definition.steps[index - 1]
    person = employee(index)
    return PreparedWorkflowStep(
        definition.id,
        step.id,
        index,
        person.id,
        person.instructions,
        step.instructions,
        person.model,
        tuple(person.allowed_tools),
    )


def start_for(
    value: PreparedWorkflowStep,
    supplied_workflow: WorkflowDefinition | None = None,
) -> PreparedStepExecutionStart:
    definition = workflow() if supplied_workflow is None else supplied_workflow
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            value.model,
            value.employee_instructions,
            value.step_instructions,
            value.allowed_tool_names,
        ),
        WorkflowExecutionState(
            value.workflow_id,
            "running",
            value.step_id,
            value.step_index,
            value.employee_id,
            tuple(step.id for step in definition.steps[: value.step_index - 1]),
            None,
        ),
    )


def predecessor_event(
    supplied_workflow: WorkflowDefinition, index: int
) -> RuntimeStepEvent:
    step = supplied_workflow.steps[index - 1]
    return RuntimeStepEvent(
        "step_succeeded",
        supplied_workflow.id,
        step.id,
        index,
        step.employee,
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{step.id}",
        f"request-{step.id}",
        f"output-{step.id}",
        None,
    )


def terminal_event(
    supplied_workflow: WorkflowDefinition,
    index: int,
    status: str,
    provider: object = "openai",
    **changes: object,
) -> RuntimeStepEvent:
    step = supplied_workflow.steps[index - 1]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        supplied_workflow.id,
        step.id,
        index,
        step.employee,
        "running",
        status,
        provider,
        None if status == "succeeded" else "api_error",
        f"response-{step.id}" if status == "succeeded" else None,
        f"request-{step.id}",
        f"output-{step.id}" if status == "succeeded" else None,
        None if status == "succeeded" else "safe failure",
    )
    return replace(event, **changes)


def targets(
    tmp_path: Path,
    *,
    status: str = "succeeded",
    index: int = 3,
    provider: object = "openai",
) -> tuple[Path, Path, bytes, bytes]:
    definition = workflow()
    current = definition.steps[index - 1]
    completed = (
        tuple(step.id for step in definition.steps[:index])
        if status == "succeeded"
        else tuple(step.id for step in definition.steps[: index - 1])
    )
    state = WorkflowExecutionState(
        definition.id,
        status,
        current.id,
        index,
        current.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    events = [
        predecessor_event(definition, position)
        for position in range(1, index)
    ] + [terminal_event(definition, index, status, provider)]
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def completion(supplied_workflow: WorkflowDefinition) -> WorkflowProgressionDecision:
    final = supplied_workflow.steps[-1]
    return WorkflowProgressionDecision(
        "workflow_complete",
        supplied_workflow.id,
        final.id,
        len(supplied_workflow.steps),
        final.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def failure(
    supplied_workflow: WorkflowDefinition, index: int = 3
) -> PersistedExecutionOutcome:
    step = supplied_workflow.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure",
        supplied_workflow.id,
        step.id,
        index,
        step.employee,
        "api_error",
    )


def invoke(
    result: object,
    supplied_workflow: object,
    supplied_employee: object,
    state: object,
    events: object,
    dependency: object,
) -> object:
    return route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary(
        result,
        supplied_workflow,
        supplied_employee,
        state,
        events,
        phase124_function=dependency,
    )


def reject(callable_object, expected: str) -> None:
    with pytest.raises(
        PreparedStepStartCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        callable_object()
    assert caught.value.detail.classification == expected


def assert_rejected(
    result: object,
    supplied_workflow: object,
    supplied_employee: object,
    state: object,
    events: object,
    expected: str,
    dependency: object,
) -> None:
    reject(
        lambda: invoke(
            result,
            supplied_workflow,
            supplied_employee,
            state,
            events,
            dependency,
        ),
        expected,
    )


def rewrite_event(events: Path, index: int, **changes: object) -> None:
    lines = events.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[index])
    payload.update(changes)
    lines[index] = json.dumps(payload, separators=(",", ":"))
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_public_signature_default_and_source_audit() -> None:
    function = route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary
    parameters = tuple(inspect.signature(function).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "workflow",
        "employee",
        "state_path",
        "events_path",
        "phase124_function",
    )
    assert all(parameter.annotation is object for parameter in parameters[:5])
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in parameters[:5]
    )
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[5].default is phase124_public
    source = Path(
        "src/ai_office/engine/"
        "prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert (
        "route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary"
        in source
    )
    assert "phase117" not in source.lower()
    assert "route_prepared_step_start_cycle_handoff_reentry_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


@pytest.mark.parametrize("index", [4, 5], ids=["continuation-four", "continuation-final"])
def test_prepared_route_delegates_once_in_canonical_order_and_returns_identity(
    tmp_path: Path, index: int
) -> None:
    supplied_workflow = workflow()
    value = prepared(index, supplied_workflow)
    person = employee(index)
    state, events, before_state, before_events = targets(
        tmp_path, index=index - 1
    )
    calls: list[tuple[object, ...]] = []
    returned = start_for(value, supplied_workflow)

    def fake(*args: object) -> PreparedStepExecutionStart:
        calls.append(args)
        return returned

    actual = invoke(value, supplied_workflow, person, state, events, fake)
    assert actual is returned
    assert calls == [(value, supplied_workflow, person, state, events)]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("index", [1, 2, 3])
def test_step_index_one_rejects_and_indices_two_three_delegate_once(
    tmp_path: Path, index: int
) -> None:
    value = prepared(index)
    target_index = index - 1 if index >= 2 else 3
    supplied_workflow = workflow()
    supplied_employee = employee(index)
    state, events, before_state, before_events = targets(
        tmp_path, index=target_index
    )
    returned = start_for(value, supplied_workflow)
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PreparedStepExecutionStart:
        calls.append(args)
        return returned

    if index == 1:
        assert_rejected(
            value,
            supplied_workflow,
            supplied_employee,
            state,
            events,
            "prepared_step_contract",
            fake,
        )
        assert calls == []
    else:
        assert invoke(
            value,
            supplied_workflow,
            supplied_employee,
            state,
            events,
            fake,
        ) is returned
        assert calls == [
            (value, supplied_workflow, supplied_employee, state, events)
        ]
        assert returned.running_state.current_step_index == index
        assert returned.running_state.completed_step_ids == tuple(
            step.id for step in supplied_workflow.steps[: index - 1]
        )
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_source_default_dependency_is_the_public_phase124_route() -> None:
    assert phase124_public is route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary.__kwdefaults__["phase124_function"]


@pytest.mark.parametrize("route", ["completion", "failure"])
def test_stop_routes_are_identity_preserving_zero_call_stops_without_stricter_provider(
    tmp_path: Path, route: str
) -> None:
    supplied_workflow = workflow()
    if route == "completion":
        result = completion(supplied_workflow)
        state, events, before_state, before_events = targets(
            tmp_path, index=len(supplied_workflow.steps), provider="other"
        )
    else:
        result = failure(supplied_workflow)
        state, events, before_state, before_events = targets(
            tmp_path, status="failed", index=3, provider="other"
        )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 124 must not be called")

    assert invoke(result, supplied_workflow, None, state, events, forbidden) is result
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("route", ["completion", "failure"])
@pytest.mark.parametrize("employee_value", ["employee"])
def test_stop_routes_reject_non_none_employee_with_zero_calls(
    tmp_path: Path, route: str, employee_value: str
) -> None:
    supplied_workflow = workflow()
    if route == "completion":
        result = completion(supplied_workflow)
        state, events, before_state, before_events = targets(
            tmp_path, index=len(supplied_workflow.steps)
        )
        expected = "completion_contract"
    else:
        result = failure(supplied_workflow)
        state, events, before_state, before_events = targets(
            tmp_path, status="failed", index=3
        )
        expected = "failure_contract"
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        employee() if employee_value == "employee" else object(),
        state,
        events,
        expected,
        forbidden,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("route", ["completion", "failure"])
def test_stop_route_result_subclasses_are_zero_call_result_type_rejections(
    tmp_path: Path, route: str
) -> None:
    supplied_workflow = workflow()
    if route == "completion":
        exact = completion(supplied_workflow)
        result: object = DecisionChild(
            decision=exact.decision,
            workflow_id=exact.workflow_id,
            current_step_id=exact.current_step_id,
            current_step_index=exact.current_step_index,
            current_employee_id=exact.current_employee_id,
            next_step_id=exact.next_step_id,
            next_step_index=exact.next_step_index,
            next_employee_id=exact.next_employee_id,
            reason=exact.reason,
        )
        state, events, before_state, before_events = targets(
            tmp_path, index=len(supplied_workflow.steps)
        )
    else:
        exact = failure(supplied_workflow)
        result = OutcomeChild(
            outcome=exact.outcome,
            workflow_id=exact.workflow_id,
            current_step_id=exact.current_step_id,
            current_step_index=exact.current_step_index,
            current_employee_id=exact.current_employee_id,
            failure_category=exact.failure_category,
        )
        state, events, before_state, before_events = targets(
            tmp_path, status="failed", index=3
        )
    assert type(result) is not type(exact)
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        None,
        state,
        events,
        "result_type",
        forbidden,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("route", ["completion", "failure"])
def test_stop_route_attribute_compatible_substitutes_are_zero_call_result_type_rejections(
    tmp_path: Path, route: str
) -> None:
    supplied_workflow = workflow()
    if route == "completion":
        exact = completion(supplied_workflow)
        result: object = SimpleNamespace(
            decision=exact.decision,
            workflow_id=exact.workflow_id,
            current_step_id=exact.current_step_id,
            current_step_index=exact.current_step_index,
            current_employee_id=exact.current_employee_id,
            next_step_id=exact.next_step_id,
            next_step_index=exact.next_step_index,
            next_employee_id=exact.next_employee_id,
            reason=exact.reason,
        )
        state, events, before_state, before_events = targets(
            tmp_path, index=len(supplied_workflow.steps)
        )
    else:
        exact = failure(supplied_workflow)
        result = SimpleNamespace(
            outcome=exact.outcome,
            workflow_id=exact.workflow_id,
            current_step_id=exact.current_step_id,
            current_step_index=exact.current_step_index,
            current_employee_id=exact.current_employee_id,
            failure_category=exact.failure_category,
        )
        state, events, before_state, before_events = targets(
            tmp_path, status="failed", index=3
        )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        None,
        state,
        events,
        "result_type",
        forbidden,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "bad",
    [
        object(),
        PreparedStepExecutionStart(
            ModelInvocationRequest("model-name", "employee instructions", "four instructions", ("tool-one", "tool-two")),
            WorkflowExecutionState("workflow", "running", "four", 4, "d", ("one", "two", "three"), None),
        ),
        RunningStatePersistenceResult(1),
        WorkflowExecutionPersistenceResult(Path("state"), Path("events"), 1, 1),
        SimpleNamespace(workflow_id="workflow", step_id="four", step_index=4),
    ],
)
def test_unsupported_direct_results_are_rejected_before_phase124(
    tmp_path: Path, bad: object
) -> None:
    state, events, *_ = targets(tmp_path)
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(bad, workflow(), employee(), state, events, "result_type", forbidden)
    assert calls == 0


@pytest.mark.parametrize("kind", ["result", "workflow", "employee"])
def test_subclass_models_are_rejected_before_phase124(tmp_path: Path, kind: str) -> None:
    supplied_workflow = workflow()
    value = prepared()
    person = employee()
    state, events, *_ = targets(tmp_path)
    values: dict[str, object] = {
        "result": PreparedChild(*value.__dict__.values()),
        "workflow": WorkflowChild.model_validate(supplied_workflow.model_dump()),
        "employee": EmployeeChild.model_validate(person.model_dump()),
    }
    supplied: dict[str, object] = {
        "result": value,
        "workflow": supplied_workflow,
        "employee": person,
    }
    supplied[kind] = values[kind]
    expected = "result_type" if kind == "result" else (
        "workflow_definition" if kind == "workflow" else "employee_contract"
    )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        supplied["result"],
        supplied["workflow"],
        supplied["employee"],
        state,
        events,
        expected,
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize("kind", ["result", "workflow", "employee"])
def test_attribute_compatible_substitutes_are_rejected_before_phase124(
    tmp_path: Path, kind: str
) -> None:
    supplied_workflow = workflow()
    value = prepared()
    person = employee()
    state, events, *_ = targets(tmp_path)
    values: dict[str, object] = {
        "result": SimpleNamespace(**value.__dict__),
        "workflow": SimpleNamespace(**supplied_workflow.__dict__),
        "employee": SimpleNamespace(**person.__dict__),
    }
    supplied: dict[str, object] = {
        "result": value,
        "workflow": supplied_workflow,
        "employee": person,
    }
    supplied[kind] = values[kind]
    expected = "result_type" if kind == "result" else (
        "workflow_definition" if kind == "workflow" else "employee_contract"
    )
    assert_rejected(
        supplied["result"],
        supplied["workflow"],
        supplied["employee"],
        state,
        events,
        expected,
        lambda *_: pytest.fail("Phase 124 was called"),
    )


def test_workflow_step_subclass_and_attribute_substitute_are_rejected(
    tmp_path: Path,
) -> None:
    value = prepared()
    person = employee()
    state, events, *_ = targets(tmp_path)
    supplied_workflow = workflow()
    original = supplied_workflow.steps[0]
    child_step = StepChild.model_validate(original.model_dump())
    substituted = SimpleNamespace(**original.__dict__)
    for bad_step in (child_step, substituted):
        bad_workflow = supplied_workflow.model_copy(
            update={"steps": [bad_step, *supplied_workflow.steps[1:]]}
        )
        assert type(bad_workflow) is WorkflowDefinition
        assert bad_workflow.steps[0].id == original.id
        calls = 0

        def forbidden(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(
            value,
            bad_workflow,
            person,
            state,
            events,
            "workflow_definition",
            forbidden,
        )
        assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "other"),
        ("step_id", "other"),
        ("step_index", IntChild(4)),
        ("step_index", 3),
        ("employee_id", "other"),
        ("employee_instructions", 4),
        ("employee_instructions", "wrong"),
        ("step_instructions", 4),
        ("step_instructions", "wrong"),
        ("model", 4),
        ("model", "wrong"),
        ("allowed_tool_names", ["tool-one", "tool-two"]),
        ("allowed_tool_names", ("tool-one", "other")),
    ],
)
def test_prepared_input_contract_matrix(
    tmp_path: Path, field: str, value: object
) -> None:
    supplied_workflow = workflow()
    supplied = prepared()
    state, events, *_ = targets(tmp_path)
    bad = replace(supplied, **{field: value})
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        bad,
        supplied_workflow,
        employee(),
        state,
        events,
        "employee_contract" if field == "employee_id" else "prepared_step_contract",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "other"),
        ("name", 4),
        ("role", 4),
        ("instructions", 4),
        ("model", 4),
        ("allowed_tools", ("tool-one", "tool-two")),
        ("allowed_tools", ["tool-one", 4]),
    ],
)
def test_employee_linkage_and_exact_field_matrix(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, *_ = targets(tmp_path)
    bad = employee().model_copy(update={field: value})
    assert_rejected(
        prepared(),
        workflow(),
        bad,
        state,
        events,
        "employee_contract",
        lambda *_: pytest.fail("Phase 124 was called"),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("instructions", "wrong"),
        ("model", "wrong"),
        ("allowed_tools", ["tool-one", "wrong-tool"]),
    ],
)
def test_employee_semantic_linkage_mismatches_are_rejected_before_phase124(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, before_state, before_events = targets(tmp_path)
    bad_employee = employee().model_copy(update={field: value})
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        prepared(),
        workflow(),
        bad_employee,
        state,
        events,
        "prepared_step_contract",
        forbidden,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("provider", ["other", 4])
def test_predecessor_terminal_provider_must_be_exact_openai(
    tmp_path: Path, provider: object
) -> None:
    state, events, *_ = targets(tmp_path, provider=provider)
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        prepared(),
        workflow(),
        employee(),
        state,
        events,
        "terminal_contract",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": "other"},
        {"step_id": "other"},
        {"step_index": 4},
        {"employee_id": "other"},
        {"request_id": 4},
        {"request_id": ""},
        {"response_id": None},
        {"output_text": None},
        {"event_type": "step_failed", "next_status": "failed"},
        {"next_status": "failed"},
        {"failure_category": "api_error"},
    ],
)
def test_predecessor_terminal_event_linkage_is_rejected_before_phase124(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 2, **changes)
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        prepared(),
        workflow(),
        employee(),
        state,
        events,
        "terminal_contract",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize(
    "mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"]
)
def test_predecessor_history_matrix_is_rejected_before_phase124(
    tmp_path: Path, mode: str
) -> None:
    state, events, *_ = targets(tmp_path)
    lines = events.read_text(encoding="utf-8").splitlines()
    if mode == "duplicate":
        lines = [lines[0], lines[0], *lines[1:]]
    elif mode == "missing":
        lines = [lines[0], lines[2]]
    elif mode == "reordered":
        lines = [lines[1], lines[0], lines[2]]
    elif mode == "unrelated":
        payload = json.loads(lines[0])
        payload["workflow_id"] = "other"
        lines[0] = json.dumps(payload, separators=(",", ":"))
    elif mode == "malformed":
        events.write_bytes(b"not-json\n")
        lines = []
    else:
        lines = [*lines, lines[-1]]
    if mode != "malformed":
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        prepared(),
        workflow(),
        employee(),
        state,
        events,
        "terminal_contract",
        forbidden,
    )
    assert calls == 0


def test_mismatched_predecessor_terminal_state_is_rejected_before_phase124(
    tmp_path: Path,
) -> None:
    state, events, *_ = targets(tmp_path)
    mismatched = WorkflowExecutionState(
        "workflow", "succeeded", "two", 2, "b", ("one", "two"), None
    )
    state.write_bytes(serialize_workflow_execution_state_json(mismatched).encode("utf-8"))
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        prepared(),
        workflow(),
        employee(),
        state,
        events,
        "terminal_contract",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "prepare_next_step"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(5)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("next_step_id", "other"),
        ("next_step_index", 4),
        ("next_employee_id", "other"),
        ("reason", "wrong"),
    ],
)
def test_workflow_complete_stop_contract_matrix(
    tmp_path: Path, field: str, value: object
) -> None:
    supplied_workflow = workflow()
    bad = replace(completion(supplied_workflow), **{field: value})
    state, events, before_state, before_events = targets(
        tmp_path, index=len(supplied_workflow.steps)
    )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        bad,
        supplied_workflow,
        None,
        state,
        events,
        "completion_contract",
        forbidden,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("outcome", "persisted_success"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", IntChild(3)),
        ("current_step_index", True),
        ("current_employee_id", "other"),
        ("failure_category", None),
    ],
)
def test_persisted_failure_stop_contract_matrix(
    tmp_path: Path, field: str, value: object
) -> None:
    supplied_workflow = workflow()
    bad = replace(failure(supplied_workflow), **{field: value})
    state, events, before_state, before_events = targets(
        tmp_path, status="failed", index=3
    )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        bad,
        supplied_workflow,
        None,
        state,
        events,
        "failure_contract",
        forbidden,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("route", ["completion", "failure"])
def test_malformed_stop_values_are_zero_call_stops(
    tmp_path: Path, route: str
) -> None:
    supplied_workflow = workflow()
    if route == "completion":
        result: object = replace(completion(supplied_workflow), decision="unsupported")
        state, events, *_ = targets(tmp_path, index=len(supplied_workflow.steps))
        expected = "completion_contract"
    else:
        result = replace(failure(supplied_workflow), outcome="persisted_success")
        state, events, *_ = targets(tmp_path, status="failed", index=3)
        expected = "failure_contract"
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        None,
        state,
        events,
        expected,
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request", SimpleNamespace(model="model-name", system_instructions="employee instructions", task_instructions="four instructions", allowed_tools=("tool-one", "tool-two"))),
        ("running_state", SimpleNamespace(workflow_id="workflow", status="running", current_step_id="four", current_step_index=4, current_employee_id="d", completed_step_ids=("one", "two", "three"), last_failure_category=None)),
    ],
)
def test_nested_attribute_compatible_start_substitutes_are_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    supplied_workflow = workflow()
    value_to_check = prepared()
    expected = start_for(value_to_check, supplied_workflow)
    bad = replace(expected, **{field: value})
    state, events, *_ = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    assert_rejected(
        value_to_check,
        supplied_workflow,
        employee(),
        state,
        events,
        "start_contract",
        fake,
    )
    assert calls == 1


@pytest.mark.parametrize("nested", ["request", "running_state"])
def test_nested_start_subclasses_are_rejected_after_one_call(
    tmp_path: Path, nested: str
) -> None:
    supplied_workflow = workflow()
    value = prepared()
    expected = start_for(value, supplied_workflow)
    request = RequestChild(
        expected.request.model,
        expected.request.system_instructions,
        expected.request.task_instructions,
        expected.request.allowed_tools,
    )
    running = StateChild(
        expected.running_state.workflow_id,
        expected.running_state.status,
        expected.running_state.current_step_id,
        expected.running_state.current_step_index,
        expected.running_state.current_employee_id,
        expected.running_state.completed_step_ids,
        expected.running_state.last_failure_category,
    )
    bad = PreparedStepExecutionStart(request if nested == "request" else expected.request, running if nested == "running_state" else expected.running_state)
    state, events, *_ = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    assert_rejected(value, supplied_workflow, employee(), state, events, "start_contract", fake)
    assert calls == 1


def test_top_level_start_subclass_is_rejected_after_one_call(
    tmp_path: Path,
) -> None:
    supplied_workflow = workflow()
    value = prepared()
    expected = start_for(value, supplied_workflow)
    bad = StartChild(expected.request, expected.running_state)
    assert type(bad) is not PreparedStepExecutionStart
    state, events, before_state, before_events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    assert_rejected(
        value,
        supplied_workflow,
        employee(),
        state,
        events,
        "start_contract",
        fake,
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_top_level_attribute_compatible_start_substitute_is_rejected_after_one_call(
    tmp_path: Path,
) -> None:
    supplied_workflow = workflow()
    value = prepared()
    expected = start_for(value, supplied_workflow)
    bad = SimpleNamespace(
        request=expected.request,
        running_state=expected.running_state,
    )
    assert bad.request is expected.request
    assert bad.running_state is expected.running_state
    state, events, before_state, before_events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    assert_rejected(
        value,
        supplied_workflow,
        employee(),
        state,
        events,
        "start_contract",
        fake,
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("nested", "field", "value"),
    [
        ("request", "model", 4),
        ("request", "model", "wrong-model"),
        ("request", "system_instructions", "wrong"),
        ("request", "task_instructions", "wrong"),
        ("request", "allowed_tools", ["tool-one", "tool-two"]),
        ("request", "allowed_tools", ("tool-one", "wrong-tool")),
        ("request", "allowed_tools", ("tool-one", IntChild(2))),
        ("running", "workflow_id", "other"),
        ("running", "status", "succeeded"),
        ("running", "current_step_id", "other"),
        ("running", "current_step_index", True),
        ("running", "current_step_index", IntChild(4)),
        ("running", "current_employee_id", "other"),
        ("running", "completed_step_ids", ["one", "two", "three"]),
        ("running", "completed_step_ids", ("one", "two", "wrong")),
        ("running", "last_failure_category", "api_error"),
    ],
)
def test_nested_start_field_contract_matrix(
    tmp_path: Path, nested: str, field: str, value: object
) -> None:
    supplied_workflow = workflow()
    value_to_check = prepared()
    expected = start_for(value_to_check, supplied_workflow)
    if nested == "request":
        bad = replace(expected, request=replace(expected.request, **{field: value}))
    else:
        bad = replace(
            expected,
            running_state=replace(expected.running_state, **{field: value}),
        )
    state, events, *_ = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    assert_rejected(value_to_check, supplied_workflow, employee(), state, events, "start_contract", fake)
    assert calls == 1


@pytest.mark.parametrize("returned", [object(), SimpleNamespace(request="request", running_state="state")])
def test_malformed_phase124_returns_are_rejected_after_one_call(
    tmp_path: Path, returned: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    assert_rejected(value, workflow(), employee(), state, events, "start_contract", fake)
    assert calls == 1


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_return_target_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        return returned

    assert_rejected(value, workflow(), employee(), state, events, "start_contract", fake)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_return_target_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    malformed = SimpleNamespace(**start_for(value).__dict__)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        return malformed

    assert_rejected(value, workflow(), employee(), state, events, "start_contract", fake)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_phase124_error_identity_and_compensation(
    tmp_path: Path, mutation: str
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    safe_error = Phase124Error("safe detail")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        raise safe_error

    with pytest.raises(Phase124Error) as caught:
        invoke(value, workflow(), employee(), state, events, fake)
    assert caught.value is safe_error
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_phase124_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        raise RuntimeError("secret detail")

    with pytest.raises(
        PreparedStepStartCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        invoke(value, workflow(), employee(), state, events, fake)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "failed_targets",
    [{"state"}, {"events"}, {"state", "events"}],
    ids=["state", "events", "both"],
)
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_targets: set[str],
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    original_write = _TestPath.write_bytes
    attempts: list[str] = []
    armed = False
    calls = 0

    def write(path: Path, data: bytes) -> int:
        nonlocal armed
        if armed and data == before_state and path == state:
            attempts.append("state")
            if "state" in failed_targets:
                raise OSError("state rollback detail")
        if armed and data == before_events and path == events:
            attempts.append("events")
            if "events" in failed_targets:
                raise OSError("events rollback detail")
        return original_write(path, data)

    monkeypatch.setattr(_TestPath, "write_bytes", write)

    def fake(*_: object) -> object:
        nonlocal armed, calls
        calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        armed = True
        return object()

    assert_rejected(
        value,
        workflow(),
        employee(),
        state,
        events,
        "dependency_rollback",
        fake,
    )
    assert calls == 1
    assert attempts.count("state") == 1
    assert attempts.count("events") == 1


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_non_regular_targets_are_rejected_before_phase124(
    tmp_path: Path, target: str, kind: str
) -> None:
    state, events, *_ = targets(tmp_path)
    selected = state if target == "state" else events
    selected.unlink()
    if kind == "directory":
        selected.mkdir()
    assert_rejected(
        prepared(),
        workflow(),
        employee(),
        state,
        events,
        "state_target" if target == "state" else "event_target",
        lambda *_: pytest.fail("Phase 124 was called"),
    )


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_state_and_event_target_oserrors_are_separately_classified(
    tmp_path: Path,
    target: str,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, events, *_ = targets(tmp_path)
    selected = state if target == "state" else events
    original = getattr(_TestPath, operation)

    def fail(path: Path, *args: object, **kwargs: object):
        if path == selected:
            raise OSError("target detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(_TestPath, operation, fail)
    assert_rejected(
        prepared(),
        workflow(),
        employee(),
        state,
        events,
        "state_target" if target == "state" else "event_target",
        lambda *_: pytest.fail("Phase 124 was called"),
    )


def test_path_subclasses_conflict_and_noncallable_dependency_are_rejected(
    tmp_path: Path,
) -> None:
    state, events, *_ = targets(tmp_path)
    assert_rejected(
        prepared(),
        workflow(),
        employee(),
        PathChild(state),
        events,
        "state_target",
        lambda *_: pytest.fail("Phase 124 was called"),
    )
    assert_rejected(
        prepared(),
        workflow(),
        employee(),
        state,
        state,
        "target_conflict",
        lambda *_: pytest.fail("Phase 124 was called"),
    )
    assert_rejected(
        prepared(),
        workflow(),
        employee(),
        state,
        events,
        "start_contract",
        None,
    )


def test_prepared_route_accepts_exact_empty_success_output_with_fallback(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 2, output_text="")
    before_state = state.read_bytes()
    before_events = events.read_bytes()
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "field, value",
    [("current_step_id", "wrong"), ("current_employee_id", "wrong")],
)
def test_empty_success_fallback_preserves_workflow_linkage(
    tmp_path: Path, field: str, value: str
) -> None:
    prepared_value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 2, output_text="", **{field: value})
    state_payload = json.loads(state.read_text(encoding="utf-8"))
    state_payload[field] = value
    state.write_text(json.dumps(state_payload, separators=(",", ":")), encoding="utf-8")
    before_state = state.read_bytes()
    before_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return start_for(prepared_value)

    assert_rejected(
        prepared_value,
        workflow(),
        employee(),
        state,
        events,
        "terminal_contract",
        fake,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase131_workflow_complete_empty_success_stays_zero_call_rejection(
    tmp_path: Path,
) -> None:
    state, events, *_ = targets(tmp_path, index=5)
    rewrite_event(events, 4, output_text="")
    before_state = state.read_bytes()
    before_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        completion(workflow()),
        workflow(),
        None,
        state,
        events,
        "terminal_contract",
        fake,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase131_fallback_preserves_preexisting_non_openai_earlier_provider(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 0, provider="other")
    rewrite_event(events, 2, output_text="")
    before_state = state.read_bytes()
    before_events = events.read_bytes()
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("provider", ["other", "", 4])
def test_phase131_empty_success_fallback_rejects_terminal_provider(
    tmp_path: Path, provider: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 2, output_text="", provider=provider)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return start_for(value)

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("response_id", ["", 4, None])
def test_phase131_empty_success_fallback_rejects_terminal_response_id(
    tmp_path: Path, response_id: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 2, output_text="", response_id=response_id)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return start_for(value)

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("request_id", ["", 4])
def test_phase131_empty_success_fallback_rejects_terminal_request_id(
    tmp_path: Path, request_id: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 2, output_text="", request_id=request_id)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return start_for(value)

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase131_empty_success_fallback_accepts_terminal_request_id_none(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 2, output_text="", request_id=None)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("output_text", [4, None])
def test_phase131_empty_success_fallback_rejects_non_string_output(
    tmp_path: Path, output_text: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 2, output_text=output_text)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return start_for(value)

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase131_prepared_nonempty_success_remains_valid(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 2, output_text="non-empty")
    before_state, before_events = state.read_bytes(), events.read_bytes()
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_phase131_fallback_preserves_preexisting_provider_request_combination(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 0, provider="other", request_id=None)
    rewrite_event(events, 2, output_text="")
    before_state, before_events = state.read_bytes(), events.read_bytes()
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
