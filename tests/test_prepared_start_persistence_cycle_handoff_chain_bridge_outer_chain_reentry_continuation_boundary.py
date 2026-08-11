"""Focused fake-only tests for the Phase 147 outer-chain prepared-start bridge."""

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
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as public_route,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterReentryContinuationError as Phase139Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as phase139_public,
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
    WorkflowExecutionPersistenceResult,
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
                {"id": "six", "name": "Six", "employee": "f", "instructions": "six"},
            ],
        }
    )


def employee(index: int = 6) -> EmployeeDefinition:
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


def start(
    index: int = 6, supplied_workflow: WorkflowDefinition | None = None
) -> PreparedStepExecutionStart:
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
    output_text: object = "output",
    terminal_provider: object = "openai",
    terminal_request_id: object = "request",
    terminal_response_id: object = "response",
) -> tuple[Path, Path, bytes, bytes]:
    definition = workflow()
    state = WorkflowExecutionState(
        "workflow",
        "succeeded",
        "five",
        5,
        "e",
        ("one", "two", "three", "four", "five"),
        None,
    )
    events = [
        _event(definition, 1, provider="other", request_id="request-one", response_id="response-one"),
        _event(definition, 2, provider="other", request_id="request-two", response_id="response-two"),
        _event(definition, 3, provider="other", request_id="request-three", response_id="response-three"),
        _event(definition, 4, provider="openai", request_id="request-four", response_id="response-four"),
        _event(
            definition,
            5,
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
    dependency: object,
) -> object:
    return public_route(
        result,
        supplied_workflow,
        supplied_employee,
        state,
        events,
        phase139_function=dependency,
    )


def reject(callable_object, classification: str) -> None:
    with pytest.raises(
        PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError
    ) as caught:
        callable_object()
    assert caught.value.detail.classification == classification


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


def test_public_signature_default_and_source_audit() -> None:
    parameters = tuple(inspect.signature(public_route).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result", "workflow", "employee", "state_path", "events_path", "phase139_function"
    )
    assert all(parameter.annotation is object for parameter in parameters[:5])
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parameters[:5])
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[5].default is phase139_public
    source = Path(
        "src/ai_office/engine/"
        "prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert (
        "route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary"
        in source
    )
    assert "phase132" not in source.lower()
    assert (
        "route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary"
        not in source
    )
    assert (
        "route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary"
        not in source
    )
    assert (
        "route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary"
        not in source
    )
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


