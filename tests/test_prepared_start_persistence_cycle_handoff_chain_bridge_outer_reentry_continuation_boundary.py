"""Focused fake-only tests for the Phase 139 outer prepared-start bridge."""

# ruff: noqa: E501

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as public_route,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeReentryContinuationError as Phase132Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary as phase132_route,
)
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
)
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class PreparedChild(PreparedStepExecutionStart):
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


class RequestChild(ModelInvocationRequest):
    pass


class StateChild(WorkflowExecutionState):
    pass


class IntChild(int):
    pass


class TupleChild(tuple):
    pass


class PersistenceChild(RunningStatePersistenceResult):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "focused",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "c", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "d", "instructions": "four"},
                {"id": "five", "name": "Five", "employee": "e", "instructions": "five"},
            ],
        }
    )


def employee(index: int = 5) -> EmployeeDefinition:
    step = workflow().steps[index - 1]
    return EmployeeDefinition.model_validate(
        {
            "id": step.employee,
            "name": step.name,
            "role": "role",
            "instructions": "employee instructions",
            "model": "model",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def start(index: int = 5, supplied_workflow: WorkflowDefinition | None = None) -> PreparedStepExecutionStart:
    definition = workflow() if supplied_workflow is None else supplied_workflow
    step = definition.steps[index - 1]
    person = employee(index)
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            person.model,
            person.instructions,
            step.instructions,
            tuple(person.allowed_tools),
        ),
        WorkflowExecutionState(
            definition.id,
            "running",
            step.id,
            index,
            step.employee,
            tuple(item.id for item in definition.steps[: index - 1]),
            None,
        ),
    )


def _event(
    definition: WorkflowDefinition,
    index: int,
    *,
    status: str = "succeeded",
    provider: object = "openai",
    output_text: object = "output",
    request_id: object = "request",
    response_id: object = "response",
    **changes: object,
) -> RuntimeStepEvent:
    step = definition.steps[index - 1]
    event = RuntimeStepEvent(
        "step_succeeded" if status == "succeeded" else "step_failed",
        definition.id,
        step.id,
        index,
        step.employee,
        "running",
        status,
        provider,
        None if status == "succeeded" else "api_error",
        response_id if status == "succeeded" else None,
        request_id,
        output_text if status == "succeeded" else None,
        None if status == "succeeded" else "safe failure",
    )
    return replace(event, **changes)


