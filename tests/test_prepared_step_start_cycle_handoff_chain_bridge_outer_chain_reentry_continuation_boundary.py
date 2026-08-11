"""Focused fake-only tests for the Phase 146 outer-chain prepared-start bridge."""

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
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as public_route,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError as Phase138Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as phase138_public,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.runtime.step_runtime_execution import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
)
from ai_office.storage import (
    RunningStatePersistenceResult,
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


class TupleChild(tuple):
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
                {"id": "six", "name": "Six", "employee": "f", "instructions": "six instructions"},
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
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def prepared(
    index: int = 6, supplied_workflow: WorkflowDefinition | None = None
) -> PreparedWorkflowStep:
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
    supplied_workflow: WorkflowDefinition, index: int, provider: object = "openai"
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
        provider,
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
        f"request-{step.id}" if status == "succeeded" else None,
        f"output-{step.id}" if status == "succeeded" else None,
        None if status == "succeeded" else "safe failure",
    )
    return replace(event, **changes)


def targets(
    tmp_path: Path,
    *,
    status: str = "succeeded",
    index: int = 5,
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
    supplied_workflow: WorkflowDefinition, index: int = 4
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
    return public_route(
        result,
        supplied_workflow,
        supplied_employee,
        state,
        events,
        phase138_function=dependency,
    )


def reject(callable_object, expected: str) -> None:
    with pytest.raises(
        PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError
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


def rewrite_state(state: Path, **changes: object) -> None:
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload.update(changes)
    state.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def rewrite_event(events: Path, index: int, **changes: object) -> None:
    lines = events.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[index])
    payload.update(changes)
    lines[index] = json.dumps(payload, separators=(",", ":"))
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_public_signature_default_and_source_audit() -> None:
    parameters = tuple(inspect.signature(public_route).parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "result",
        "workflow",
        "employee",
        "state_path",
        "events_path",
        "phase138_function",
    )
    assert all(parameter.annotation is object for parameter in parameters[:5])
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in parameters[:5]
    )
    assert parameters[5].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[5].default is phase138_public
    source = Path(
        "src/ai_office/engine/"
        "prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert (
        "route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary"
        in source
    )
    assert "phase131" not in source.lower()
    assert (
        "route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary"
        not in source
    )
    assert (
        "route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary"
        not in source
    )
    assert (
        "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary"
        not in source
    )
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


def test_valid_route_uses_canonical_identity_once_and_returns_exact_start(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    returned = start_for(value)
    observed: list[object] = []
    calls = 0

    def fake(*arguments: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        observed.extend(arguments)
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1
    assert observed == [value, workflow(), employee(), state, events]
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_empty_immediate_predecessor_output_is_accepted(tmp_path: Path) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 3, output_text="")
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1


def test_empty_earlier_predecessor_output_is_accepted(tmp_path: Path) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 0, output_text="")
    rewrite_event(events, 2, output_text="")
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1


def test_prepared_indices_one_to_five_are_zero_call_rejections(
    tmp_path: Path,
) -> None:
    for index in (1, 2, 3, 4, 5):
        value = prepared(index)
        state, events, *_ = targets(tmp_path, index=index - 1)
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(
            value, workflow(), employee(index), state, events, "prepared_step_contract", fake
        )
        assert calls == 0


@pytest.mark.parametrize("kind", ["result", "workflow", "employee", "step"])
def test_subclass_inputs_are_rejected_before_phase138(
    tmp_path: Path, kind: str
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    supplied_workflow: object = workflow()
    supplied_employee: object = employee()
    if kind == "result":
        result: object = PreparedChild(*value.__dict__.values())
        expected = "result_type"
    elif kind == "workflow":
        result = value
        supplied_workflow = WorkflowChild.model_validate(workflow().model_dump())
        expected = "workflow_definition"
    elif kind == "employee":
        result = value
        supplied_employee = EmployeeChild.model_validate(employee().model_dump())
        expected = "employee_contract"
    else:
        result = value
        model = workflow().model_dump()
        model["steps"][0] = StepChild.model_validate(model["steps"][0])
        supplied_workflow = WorkflowDefinition.model_validate(model)
        expected = "workflow_definition"
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        supplied_employee,
        state,
        events,
        expected,
        fake,
    )
    assert calls == 0