def test_valid_prepared_route_calls_phase139_once_in_canonical_order_and_persists_state(
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


def test_empty_immediate_predecessor_output_is_accepted(tmp_path: Path) -> None:
    state, events, _, _ = predecessor_targets(tmp_path)
    _rewrite_event(events, 3, output_text="")
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
    assert events.read_bytes() == rewritten_events


def test_empty_earlier_predecessor_output_is_accepted(tmp_path: Path) -> None:
    state, events, _, _ = predecessor_targets(tmp_path)
    _rewrite_event(events, 0, output_text="")
    _rewrite_event(events, 2, output_text="")
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
    assert events.read_bytes() == rewritten_events


def test_terminal_empty_and_nonempty_output_are_accepted(tmp_path: Path) -> None:
    for output in ("", "final output"):
        state, events, _, before_events = predecessor_targets(tmp_path, output_text=output)
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


@pytest.mark.parametrize("index", [1, 2, 3, 4, 5])
def test_prepared_indices_one_to_five_are_zero_call_rejections(
    tmp_path: Path, index: int
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        start(index), workflow(), employee(index), state, events, "start_contract", fake
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["result", "workflow", "employee"])
def test_exact_model_subclasses_are_zero_call_rejections(
    tmp_path: Path, kind: str
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value, supplied_workflow, supplied_employee = start(), workflow(), employee()
    bad: dict[str, object] = {
        "result": PreparedChild(*tuple(value.__dict__.values())),
        "workflow": WorkflowChild.model_validate(supplied_workflow.model_dump()),
        "employee": EmployeeChild.model_validate(supplied_employee.model_dump()),
    }
    supplied = {"result": value, "workflow": supplied_workflow, "employee": supplied_employee}
    supplied[kind] = bad[kind]
    expected = {
        "result": "result_type",
        "workflow": "workflow_definition",
        "employee": "employee_contract",
    }[kind]
    calls = 0

    def fake(*_: object) -> object:
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
        fake,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_nested_start_subclasses_and_substitutes_are_zero_call_rejected(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    request = value.request
    running = value.running_state
    bad_values = [
        StartChild(*tuple(value.__dict__.values())),
        PreparedStepExecutionStart(RequestChild(*tuple(request.__dict__.values())), running),
        PreparedStepExecutionStart(SimpleNamespace(**request.__dict__), running),
        PreparedStepExecutionStart(request, StateChild(*tuple(running.__dict__.values()))),
        PreparedStepExecutionStart(request, SimpleNamespace(**running.__dict__)),
    ]
    for bad in bad_values:
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        expected = (
            "result_type"
            if type(bad) is not PreparedStepExecutionStart
            else "start_contract"
        )
        assert_rejected(bad, workflow(), employee(), state, events, expected, fake)
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_workflow_step_subclass_and_attribute_substitute_are_zero_call_rejected(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    for model in (
        WorkflowDefinition.model_validate(
            {
                "id": "workflow",
                "name": "Workflow",
                "description": "focused",
                "steps": [
                    StepChild.model_validate(step)
                    for step in workflow().model_dump()["steps"]
                ],
            }
        ),
        SimpleNamespace(**workflow().__dict__),
    ):
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(value, model, employee(), state, events, "workflow_definition", fake)
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_fully_compatible_workflow_and_employee_substitutes_are_zero_call_rejected(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    cases = [
        (SimpleNamespace(**workflow().__dict__), employee(), "workflow_definition"),
        (workflow(), SimpleNamespace(**employee().__dict__), "employee_contract"),
    ]
    for supplied_workflow, supplied_employee, expected in cases:
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(
            value, supplied_workflow, supplied_employee, state, events, expected, fake
        )
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_start_index_boundary_is_exact_and_targets_unchanged(tmp_path: Path) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    for index, expected in ((True, "start_contract"), (IntChild(6), "start_contract")):
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(
            start() if False else _start_with_index(index),
            workflow(),
            employee(),
            state,
            events,
            expected,
            fake,
        )
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def _start_with_index(index: object) -> PreparedStepExecutionStart:
    value = start()
    running = replace(value.running_state, current_step_index=index)
    return PreparedStepExecutionStart(value.request, running)


@pytest.mark.parametrize(
    "field", ["model", "system_instructions", "task_instructions", "allowed_tools"]
)
def test_request_linkage_is_strict_before_phase139(
    tmp_path: Path, field: str
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    request = value.request
    bad = {
        "model": replace(request, model="wrong-model"),
        "system_instructions": replace(request, system_instructions="wrong"),
        "task_instructions": replace(request, task_instructions="wrong"),
        "allowed_tools": replace(request, allowed_tools=("tool-one",)),
    }[field]
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        PreparedStepExecutionStart(bad, value.running_state),
        workflow(),
        employee(),
        state,
        events,
        "start_contract",
        fake,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "allowed_tools",
    [
        ["tool-one", "tool-two"],
        TupleChild(("tool-one", "tool-two")),
        ("tool-one", 4),
        ("tool-one", ""),
        (),
    ],
)
def test_request_allowed_tools_container_and_value_types_are_exact(
    tmp_path: Path, allowed_tools: object
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    value = start()
    request = replace(value.request, allowed_tools=allowed_tools)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        PreparedStepExecutionStart(request, value.running_state),
        workflow(),
        employee(),
        state,
        events,
        "start_contract",
        fake,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "mutation", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"]
)
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
        unrelated = _event(workflow(), 6, provider="openai")
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

    assert_rejected(
        start(), workflow(), employee(), state, events, "terminal_contract", fake
    )
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
        _rewrite_event(
            events,
            4 if field not in {"provider", "request_id", "response_id", "output_text"} else 3,
            **{field: value},
        )
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        start(), workflow(), employee(), state, events, "terminal_contract", fake
    )
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
    _rewrite_event(events, 4, **{field: value})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        start(), workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() != before_events


@pytest.mark.parametrize(
    "returned",
    [
        object(),
        RunningStatePersistenceResult(0),
        RunningStatePersistenceResult(-1),
        RunningStatePersistenceResult(True),
        RunningStatePersistenceResult(IntChild(1)),
    ],
)
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

    reject(
        lambda: invoke(value, workflow(), employee(), state, events, fake),
        "persistence_contract",
    )
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
def test_safe_error_identity_is_preserved_after_compensation(
    tmp_path: Path, mutation: str
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    supplied_error = Phase139Error("safe")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"changed state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"changed events")
        raise supplied_error

    with pytest.raises(Phase139Error) as caught:
        invoke(start(), workflow(), employee(), state, events, fake)
    assert caught.value is supplied_error
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_error_is_detail_safe_and_compensated(
    tmp_path: Path, mutation: str
) -> None:
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

    with pytest.raises(
        PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError
    ) as caught:
        invoke(start(), workflow(), employee(), state, events, fake)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("failed", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
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
        if (path == state and data == original_state) or (
            path == events and data == original_events
        ):
            writes.append(path)
            if (path == state and failed in {"state", "both"}) or (
                path == events and failed in {"events", "both"}
            ):
                raise OSError("rollback")
        return original_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    reject(
        lambda: invoke(start(), workflow(), employee(), state, events, fake),
        "dependency_rollback",
    )
    assert calls == 1
    assert writes == [state, events]


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_stop_routes_are_identity_preserving_zero_call_stops(
    tmp_path: Path, result_kind: str
) -> None:
    if result_kind == "completion":
        result = WorkflowProgressionDecision(
            "workflow_complete", "workflow", "six", 6, "f", None, None, None,
            "last_step_succeeded",
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="succeeded", index=6
        )
    else:
        result = PersistedExecutionOutcome(
            "persisted_failure", "workflow", "four", 4, "d", "api_error"
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="failed", index=4
        )
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 139 must not be called")

    assert invoke(result, workflow(), None, state, events, fake) is result
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_stop_routes_preserve_valid_non_openai_terminal_providers(
    tmp_path: Path, result_kind: str
) -> None:
    for provider in ("other", "custom-provider"):
        if result_kind == "completion":
            result = WorkflowProgressionDecision(
                "workflow_complete", "workflow", "six", 6, "f", None, None, None,
                "last_step_succeeded",
            )
            state, events, before_state, before_events = stop_targets(
                tmp_path, status="succeeded", index=6, provider=provider
            )
        else:
            result = PersistedExecutionOutcome(
                "persisted_failure", "workflow", "four", 4, "d", "api_error"
            )
            state, events, before_state, before_events = stop_targets(
                tmp_path, status="failed", index=4, provider=provider
            )
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            raise AssertionError("Phase 139 must not be called")

        assert invoke(result, workflow(), None, state, events, fake) is result
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_stop_routes_preserve_empty_predecessor_outputs(
    tmp_path: Path, result_kind: str
) -> None:
    if result_kind == "completion":
        result = WorkflowProgressionDecision(
            "workflow_complete", "workflow", "six", 6, "f", None, None, None,
            "last_step_succeeded",
        )
        state, events, before_state, _ = stop_targets(
            tmp_path, status="succeeded", index=6
        )
        _rewrite_event(events, 1, output_text="")
        _rewrite_event(events, 4, output_text="")
    else:
        result = PersistedExecutionOutcome(
            "persisted_failure", "workflow", "four", 4, "d", "api_error"
        )
        state, events, before_state, _ = stop_targets(
            tmp_path, status="failed", index=4
        )
        _rewrite_event(events, 1, output_text="")
        _rewrite_event(events, 2, output_text="")
    rewritten_events = events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 139 must not be called")

    assert invoke(result, workflow(), None, state, events, fake) is result
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() == rewritten_events


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_stop_result_subclasses_and_substitutes_are_zero_call_rejected(
    tmp_path: Path, result_kind: str
) -> None:
    if result_kind == "completion":
        exact = WorkflowProgressionDecision(
            "workflow_complete", "workflow", "six", 6, "f", None, None, None,
            "last_step_succeeded",
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="succeeded", index=6
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

        assert_rejected(bad, workflow(), None, state, events, "result_type", fake)
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("result_kind", ["completion", "failure"])
def test_stop_routes_reject_non_none_employee_with_zero_calls(
    tmp_path: Path, result_kind: str
) -> None:
    if result_kind == "completion":
        result = WorkflowProgressionDecision(
            "workflow_complete", "workflow", "six", 6, "f", None, None, None,
            "last_step_succeeded",
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="succeeded", index=6
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

    assert_rejected(result, workflow(), employee(), state, events, classification, fake)
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_workflow_complete_empty_success_output_is_rejected_zero_call(
    tmp_path: Path,
) -> None:
    result = WorkflowProgressionDecision(
        "workflow_complete", "workflow", "six", 6, "f", None, None, None,
        "last_step_succeeded",
    )
    state, events, before_state, before_events = stop_targets(
        tmp_path, status="succeeded", index=6, output_text=""
    )
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(result, workflow(), None, state, events, "terminal_contract", fake)
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("result_kind", "value"),
    [
        ("completion", True),
        ("completion", IntChild(6)),
        ("failure", True),
        ("failure", IntChild(4)),
    ],
)
def test_stop_index_requires_exact_builtin_int(
    tmp_path: Path, result_kind: str, value: object
) -> None:
    if result_kind == "completion":
        result = WorkflowProgressionDecision(
            "workflow_complete", "workflow", "six", 6, "f", None, None, None,
            "last_step_succeeded",
        )
        state, events, before_state, before_events = stop_targets(
            tmp_path, status="succeeded", index=6
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

    assert_rejected(result, workflow(), None, state, events, classification, fake)
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_direct_unsupported_inputs_are_zero_call(tmp_path: Path) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    runtime_results = [
        StepRuntimeExecutionSuccess(
            "workflow",
            "six",
            6,
            "f",
            ModelInvocationSuccess(
                "openai", "response", None, "completed", ("output",), "output"
            ),
        ),
        StepRuntimeExecutionFailure(
            "workflow",
            "six",
            6,
            "f",
            ModelInvocationFailure(
                "openai", "api_error", "safe", None, None, None, None
            ),
        ),
    ]
    for bad in [
        RunningStatePersistenceResult(1),
        WorkflowExecutionPersistenceResult(state, events, 1, 1),
        PreparedWorkflowStep(
            "workflow", "six", 6, "f", "employee instructions", "six", "model",
            ("tool-one", "tool-two"),
        ),
        WorkflowExecutionState(
            "workflow", "running", "six", 6, "f",
            ("one", "two", "three", "four", "five"), None,
        ),
        *runtime_results,
    ]:
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(bad, workflow(), employee(), state, events, "result_type", fake)
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("decision", ["prepare_next_step", "not_progressable"])
def test_unsupported_progression_decision_is_zero_call_rejected(
    tmp_path: Path, decision: str
) -> None:
    state, events, before_state, before_events = stop_targets(
        tmp_path, status="succeeded", index=6
    )
    result = WorkflowProgressionDecision(
        decision, "workflow", "six", 6, "f", None, None, None,
        "last_step_succeeded",
    )
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(result, workflow(), None, state, events, "completion_contract", fake)
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("outcome", ["persisted_success", "stopped_failed"])
def test_unsupported_outcome_is_zero_call_rejected(
    tmp_path: Path, outcome: str
) -> None:
    state, events, before_state, before_events = stop_targets(
        tmp_path, status="failed", index=4
    )
    result = PersistedExecutionOutcome(
        outcome, "workflow", "four", 4, "d", "api_error"
    )
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(result, workflow(), None, state, events, "failure_contract", fake)
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_non_callable_dependency_and_targets_are_classified(tmp_path: Path) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        start(), workflow(), employee(), state, events, "persistence_contract", object()
    )
    assert calls == 0
    assert_rejected(
        start(), workflow(), employee(), state, state, "target_conflict", fake
    )
    assert calls == 0
    events.unlink()
    assert_rejected(
        start(), workflow(), employee(), state, events, "event_target", fake
    )
    assert calls == 0
    assert state.read_bytes() == before_state


@pytest.mark.parametrize("field", ["current_step_id", "current_employee_id"])
def test_same_wrong_predecessor_state_and_terminal_event_linkage_is_rejected(
    tmp_path: Path, field: str
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    _rewrite_state(state, **{field: "wrong"})
    _rewrite_event(events, 4, **{field: "wrong"})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        start(), workflow(), employee(), state, events, "terminal_contract", fake
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

    assert_rejected(
        start(), workflow(), employee(), state, events, "terminal_contract", fake
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

    assert_rejected(
        start(), workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() != before_events


def test_immediate_predecessor_non_openai_provider_is_rejected(tmp_path: Path) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    _rewrite_event(events, 3, provider="other")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        start(), workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() != before_events


@pytest.mark.parametrize("provider", ["", 4])
def test_immediate_predecessor_empty_or_non_string_provider_is_rejected(
    tmp_path: Path, provider: object
) -> None:
    state, events, before_state, before_events = predecessor_targets(tmp_path)
    _rewrite_event(events, 3, provider=provider)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        start(), workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert state.read_bytes() == before_state
    assert events.read_bytes() != before_events


def test_earlier_non_openai_provider_remains_accepted(tmp_path: Path) -> None:
    state, events, _, before_events = predecessor_targets(tmp_path)
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
    _rewrite_event(events, 4, **{field: value})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        start(), workflow(), employee(), state, events, "terminal_contract", fake
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
        "six",
        6,
        "f",
        ("one", "two", "three", "four", "five"),
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
def test_target_is_file_oserror_is_classified_before_phase139(
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
    assert_rejected(
        start(), workflow(), employee(), state, events, classification, fake
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["state", "events"])
def test_target_read_bytes_oserror_is_classified_before_phase139(
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
    assert_rejected(
        start(), workflow(), employee(), state, events, classification, fake
    )
    assert calls == 0
    assert (original(state), original(events)) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["state", "events"])
def test_non_regular_target_is_rejected_before_phase139(
    tmp_path: Path, kind: str
) -> None:
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
    assert_rejected(
        start(), workflow(), employee(), state, events, classification, fake
    )
    assert calls == 0
    if kind == "state":
        assert state.is_dir()
        assert events.read_bytes() == before_events
    else:
        assert events.is_dir()
        assert state.read_bytes() == before_state


def test_public_error_detail_contains_only_safe_classification(tmp_path: Path) -> None:
    state, events, *_ = predecessor_targets(tmp_path)

    def fake(*_: object) -> object:
        raise AssertionError("Phase 139 must not be called")

    with pytest.raises(
        PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError
    ) as caught:
        invoke(start(1), workflow(), employee(1), state, events, fake)
    detail = caught.value.detail
    assert detail.classification == "start_contract"
    assert tuple(detail.__dict__.keys()) == ("classification",)