def predecessor_targets(
    tmp_path: Path,
    *,
    index: int = 4,
    output_text: object = "output",
    terminal_provider: object = "openai",
    terminal_request_id: object = "request",
    terminal_response_id: object = "response",
) -> tuple[Path, Path, bytes, bytes]:
    definition = workflow()
    current = definition.steps[index - 1]
    state = WorkflowExecutionState(
        "workflow",
        "succeeded",
        current.id,
        index,
        current.employee,
        tuple(item.id for item in definition.steps[:index]),
        None,
    )
    events = [
        _event(
            definition,
            position,
            provider="other" if position < index - 1 else "openai",
            request_id=f"request-{definition.steps[position - 1].id}",
            response_id=f"response-{definition.steps[position - 1].id}",
        )
        for position in range(1, index)
    ] + [
        _event(
            definition,
            index,
            provider=terminal_provider,
            output_text=output_text,
            request_id=terminal_request_id,
            response_id=terminal_response_id,
        )
    ]
    state_bytes = serialize_workflow_execution_state_json(state).encode()
    event_bytes = b"".join(serialize_runtime_step_event_jsonl(event).encode() for event in events)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def stop_targets(
    tmp_path: Path,
    *,
    status: str,
    index: int,
    provider: object = "other",
    output_text: object = "output",
) -> tuple[Path, Path, bytes, bytes]:
    definition = workflow()
    completed = (
        tuple(step.id for step in definition.steps[:index])
        if status == "succeeded"
        else tuple(step.id for step in definition.steps[: index - 1])
    )
    state = WorkflowExecutionState(
        definition.id,
        status,
        definition.steps[index - 1].id,
        index,
        definition.steps[index - 1].employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    events = [
        _event(definition, position, provider="openai")
        for position in range(1, index)
    ] + [
        _event(
            definition,
            index,
            status=status,
            provider=provider,
            output_text=output_text,
        )
    ]
    state_bytes = serialize_workflow_execution_state_json(state).encode()
    event_bytes = b"".join(serialize_runtime_step_event_jsonl(event).encode() for event in events)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def _rewrite_state(path: Path, **changes: object) -> None:
    payload = json.loads(path.read_text())
    payload.update(changes)
    path.write_text(json.dumps(payload, separators=(",", ":")))


def _rewrite_event(path: Path, index: int, **changes: object) -> None:
    lines = path.read_text().splitlines()
    payload = json.loads(lines[index])
    payload.update(changes)
    lines[index] = json.dumps(payload, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")


def _valid_persistence(value: PreparedStepExecutionStart) -> RunningStatePersistenceResult:
    contents = serialize_workflow_execution_state_json(value.running_state).encode()
    return RunningStatePersistenceResult(len(contents))


def invoke(
    result: object,
    supplied_workflow: object,
    supplied_employee: object,
    state: object,
    events: object,
    dependency: object = phase132_route,
) -> object:
    return public_route(
        result,
        supplied_workflow,
        supplied_employee,
        state,
        events,
        phase132_function=dependency,
    )


def reject(callable_object, classification: str) -> None:
    with pytest.raises(
        PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        callable_object()
    assert caught.value.detail.classification == classification


def test_public_signature_default_and_source_audit() -> None:
    parameters = tuple(inspect.signature(public_route).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result", "workflow", "employee", "state_path", "events_path", "phase132_function"
    )
    assert all(parameter.annotation is object for parameter in parameters[:5])
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parameters[:5])
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[5].default is phase132_route
    source = Path(
        "src/ai_office/engine/prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py"
    ).read_text()
    assert "route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary" in source
    assert "phase125" not in source.lower()
    assert "phase133" not in source.lower()
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


def test_valid_prepared_route_calls_phase132_once_in_canonical_order_and_persists_state(
    tmp_path: Path,
) -> None:
    value, supplied_workflow, supplied_employee = start(), workflow(), employee()
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    expected = _valid_persistence(value)
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    calls: list[tuple[object, ...]] = []

    def fake(*arguments: object) -> RunningStatePersistenceResult:
        calls.append(arguments)
        state.write_bytes(expected_state)
        return expected

    returned = invoke(value, supplied_workflow, supplied_employee, state, events, fake)
    assert returned is expected
    assert calls == [(value, supplied_workflow, supplied_employee, state, events)]
    assert state.read_bytes() == expected_state
    assert events.read_bytes() == before_events
    assert before_state != state.read_bytes()


def test_empty_success_predecessor_output_is_accepted(tmp_path: Path) -> None:
    state, events, _, before_events = predecessor_targets(tmp_path, output_text="")
    value = start()
    expected = _valid_persistence(value)
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    calls = 0

    def fake(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        return expected

    assert invoke(value, workflow(), employee(), state, events, fake) is expected
    assert calls == 1
    assert events.read_bytes() == before_events


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_index_one_rejects_and_indices_two_to_four_delegate_once(
    tmp_path: Path, index: int
) -> None:
    target_index = index - 1 if index >= 2 else 4
    state, events, before_state, before_events = predecessor_targets(
        tmp_path, index=target_index
    )
    supplied_workflow = workflow()
    supplied_employee = employee(index)
    value = start(index, supplied_workflow)
    expected = _valid_persistence(value)
    expected_state = serialize_workflow_execution_state_json(
        value.running_state
    ).encode()
    calls: list[tuple[object, ...]] = []

    def fake(*arguments: object) -> RunningStatePersistenceResult:
        calls.append(arguments)
        state.write_bytes(expected_state)
        return expected

    if index == 1:
        reject(
            lambda: invoke(
                value,
                supplied_workflow,
                supplied_employee,
                state,
                events,
                fake,
            ),
            "start_contract",
        )
        assert calls == []
        assert state.read_bytes() == before_state
    else:
        assert invoke(
            value,
            supplied_workflow,
            supplied_employee,
            state,
            events,
            fake,
        ) is expected
        assert calls == [
            (value, supplied_workflow, supplied_employee, state, events)
        ]
        assert state.read_bytes() == expected_state
        assert state.read_bytes() != before_state
    assert events.read_bytes() == before_events


@pytest.mark.parametrize("kind", ["result", "workflow", "employee"])
def test_exact_model_subclasses_are_zero_call_rejections(tmp_path: Path, kind: str) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value, supplied_workflow, supplied_employee = start(), workflow(), employee()
    bad: dict[str, object] = {
        "result": PreparedChild(*tuple(value.__dict__.values())),
        "workflow": WorkflowChild.model_validate(supplied_workflow.model_dump()),
        "employee": EmployeeChild.model_validate(supplied_employee.model_dump()),
    }
    supplied = {"result": value, "workflow": supplied_workflow, "employee": supplied_employee}
    supplied[kind] = bad[kind]
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    expected = "result_type" if kind == "result" else "workflow_definition" if kind == "workflow" else "employee_contract"
    reject(lambda: invoke(supplied["result"], supplied["workflow"], supplied["employee"], state, events, fake), expected)
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_nested_start_subclasses_and_substitutes_are_zero_call_rejected(tmp_path: Path) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    cases = [
        StartChild(*tuple(value.__dict__.values())),
        PreparedStepExecutionStart(
            RequestChild(*tuple(value.request.__dict__.values())), value.running_state
        ),
        PreparedStepExecutionStart(
            value.request, StateChild(*tuple(value.running_state.__dict__.values()))
        ),
        SimpleNamespace(request=value.request, running_state=value.running_state),
    ]
    for bad in cases:
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        expected = "result_type" if type(bad) is not PreparedStepExecutionStart else "start_contract"
        reject(lambda bad=bad: invoke(bad, workflow(), employee(), state, events, fake), expected)
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_workflow_step_subclass_and_attribute_substitute_are_zero_call_rejected(tmp_path: Path) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    supplied = workflow()
    step = supplied.steps[4]
    child = list(supplied.steps)
    child[4] = StepChild.model_validate(step.model_dump())
    child_workflow = supplied.model_copy(update={"steps": child})
    substitute = supplied.model_copy(update={
        "steps": [*supplied.steps[:4], SimpleNamespace(id=step.id, name=step.name, employee=step.employee, instructions=step.instructions)]
    })
    for bad in (child_workflow, substitute):
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        reject(lambda bad=bad: invoke(start(), bad, employee(), state, events, fake), "workflow_definition")
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_fully_compatible_workflow_and_employee_substitutes_are_zero_call_rejected(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    definition = workflow()
    person = employee()
    workflow_substitute = SimpleNamespace(
        id=definition.id,
        name=definition.name,
        description=definition.description,
        steps=definition.steps,
    )
    employee_substitute = SimpleNamespace(
        id=person.id,
        name=person.name,
        role=person.role,
        instructions=person.instructions,
        model=person.model,
        allowed_tools=person.allowed_tools,
    )
    for supplied_workflow, supplied_employee, classification in (
        (workflow_substitute, person, "workflow_definition"),
        (definition, employee_substitute, "employee_contract"),
    ):
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        reject(
            lambda: invoke(
                start(), supplied_workflow, supplied_employee, state, events, fake
            ),
            classification,
        )
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_start_index_boundary_delegates_from_two_and_preserves_targets(
    tmp_path: Path, index: int
) -> None:
    target_index = index - 1 if index >= 2 else 4
    state, events, before_state, before_events = predecessor_targets(
        tmp_path, index=target_index
    )
    supplied_workflow = workflow()
    supplied_employee = employee(index)
    value = start(index, supplied_workflow)
    expected = _valid_persistence(value)
    expected_state = serialize_workflow_execution_state_json(
        value.running_state
    ).encode()
    calls: list[tuple[object, ...]] = []

    def fake(*arguments: object) -> RunningStatePersistenceResult:
        calls.append(arguments)
        state.write_bytes(expected_state)
        return expected

    if index == 1:
        reject(
            lambda: invoke(
                value,
                supplied_workflow,
                supplied_employee,
                state,
                events,
                fake,
            ),
            "start_contract",
        )
        assert calls == []
        assert (state.read_bytes(), events.read_bytes()) == (
            before_state,
            before_events,
        )
    else:
        assert invoke(
            value,
            supplied_workflow,
            supplied_employee,
            state,
            events,
            fake,
        ) is expected
        assert calls == [
            (value, supplied_workflow, supplied_employee, state, events)
        ]
        assert state.read_bytes() == expected_state
        assert state.read_bytes() != before_state
        assert events.read_bytes() == before_events


@pytest.mark.parametrize("field", ["model", "system_instructions", "task_instructions", "allowed_tools"])
def test_request_linkage_is_strict_before_phase132(tmp_path: Path, field: str) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    changes = {
        "model": "wrong",
        "system_instructions": "wrong",
        "task_instructions": "wrong",
        "allowed_tools": ["tool-one"],
    }
    bad_request = replace(value.request, **{field: changes[field]})
    bad = PreparedStepExecutionStart(bad_request, value.running_state)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(bad, workflow(), employee(), state, events, fake), "start_contract")
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "allowed_tools",
    [
        TupleChild(("tool-one", "tool-two")),
        ("tool-one", 4),
        ("tool-one", "wrong-tool"),
    ],
)
def test_request_allowed_tools_container_and_value_types_are_exact(
    tmp_path: Path, allowed_tools: object
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    bad = PreparedStepExecutionStart(
        replace(value.request, allowed_tools=allowed_tools), value.running_state
    )
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(bad, workflow(), employee(), state, events, fake), "start_contract")
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_predecessor_history_matrix_is_zero_call(tmp_path: Path, mutation: str) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    lines = events.read_bytes().splitlines(keepends=True)
    if mutation == "duplicate":
        mutated = lines[0] + lines[0] + b"".join(lines[2:])
    elif mutation == "missing":
        mutated = b"".join(lines[:2])
    elif mutation == "reordered":
        mutated = lines[1] + lines[0] + b"".join(lines[2:])
    elif mutation == "unrelated":
        unrelated = _event(workflow(), 5, provider="openai")
        mutated = b"".join(lines[:-1]) + serialize_runtime_step_event_jsonl(unrelated).encode()
    elif mutation == "malformed":
        mutated = b"not-json\n"
    else:
        mutated = b"".join(lines + [lines[0]])
    events.write_bytes(mutated)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(start(), workflow(), employee(), state, events, fake), "terminal_contract")
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() == mutated
    assert events.read_bytes() != before_events


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("state", "workflow_id", "wrong"),
        ("state", "current_step_id", "wrong"),
        ("state", "current_employee_id", "wrong"),
        ("event", "workflow_id", "wrong"),
        ("event", "step_id", "wrong"),
        ("event", "employee_id", "wrong"),
        ("event", "step_index", True),
        ("event", "provider", ""),
        ("event", "provider", 4),
        ("event", "request_id", ""),
        ("event", "request_id", 4),
        ("event", "response_id", ""),
        ("event", "response_id", 4),
        ("event", "output_text", 4),
    ],
)
def test_predecessor_provenance_is_zero_call(
    tmp_path: Path, target: str, field: str, value: object
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    if target == "state":
        _rewrite_state(state, **{field: value})
    else:
        _rewrite_event(events, 3 if field not in {"provider", "request_id", "response_id", "output_text"} else 2, **{field: value})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(start(), workflow(), employee(), state, events, fake), "terminal_contract")
    assert calls == 0
    assert state.read_bytes() != before_state or events.read_bytes() != before_events


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "wrong"),
        ("step_id", "wrong"),
        ("step_index", True),
        ("employee_id", "wrong"),
        ("event_type", "step_failed"),
        ("previous_status", "ready"),
        ("next_status", "failed"),
        ("failure_category", "api_error"),
        ("message", "bad"),
        ("output_text", 4),
    ],
)
def test_predecessor_terminal_event_contract_is_zero_call(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    _rewrite_event(events, 3, **{field: value})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(start(), workflow(), employee(), state, events, fake), "terminal_contract")
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() != before_events