@pytest.mark.parametrize("kind", ["result", "workflow", "employee"])
def test_attribute_compatible_inputs_are_rejected_before_phase138(
    tmp_path: Path, kind: str
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    supplied_workflow: object = workflow()
    supplied_employee: object = employee()
    if kind == "result":
        result: object = SimpleNamespace(**value.__dict__)
        expected = "result_type"
    elif kind == "workflow":
        result = value
        supplied_workflow = SimpleNamespace(**workflow().__dict__)
        expected = "workflow_definition"
    else:
        result = value
        supplied_employee = SimpleNamespace(**employee().__dict__)
        expected = "employee_contract"
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        supplied_employee,
        state,
        events,
        expected,
        fake,
    )
    assert calls == 0


def test_workflow_step_subclass_and_attribute_substitute_are_zero_call_rejections(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    for model in (
        WorkflowDefinition.model_validate(
            {
                "id": "workflow",
                "name": "Workflow",
                "description": "test workflow",
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

        assert_rejected(
            value, model, employee(), state, events, "workflow_definition", fake
        )
        assert calls == 0


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("workflow_id", "other"),
        ("step_id", "other"),
        ("step_index", True),
        ("step_index", IntChild(6)),
        ("employee_id", "other"),
        ("employee_instructions", "other"),
        ("step_instructions", "other"),
        ("model", "other"),
        ("allowed_tool_names", ["tool-one", "tool-two"]),
        ("allowed_tool_names", TupleChild(("tool-one", "tool-two"))),
        ("allowed_tool_names", ("tool-one", 4)),
        ("allowed_tool_names", ("tool-one", "wrong-tool")),
    ],
)
def test_prepared_step_exact_contract_is_checked_before_phase138(
    tmp_path: Path, field: str, bad: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    changed = replace(value, **{field: bad})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        changed,
        workflow(),
        employee(),
        state,
        events,
        "employee_contract" if field == "employee_id" else "prepared_step_contract",
        fake,
    )
    assert calls == 0


def test_employee_linkage_contract_is_checked_before_phase138(tmp_path: Path) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    for person, expected in (
        (employee(5), "employee_contract"),
        (
            EmployeeDefinition.model_validate(
                {
                    "id": "f",
                    "name": "Six",
                    "role": "role",
                    "instructions": "other instructions",
                    "model": "model-name",
                    "allowed_tools": ["tool-one", "tool-two"],
                }
            ),
            "prepared_step_contract",
        ),
        (
            EmployeeDefinition.model_validate(
                {
                    "id": "f",
                    "name": "Six",
                    "role": "role",
                    "instructions": "employee instructions",
                    "model": "other-model",
                    "allowed_tools": ["tool-one", "tool-two"],
                }
            ),
            "prepared_step_contract",
        ),
        (
            EmployeeDefinition.model_validate(
                {
                    "id": "f",
                    "name": "Six",
                    "role": "role",
                    "instructions": "employee instructions",
                    "model": "model-name",
                    "allowed_tools": ["tool-one"],
                }
            ),
            "prepared_step_contract",
        ),
    ):
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(value, workflow(), person, state, events, expected, fake)
        assert calls == 0


def test_predecessor_state_and_event_same_wrong_linkage_still_rejected(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_state(state, workflow_id="other")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0


def test_predecessor_state_and_all_history_events_same_wrong_workflow_are_rejected(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_state(state, workflow_id="other")
    for index in range(5):
        rewrite_event(events, index, workflow_id="other")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0


def test_immediate_predecessor_provider_is_openai_exact(tmp_path: Path) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 3, provider="other")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0


def test_earlier_non_openai_predecessor_remains_valid(tmp_path: Path) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 0, provider="other")
    rewrite_event(events, 1, provider="anthropic")
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1


def test_immediate_predecessor_openai_is_accepted_exactly(tmp_path: Path) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 3, provider="openai")
    returned = start_for(value)
    calls = 0

    def fake(*_: object) -> PreparedStepExecutionStart:
        nonlocal calls
        calls += 1
        return returned

    assert invoke(value, workflow(), employee(), state, events, fake) is returned
    assert calls == 1


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("workflow_id", "other"),
        ("step_id", "other"),
        ("step_index", True),
        ("step_index", 4),
        ("employee_id", "other"),
        ("event_type", "step_failed"),
        ("previous_status", "ready"),
        ("next_status", "failed"),
        ("failure_category", "api_error"),
        ("message", "bad"),
    ],
)
def test_earlier_predecessor_linkage_and_status_are_zero_call_rejections(
    tmp_path: Path, field: str, bad: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 0, **{field: bad})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0