@pytest.mark.parametrize("returned", [object(), RunningStatePersistenceResult(0), RunningStatePersistenceResult(-1), RunningStatePersistenceResult(True), RunningStatePersistenceResult(IntChild(1))])
@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_persistence_and_mutations_are_compensated_without_retry(
    tmp_path: Path, returned: object, mutation: str
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(expected_state + b"bad")
        if mutation in {"events", "both"}:
            events.write_bytes(events.read_bytes() + b"bad")
        return returned

    reject(lambda: invoke(value, workflow(), employee(), state, events, fake), "persistence_contract")
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_valid_persistence_result_with_event_mutation_is_rejected_and_compensated(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    expected = RunningStatePersistenceResult(len(expected_state))
    calls = 0

    def fake(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        events.write_bytes(before_events + b"unexpected-event")
        return expected

    reject(
        lambda: invoke(value, workflow(), employee(), state, events, fake),
        "persistence_contract",
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_valid_persistence_result_preserves_exact_identity(tmp_path: Path) -> None:
    state, events, _, before_events = predecessor_targets(tmp_path)
    value = start()
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    result = RunningStatePersistenceResult(len(expected_state))

    def fake(*_: object) -> RunningStatePersistenceResult:
        state.write_bytes(expected_state)
        return result

    assert invoke(value, workflow(), employee(), state, events, fake) is result
    assert events.read_bytes() == before_events


def test_state_bytes_wrong_positive_count_is_rejected_and_compensated(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    returned = RunningStatePersistenceResult(len(expected_state) + 1)
    calls = 0

    def fake(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        return returned

    reject(
        lambda: invoke(value, workflow(), employee(), state, events, fake),
        "persistence_contract",
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_error_identity_is_preserved_after_compensation(tmp_path: Path, mutation: str) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    supplied_error = Phase132Error("safe")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed events")
        raise supplied_error

    with pytest.raises(Phase132Error) as caught:
        invoke(start(), workflow(), employee(), state, events, fake)
    assert caught.value is supplied_error
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_error_is_detail_safe_and_compensated(tmp_path: Path, mutation: str) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed events")
        raise RuntimeError("secret detail")

    with pytest.raises(PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError) as caught:
        invoke(start(), workflow(), employee(), state, events, fake)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str) -> None:
    state, events, original_state, original_events = predecessor_targets(tmp_path)
    calls = 0
    writes: list[Path] = []
    original_write = Path.write_bytes

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        original_write(state, b"changed state")
        original_write(events, b"changed events")
        return object()

    def write(path: Path, data: bytes) -> int:
        if (path == state and data == original_state) or (path == events and data == original_events):
            writes.append(path)
            if (path == state and failed in {"state", "both"}) or (path == events and failed in {"events", "both"}):
                raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    reject(lambda: invoke(start(), workflow(), employee(), state, events, fake), "dependency_rollback")
    assert calls == 1
    assert writes == [state, events]


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_stop_routes_are_identity_preserving_zero_call_stops(tmp_path: Path, result_kind: str) -> None:
    if result_kind == "completion":
        result = WorkflowProgressionDecision("workflow_complete", "workflow", "five", 5, "e", None, None, None, "last_step_succeeded")
        state, events, before_state, before_events = stop_targets(tmp_path, status="succeeded", index=5)
    else:
        result = PersistedExecutionOutcome("persisted_failure", "workflow", "four", 4, "d", "api_error")
        state, events, before_state, before_events = stop_targets(tmp_path, status="failed", index=4)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 132 must not be called")

    assert invoke(result, workflow(), None, state, events, fake) is result
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_stop_result_subclasses_and_substitutes_are_zero_call_rejected(
    tmp_path: Path, result_kind: str
) -> None:
    if result_kind == "completion":
        exact = WorkflowProgressionDecision(
            "workflow_complete", "workflow", "five", 5, "e", None, None, None,
            "last_step_succeeded",
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="succeeded", index=5
        )
        bad_values = [
            DecisionChild(*tuple(exact.__dict__.values())),
            SimpleNamespace(**exact.__dict__),
        ]
    else:
        exact = PersistedExecutionOutcome(
            "persisted_failure", "workflow", "four", 4, "d", "api_error"
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="failed", index=4
        )
        bad_values = [
            OutcomeChild(*tuple(exact.__dict__.values())),
            SimpleNamespace(**exact.__dict__),
        ]

    for bad in bad_values:
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        reject(
            lambda bad=bad: invoke(bad, workflow(), None, state, events, fake),
            "result_type",
        )
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_stop_routes_reject_non_none_employee_with_zero_calls(
    tmp_path: Path, result_kind: str
) -> None:
    if result_kind == "completion":
        result = WorkflowProgressionDecision(
            "workflow_complete", "workflow", "five", 5, "e", None, None, None,
            "last_step_succeeded",
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="succeeded", index=5
        )
        classification = "completion_contract"
    else:
        result = PersistedExecutionOutcome(
            "persisted_failure", "workflow", "four", 4, "d", "api_error"
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="failed", index=4
        )
        classification = "failure_contract"
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(result, workflow(), employee(), state, events, fake),
        classification,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_workflow_complete_empty_success_output_is_rejected_zero_call(tmp_path: Path) -> None:
    result = WorkflowProgressionDecision("workflow_complete", "workflow", "five", 5, "e", None, None, None, "last_step_succeeded")
    state, events, before_state, before_events = stop_targets(tmp_path, status="succeeded", index=5, output_text="")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(result, workflow(), None, state, events, fake), "terminal_contract")
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("result_kind", "value"),
    [
        ("completion", True),
        ("completion", IntChild(5)),
        ("failure", True),
        ("failure", IntChild(4)),
    ],
)
def test_stop_index_requires_exact_builtin_int(
    tmp_path: Path, result_kind: str, value: object
) -> None:
    if result_kind == "completion":
        result = WorkflowProgressionDecision(
            "workflow_complete", "workflow", "five", 5, "e", None, None, None,
            "last_step_succeeded",
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="succeeded", index=5
        )
        classification = "completion_contract"
    else:
        result = PersistedExecutionOutcome(
            "persisted_failure", "workflow", "four", 4, "d", "api_error"
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="failed", index=4
        )
        classification = "failure_contract"
    object.__setattr__(result, "current_step_index", value)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(result, workflow(), None, state, events, fake), classification)
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_direct_unsupported_inputs_are_zero_call(tmp_path: Path) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    runtime_results = [
        StepRuntimeExecutionSuccess(
            "workflow",
            "five",
            5,
            "e",
            ModelInvocationSuccess(
                "openai", "response", None, "completed", ("output",), "output"
            ),
        ),
        StepRuntimeExecutionFailure(
            "workflow",
            "five",
            5,
            "e",
            ModelInvocationFailure(
                "openai", "api_error", "safe", None, None, None, None
            ),
        ),
    ]
    for bad in [
        RunningStatePersistenceResult(1),
        PreparedWorkflowStep(
            "workflow", "five", 5, "e", "employee instructions", "five", "model",
            ("tool-one", "tool-two"),
        ),
        *runtime_results,
    ]:
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        reject(
            lambda bad=bad: invoke(bad, workflow(), employee(), state, events, fake),
            "result_type",
        )
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_non_callable_dependency_and_targets_are_classified(tmp_path: Path) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    reject(lambda: invoke(start(), workflow(), employee(), state, events, object()), "persistence_contract")
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
    reject(lambda: invoke(start(), workflow(), employee(), state, state, phase132_route), "target_conflict")
    events.unlink()
    reject(lambda: invoke(start(), workflow(), employee(), state, events, phase132_route), "event_target")
    assert state.read_bytes() == before_state


@pytest.mark.parametrize("field", ["current_step_id", "current_employee_id"])
def test_same_wrong_predecessor_state_and_terminal_event_linkage_is_rejected(
    tmp_path: Path, field: str
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    _rewrite_state(state, **{field: "wrong"})
    _rewrite_event(events, 3, **{field: "wrong"})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert state.read_bytes() != before_state
    assert events.read_bytes() != before_events


def test_same_wrong_workflow_id_on_state_and_all_history_events_is_rejected(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    _rewrite_state(state, workflow_id="wrong-workflow")
    lines = events.read_text().splitlines()
    events.write_text(
        "\n".join(
            json.dumps(
                {**json.loads(line), "workflow_id": "wrong-workflow"},
                separators=(",", ":"),
            )
            for line in lines
        )
        + "\n"
    )
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert state.read_bytes() != before_state
    assert events.read_bytes() != before_events


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "wrong"),
        ("step_id", "wrong"),
        ("step_index", True),
        ("employee_id", "wrong"),
        ("event_type", "step_failed"),
        ("previous_status", "ready"),
        ("next_status", "failed"),
        ("provider", ""),
        ("provider", 4),
        ("request_id", ""),
        ("request_id", 4),
        ("response_id", ""),
        ("response_id", 4),
        ("output_text", 4),
        ("failure_category", "api_error"),
        ("message", "bad"),
    ],
)
def test_earlier_predecessor_history_fields_are_strict(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    _rewrite_event(events, 2, **{field: value})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() != before_events


def test_immediate_predecessor_non_openai_provider_is_rejected(tmp_path: Path) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    _rewrite_event(events, 2, provider="other")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() != before_events


def test_empty_immediate_predecessor_output_is_accepted_and_delegates_once(
    tmp_path: Path,
) -> None:
    state, events, _, _ = predecessor_targets(tmp_path)
    _rewrite_event(events, 2, output_text="")
    rewritten_events = events.read_bytes()
    value, supplied_workflow, supplied_employee = start(), workflow(), employee()
    expected = _valid_persistence(value)
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    calls: list[tuple[object, ...]] = []

    def fake(*arguments: object) -> RunningStatePersistenceResult:
        calls.append(arguments)
        state.write_bytes(expected_state)
        return expected

    assert invoke(value, supplied_workflow, supplied_employee, state, events, fake) is expected
    assert calls == [(value, supplied_workflow, supplied_employee, state, events)]
    assert state.read_bytes() == expected_state
    assert events.read_bytes() == rewritten_events


def test_empty_earlier_predecessor_output_remains_accepted(tmp_path: Path) -> None:
    state, events, _, _ = predecessor_targets(tmp_path)
    _rewrite_event(events, 1, output_text="")
    _rewrite_event(events, 2, output_text="later-output")
    rewritten_events = events.read_bytes()
    value = start()
    expected = _valid_persistence(value)
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    calls = 0

    def fake(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        return expected

    assert invoke(value, workflow(), employee(), state, events, fake) is expected
    assert calls == 1
    assert state.read_bytes() == expected_state
    assert events.read_bytes() == rewritten_events


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_stop_routes_keep_nonempty_predecessor_output_strictness(
    tmp_path: Path, result_kind: str
) -> None:
    if result_kind == "completion":
        result = WorkflowProgressionDecision(
            "workflow_complete", "workflow", "five", 5, "e", None, None, None,
            "last_step_succeeded",
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="succeeded", index=5
        )
    else:
        result = PersistedExecutionOutcome(
            "persisted_failure", "workflow", "four", 4, "d", "api_error"
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="failed", index=4
        )
    _rewrite_event(events, 1, output_text="")
    rewritten_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(lambda: invoke(result, workflow(), None, state, events, fake), "terminal_contract")
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() == rewritten_events


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "other"),
        ("provider", ""),
        ("provider", 4),
        ("request_id", ""),
        ("request_id", 4),
        ("response_id", ""),
        ("response_id", 4),
        ("response_id", None),
        ("output_text", 4),
        ("failure_category", "api_error"),
        ("message", "bad"),
    ],
)
def test_terminal_predecessor_fields_are_strict(
    tmp_path: Path, field: str, value: object
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    _rewrite_event(events, 3, **{field: value})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() != before_events


def test_terminal_predecessor_none_request_id_remains_valid(tmp_path: Path) -> None:
    state, events, _, before_events = predecessor_targets(
        tmp_path, terminal_request_id=None
    )
    value = start()
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    expected = RunningStatePersistenceResult(len(expected_state))
    calls = 0

    def fake(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        return expected

    assert invoke(value, workflow(), employee(), state, events, fake) is expected
    assert calls == 1
    assert events.read_bytes() == before_events


@pytest.mark.parametrize(
    "returned_factory",
    [
        lambda length: PersistenceChild(length),
        lambda length: SimpleNamespace(state_bytes_written=length),
    ],
)
def test_persistence_result_exact_type_is_required(
    tmp_path: Path, returned_factory: object
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    returned = returned_factory(len(expected_state))
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        return returned

    reject(
        lambda: invoke(value, workflow(), employee(), state, events, fake),
        "persistence_contract",
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["no_write", "wrong_state", "malformed_state"])
def test_persistence_state_transition_is_exact(tmp_path: Path, mutation: str) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    expected = RunningStatePersistenceResult(len(expected_state))
    calls = 0

    def fake(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        if mutation == "wrong_state":
            state.write_bytes(expected_state + b"wrong")
        elif mutation == "malformed_state":
            state.write_bytes(b"not-json\n")
        return expected

    reject(
        lambda: invoke(value, workflow(), employee(), state, events, fake),
        "persistence_contract",
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_semantically_wrong_running_state_is_rejected_and_compensated(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    wrong_state = WorkflowExecutionState(
        "wrong-workflow",
        "running",
        "five",
        5,
        "e",
        ("one", "two", "three", "four"),
        None,
    )
    wrong_state_bytes = serialize_workflow_execution_state_json(wrong_state).encode()
    calls = 0

    def fake(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        state.write_bytes(wrong_state_bytes)
        return RunningStatePersistenceResult(len(wrong_state_bytes))

    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        "persistence_contract",
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["state", "events"])
def test_target_is_file_oserror_is_classified_before_phase132(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    target = state if kind == "state" else events
    original = Path.is_file

    def failing(path: Path) -> bool:
        if path == target:
            raise OSError("synthetic is_file failure")
        return original(path)

    monkeypatch.setattr(Path, "is_file", failing)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    classification = "state_target" if kind == "state" else "event_target"
    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        classification,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["state", "events"])
def test_target_read_bytes_oserror_is_classified_before_phase132(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    target = state if kind == "state" else events
    original = Path.read_bytes

    def failing(path: Path) -> bytes:
        if path == target:
            raise OSError("synthetic read failure")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", failing)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    classification = "state_target" if kind == "state" else "event_target"
    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        classification,
    )
    assert calls == 0
    assert (original(state), original(events)) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["state", "events"])
def test_non_regular_target_is_rejected_before_phase132(tmp_path: Path, kind: str) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    target = state if kind == "state" else events
    target.unlink()
    target.mkdir()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    classification = "state_target" if kind == "state" else "event_target"
    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        classification,
    )
    assert calls == 0
    if kind == "state":
        assert state.is_dir()
        assert events.read_bytes() == before_events
    else:
        assert events.is_dir()
        assert state.read_bytes() == before_state


def _seven_step_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Workflow",
            "description": "focused",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "c", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "d", "instructions": "four"},
                {"id": "five", "name": "Five", "employee": "e", "instructions": "five"},
                {"id": "six", "name": "Six", "employee": "f", "instructions": "six"},
                {"id": "seven", "name": "Seven", "employee": "g", "instructions": "seven"},
            ],
        }
    )


def _seven_step_employee(index: int = 7) -> EmployeeDefinition:
    step = _seven_step_workflow().steps[index - 1]
    return EmployeeDefinition.model_validate(
        {
            "id": step.employee,
            "name": step.name,
            "role": "role",
            "instructions": "employee instructions",
            "model": "model",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def _seven_step_start(index: int = 7) -> PreparedStepExecutionStart:
    definition = _seven_step_workflow()
    step = definition.steps[index - 1]
    person = _seven_step_employee(index)
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            person.model,
            person.instructions,
            step.instructions,
            tuple(person.allowed_tools),
        ),
        WorkflowExecutionState(
            definition.id,
            "running",
            step.id,
            index,
            step.employee,
            tuple(item.id for item in definition.steps[: index - 1]),
            None,
        ),
    )


def _seven_step_predecessor_targets(
    tmp_path: Path,
    *,
    output_text: object = "output",
    terminal_provider: object = "openai",
    terminal_request_id: object = "request",
    terminal_response_id: object = "response",
) -> tuple[Path, Path, bytes, bytes]:
    definition = _seven_step_workflow()
    state = WorkflowExecutionState(
        "workflow",
        "succeeded",
        "six",
        6,
        "f",
        ("one", "two", "three", "four", "five", "six"),
        None,
    )
    events = [
        _event(definition, 1, provider="openai", request_id="request-step-1", output_text="output-step-1"),
        _event(definition, 2, provider="openai", request_id="request-step-2", output_text=""),
        _event(definition, 3, provider="openai", request_id="request-step-3", output_text=""),
        _event(definition, 4, provider="openai", request_id="request-step-4", output_text=""),
        _event(definition, 5, provider="openai", request_id=None, output_text=""),
        _event(
            definition,
            6,
            provider=terminal_provider,
            output_text=output_text,
            request_id=terminal_request_id,
            response_id=terminal_response_id,
        ),
    ]
    state_bytes = serialize_workflow_execution_state_json(state).encode()
    event_bytes = b"".join(serialize_runtime_step_event_jsonl(event).encode() for event in events)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    return state_path, events_path, state_bytes, event_bytes


def test_seven_step_immediate_none_request_id_is_accepted_and_delegates_once(
    tmp_path: Path,
) -> None:
    value, supplied_workflow, supplied_employee = (
        _seven_step_start(),
        _seven_step_workflow(),
        _seven_step_employee(),
    )
    state, events, before_state, before_events = _seven_step_predecessor_targets(tmp_path)
    expected = _valid_persistence(value)
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    calls: list[tuple[object, ...]] = []

    def fake(*arguments: object) -> RunningStatePersistenceResult:
        calls.append(arguments)
        state.write_bytes(expected_state)
        return expected

    returned = invoke(value, supplied_workflow, supplied_employee, state, events, fake)
    assert returned is expected
    assert calls == [(value, supplied_workflow, supplied_employee, state, events)]
    assert state.read_bytes() == expected_state
    assert events.read_bytes() == before_events
    assert before_state != state.read_bytes()
    # non-empty immediate predecessor request_id remains accepted and delegates once
    state, events, _, _ = _seven_step_predecessor_targets(tmp_path)
    _rewrite_event(events, 4, request_id="request-step-5")
    rewritten_events = events.read_bytes()
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    calls.clear()

    def fake(*arguments: object) -> RunningStatePersistenceResult:
        calls.append(arguments)
        state.write_bytes(expected_state)
        return expected

    returned = invoke(value, supplied_workflow, supplied_employee, state, events, fake)
    assert returned is expected
    assert calls == [(value, supplied_workflow, supplied_employee, state, events)]
    assert state.read_bytes() == expected_state
    assert events.read_bytes() == rewritten_events


def test_seven_step_immediate_none_request_id_narrowness_inline_subcases(
    tmp_path: Path,
) -> None:
    # positive control: canonical 7-step immediate None is accepted once
    value, supplied_workflow, supplied_employee = (
        _seven_step_start(),
        _seven_step_workflow(),
        _seven_step_employee(),
    )
    state, events, _, _ = _seven_step_predecessor_targets(tmp_path)
    expected = _valid_persistence(value)
    expected_state = serialize_workflow_execution_state_json(value.running_state).encode()
    calls = 0

    def fake(*_: object) -> RunningStatePersistenceResult:
        nonlocal calls
        calls += 1
        state.write_bytes(expected_state)
        return expected

    assert invoke(value, supplied_workflow, supplied_employee, state, events, fake) is expected
    assert calls == 1
    # (1) earlier predecessor (step-4, position 4) request_id=None -> reject
    state, events, before_state, _ = _seven_step_predecessor_targets(tmp_path)
    _rewrite_event(events, 3, request_id=None)
    rewritten_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(value, supplied_workflow, supplied_employee, state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, rewritten_events)
    # (2) immediate predecessor request_id="" -> reject
    state, events, before_state, _ = _seven_step_predecessor_targets(tmp_path)
    _rewrite_event(events, 4, request_id="")
    rewritten_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(value, supplied_workflow, supplied_employee, state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, rewritten_events)
    # (3) immediate predecessor request_id non-string -> reject
    state, events, before_state, _ = _seven_step_predecessor_targets(tmp_path)
    _rewrite_event(events, 4, request_id=4)
    rewritten_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(value, supplied_workflow, supplied_employee, state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, rewritten_events)
    # (4) boundary: 5-step workflow + start(5) immediate None -> reject
    state, events, before_state, _ = predecessor_targets(tmp_path)
    _rewrite_event(events, 2, request_id=None)
    rewritten_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, rewritten_events)
    # (5) stop route semantics unchanged: workflow_complete with an immediate
    #     predecessor request_id=None is still rejected at terminal_contract
    #     (Phase 139 stop routes were not relaxed by this change)
    result = WorkflowProgressionDecision(
        "workflow_complete", "workflow", "five", 5, "e", None, None, None,
        "last_step_succeeded",
    )
    state, events, before_state, _ = stop_targets(tmp_path, status="succeeded", index=5)
    _rewrite_event(events, 3, request_id=None)
    rewritten_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(result, workflow(), None, state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, rewritten_events)
    # (6) stop route semantics unchanged: persisted_failure with an immediate
    #     predecessor request_id=None is still rejected at terminal_contract
    outcome = PersistedExecutionOutcome(
        "persisted_failure", "workflow", "four", 4, "d", "api_error"
    )
    state, events, before_state, _ = stop_targets(tmp_path, status="failed", index=4)
    _rewrite_event(events, 2, request_id=None)
    rewritten_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    reject(
        lambda: invoke(outcome, workflow(), None, state, events, fake),
        "terminal_contract",
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, rewritten_events)