@pytest.mark.parametrize("bad", ["", 4])
def test_earlier_predecessor_provider_must_be_nonempty_string(
    tmp_path: Path, bad: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 0, provider=bad)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("response_id", ""),
        ("response_id", 4),
        ("request_id", ""),
        ("request_id", 4),
        ("output_text", 4),
    ],
)
def test_earlier_predecessor_request_response_output_are_zero_call_rejections(
    tmp_path: Path, field: str, bad: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 0, **{field: bad})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0


@pytest.mark.parametrize(
    "field",
    ["failure_category", "message"],
)
def test_earlier_predecessor_failure_and_message_are_zero_call_rejections(
    tmp_path: Path, field: str
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(
        events,
        0,
        **{field: "api_error" if field == "failure_category" else "bad"},
    )
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "mutation",
    ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"],
)
def test_predecessor_history_matrix_is_zero_call(
    tmp_path: Path, mutation: str
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    lines = events.read_text(encoding="utf-8").splitlines()
    if mutation == "duplicate":
        lines.insert(1, lines[0])
    elif mutation == "missing":
        lines.pop(1)
    elif mutation == "reordered":
        lines[0], lines[1] = lines[1], lines[0]
    elif mutation == "unrelated":
        payload = json.loads(lines[0])
        payload.update(step_id="unrelated", workflow_id="other")
        lines.insert(1, json.dumps(payload, separators=(",", ":")))
    elif mutation == "malformed":
        lines[1] = "{"
    else:
        lines.append(lines[-1])
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("status", "failed"),
        ("current_step_index", True),
        ("current_step_index", 4),
        ("completed_step_ids", ["one", "two", "three", "four", "wrong"]),
        ("completed_step_ids", ["one", "two", "three", "four"]),
        ("last_failure_category", "api_error"),
    ],
)
def test_predecessor_state_contract_matrix_is_zero_call(
    tmp_path: Path, field: str, bad: object
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_state(state, **{field: bad})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0


@pytest.mark.parametrize(
    "field",
    [
        "workflow_id",
        "step_id",
        "step_index",
        "employee_id",
        "event_type",
        "previous_status",
        "next_status",
        "failure_category",
        "message",
    ],
)
def test_terminal_event_contract_matrix_is_zero_call(
    tmp_path: Path, field: str
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    bad = {
        "workflow_id": "other",
        "step_id": "other",
        "step_index": 4,
        "employee_id": "other",
        "event_type": "step_failed",
        "previous_status": "ready",
        "next_status": "failed",
        "failure_category": "api_error",
        "message": "bad",
    }[field]
    rewrite_event(events, 4, **{field: bad})
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "bad"),
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
        ("output_text", None),
        ("failure_category", "api_error"),
        ("message", "bad"),
    ],
)
def test_terminal_provider_identifiers_output_and_forbidden_fields_are_zero_call(
    tmp_path: Path, field: str, bad: object
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    rewrite_event(events, 4, **{field: bad})
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value, workflow(), employee(), state, events, "terminal_contract", fake
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_terminal_request_id_none_is_valid_and_identity_preserving(
    tmp_path: Path,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    rewrite_event(events, 4, request_id=None)
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


@pytest.mark.parametrize(
    "bad",
    [
        object(),
        PreparedStepExecutionStart(
            ModelInvocationRequest("model", "system", "task", ("tool",)),
            WorkflowExecutionState("workflow", "running", "six", 6, "f", (), None),
        ),
        SimpleNamespace(request="x", running_state="y"),
    ],
)
def test_direct_unsupported_exact_results_are_zero_call(
    tmp_path: Path, bad: object
) -> None:
    state, events, before_state, before_events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        bad,
        workflow(),
        employee(),
        state,
        events,
        "result_type",
        fake,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("runtime_result", [
    StepRuntimeExecutionSuccess("workflow", "six", 6, "f", SimpleNamespace()),
    StepRuntimeExecutionFailure("workflow", "six", 6, "f", SimpleNamespace()),
])
def test_direct_runtime_results_are_zero_call(
    tmp_path: Path, runtime_result: object
) -> None:
    state, events, before_state, before_events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        runtime_result,
        workflow(),
        employee(),
        state,
        events,
        "result_type",
        fake,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_direct_running_state_persistence_result_is_zero_call(tmp_path: Path) -> None:
    state, events, before_state, before_events = targets(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        RunningStatePersistenceResult(len(before_state)),
        workflow(),
        employee(),
        state,
        events,
        "result_type",
        fake,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_valid_return_mutation_is_compensated_without_retry(
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

    assert_rejected(
        value, workflow(), employee(), state, events, "start_contract", fake
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_error_identity_is_preserved_after_compensation(
    tmp_path: Path, mutation: str
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    safe_error = Phase138Error("safe detail")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        raise safe_error

    with pytest.raises(Phase138Error) as caught:
        invoke(value, workflow(), employee(), state, events, fake)
    assert caught.value is safe_error
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_error_is_sanitized_and_compensated(
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
        PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError
    ) as caught:
        invoke(value, workflow(), employee(), state, events, fake)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("failed_target", ["state", "events", "both"])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_target: str,
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
            if failed_target in {"state", "both"}:
                raise OSError("state rollback")
        if armed and data == before_events and path == events:
            attempts.append("events")
            if failed_target in {"events", "both"}:
                raise OSError("events rollback")
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
        value, workflow(), employee(), state, events, "dependency_rollback", fake
    )
    assert calls == 1
    assert attempts.count("state") == 1
    assert attempts.count("events") == 1


def test_stop_routes_are_identity_preserving_zero_call_stops(tmp_path: Path) -> None:
    for result, status, index in (
        (completion(workflow()), "succeeded", 6),
        (failure(workflow()), "failed", 4),
    ):
        case_dir = tmp_path / result.__class__.__name__
        case_dir.mkdir()
        state, events, before_state, before_events = targets(
            case_dir, status=status, index=index, provider="other"
        )
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert invoke(result, workflow(), None, state, events, fake) is result
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_stop_routes_preserve_empty_predecessor_output(tmp_path: Path) -> None:
    result = completion(workflow())
    state, events, before_state, before_events = targets(tmp_path, index=6)
    rewrite_event(events, 3, output_text="")
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert invoke(result, workflow(), None, state, events, fake) is result
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_workflow_complete_empty_success_output_is_rejected_zero_call(
    tmp_path: Path,
) -> None:
    state, events, before_state, before_events = targets(tmp_path, index=6)
    rewrite_event(events, 5, output_text="")
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


@pytest.mark.parametrize("context", ["employee", "workflow"])
def test_stop_routes_reject_non_none_employee_with_zero_call(
    tmp_path: Path, context: str
) -> None:
    result = completion(workflow()) if context == "workflow" else failure(workflow())
    index = 6 if context == "workflow" else 4
    status = "succeeded" if context == "workflow" else "failed"
    state, events, before_state, before_events = targets(tmp_path, status=status, index=index)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        workflow(),
        employee(),
        state,
        events,
        "completion_contract" if context == "workflow" else "failure_contract",
        fake,
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["start", "request", "state"])
def test_phase138_return_exact_nested_models_are_required(
    tmp_path: Path, kind: str
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    exact = start_for(value)
    request = exact.request
    running = exact.running_state
    returned: object = {
        "start": StartChild(request, running),
        "request": PreparedStepExecutionStart(
            RequestChild(
                request.model,
                request.system_instructions,
                request.task_instructions,
                request.allowed_tools,
            ),
            running,
        ),
        "state": PreparedStepExecutionStart(
            request,
            StateChild(*running.__dict__.values()),
        ),
    }[kind]
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    assert_rejected(
        value, workflow(), employee(), state, events, "start_contract", fake
    )
    assert calls == 1


def test_phase138_return_fully_compatible_substitute_is_rejected(tmp_path: Path) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    exact = start_for(value)
    returned = SimpleNamespace(request=exact.request, running_state=exact.running_state)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    assert_rejected(
        value, workflow(), employee(), state, events, "start_contract", fake
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("nested", ["request", "state"])
def test_phase138_nested_fully_compatible_substitutes_are_rejected(
    tmp_path: Path, nested: str
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    exact = start_for(value)
    returned = PreparedStepExecutionStart(
        SimpleNamespace(**exact.request.__dict__)
        if nested == "request"
        else exact.request,
        SimpleNamespace(**exact.running_state.__dict__)
        if nested == "state"
        else exact.running_state,
    )
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    assert_rejected(
        value, workflow(), employee(), state, events, "start_contract", fake
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("nested", "field", "bad"),
    [
        ("request", "model", "wrong-model"),
        ("request", "system_instructions", "wrong-system"),
        ("request", "task_instructions", "wrong-task"),
        ("request", "allowed_tools", ["tool-one", "tool-two"]),
        ("request", "allowed_tools", TupleChild(("tool-one", "tool-two"))),
        ("request", "allowed_tools", ("tool-one", 4)),
        ("request", "allowed_tools", ("tool-one", "wrong-tool")),
        ("running", "workflow_id", "other"),
        ("running", "current_step_id", "other"),
        ("running", "current_step_index", True),
        ("running", "current_step_index", IntChild(6)),
        ("running", "current_step_index", 5),
        ("running", "current_employee_id", "other"),
        ("running", "status", "succeeded"),
        ("running", "completed_step_ids", ("one", "two", "three", "four", "wrong")),
        ("running", "completed_step_ids", ["one", "two", "three", "four", "five"]),
        ("running", "last_failure_category", "api_error"),
    ],
)
def test_phase138_return_semantic_linkage_and_exact_fields_are_rejected(
    tmp_path: Path, nested: str, field: str, bad: object
) -> None:
    value = prepared()
    state, events, before_state, before_events = targets(tmp_path)
    exact = start_for(value)
    if nested == "request":
        returned = replace(exact, request=replace(exact.request, **{field: bad}))
    else:
        returned = replace(
            exact, running_state=replace(exact.running_state, **{field: bad})
        )
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    assert_rejected(
        value, workflow(), employee(), state, events, "start_contract", fake
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("kind", ["workflow", "failure"])
@pytest.mark.parametrize("field_value", [True, IntChild(6)])
def test_stop_current_step_index_requires_exact_builtin_int(
    tmp_path: Path, kind: str, field_value: object
) -> None:
    if kind == "workflow":
        result = replace(completion(workflow()), current_step_index=field_value)
        expected = "completion_contract"
        state, events, before_state, before_events = targets(tmp_path, index=6)
    else:
        result = replace(failure(workflow()), current_step_index=field_value)
        expected = "failure_contract"
        state, events, before_state, before_events = targets(tmp_path, status="failed", index=4)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result, workflow(), None, state, events, expected, fake
    )
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_stop_result_subclasses_and_substitutes_are_zero_call_rejections(
    tmp_path: Path,
) -> None:
    supplied_workflow = workflow()
    decision = completion(supplied_workflow)
    outcome = failure(supplied_workflow)
    values = [
        DecisionChild(*decision.__dict__.values()),
        OutcomeChild(*outcome.__dict__.values()),
        SimpleNamespace(**decision.__dict__),
        SimpleNamespace(**outcome.__dict__),
    ]
    for value in values:
        index = 6 if hasattr(value, "decision") else 4
        status = "succeeded" if index == 6 else "failed"
        case_dir = tmp_path / str(len(list(tmp_path.iterdir())))
        case_dir.mkdir()
        state, events, *_ = targets(case_dir, status=status, index=index)
        expected = "result_type"
        calls = 0

        def fake(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(value, supplied_workflow, None, state, events, expected, fake)
        assert calls == 0


def test_path_and_dependency_contracts_are_rejected_before_phase138(tmp_path: Path) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    assert_rejected(
        value,
        workflow(),
        employee(),
        PathChild(state),
        events,
        "state_target",
        lambda *_: pytest.fail("Phase 138 was called"),
    )
    assert_rejected(
        value,
        workflow(),
        employee(),
        state,
        events,
        "dependency_error",
        None,
    )


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_non_regular_targets_are_zero_call_rejections(
    tmp_path: Path, target: str, kind: str
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    selected = state if target == "state" else events
    selected.unlink()
    if kind == "directory":
        selected.mkdir()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value,
        workflow(),
        employee(),
        state,
        events,
        "state_target" if target == "state" else "event_target",
        fake,
    )
    assert calls == 0


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_state_and_event_target_oserrors_are_zero_call_rejections(
    tmp_path: Path,
    target: str,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = prepared()
    state, events, *_ = targets(tmp_path)
    selected = state if target == "state" else events
    original = getattr(_TestPath, operation)

    def fail(path: Path, *args: object, **kwargs: object):
        if path == selected:
            raise OSError("target detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(_TestPath, operation, fail)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        value,
        workflow(),
        employee(),
        state,
        events,
        "state_target" if target == "state" else "event_target",
        fake,
    )
    assert calls == 0
