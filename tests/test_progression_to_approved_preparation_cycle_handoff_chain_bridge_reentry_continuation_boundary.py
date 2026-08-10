"""Focused tests for the Phase 130 progression-preparation bridge boundary."""

# ruff: noqa: E501,E701,E702

import inspect
import json
from dataclasses import replace
from pathlib import Path
from pathlib import Path as _TestPath
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError,
    WorkflowProgressionDecision,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary,
    route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainReentryContinuationError as Phase123Error,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class DecisionChild(WorkflowProgressionDecision):
    pass


class OutcomeChild(PersistedExecutionOutcome):
    pass


class WorkflowChild(WorkflowDefinition):
    pass


class StepChild(WorkflowStepDefinition):
    pass


class ApprovalChild(NextStepPreparationApproval):
    pass


class EmployeeChild(EmployeeDefinition):
    pass


class PreparedChild(PreparedWorkflowStep):
    pass


class PathChild(type(Path())):
    pass


class IntChild(int):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "two"},
                {"id": "three", "name": "Three", "employee": "c", "instructions": "three"},
                {"id": "four", "name": "Four", "employee": "d", "instructions": "four"},
                {"id": "five", "name": "Five", "employee": "e", "instructions": "five"},
            ],
        }
    )


def progression(supplied_workflow: WorkflowDefinition, index: int = 3) -> WorkflowProgressionDecision:
    current = supplied_workflow.steps[index - 1]
    following = supplied_workflow.steps[index]
    return WorkflowProgressionDecision(
        "prepare_next_step",
        supplied_workflow.id,
        current.id,
        index,
        current.employee,
        following.id,
        index + 1,
        following.employee,
        "next_step_available",
    )


def approval(value: WorkflowProgressionDecision) -> NextStepPreparationApproval:
    return NextStepPreparationApproval(
        True,
        value.workflow_id,
        value.current_step_id,
        value.current_step_index,
        value.next_step_id,
        value.next_step_index,
        value.next_employee_id,
    )


def employee(value: WorkflowProgressionDecision) -> EmployeeDefinition:
    return EmployeeDefinition.model_validate(
        {
            "id": value.next_employee_id,
            "name": "Next Employee",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def prepared(
    supplied_workflow: WorkflowDefinition,
    value: WorkflowProgressionDecision,
    supplied_employee: EmployeeDefinition,
) -> PreparedWorkflowStep:
    step = supplied_workflow.steps[value.next_step_index - 1]
    return PreparedWorkflowStep(
        supplied_workflow.id,
        step.id,
        value.next_step_index,
        supplied_employee.id,
        supplied_employee.instructions,
        step.instructions,
        supplied_employee.model,
        tuple(supplied_employee.allowed_tools),
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


def predecessor_event(step_id: str, index: int, employee_id: str) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        index,
        employee_id,
        "running",
        "succeeded",
        "openai",
        None,
        f"response-{step_id}",
        f"request-{step_id}",
        f"output-{step_id}",
        None,
    )


def setup(
    tmp_path: Path,
    *,
    status: str = "succeeded",
    index: int = 3,
    provider: object = "openai",
) -> tuple[
    WorkflowProgressionDecision,
    WorkflowDefinition,
    NextStepPreparationApproval,
    EmployeeDefinition,
    Path,
    Path,
    bytes,
    bytes,
]:
    supplied_workflow = workflow()
    current = supplied_workflow.steps[index - 1]
    completed = (
        tuple(step.id for step in supplied_workflow.steps[:index])
        if status == "succeeded"
        else tuple(step.id for step in supplied_workflow.steps[: index - 1])
    )
    state_model = WorkflowExecutionState(
        supplied_workflow.id,
        status,
        current.id,
        index,
        current.employee,
        completed,
        None if status == "succeeded" else "api_error",
    )
    prior = tuple(
        predecessor_event(step.id, position, step.employee)
        for position, step in enumerate(supplied_workflow.steps[: index - 1], 1)
    )
    events = (*prior, terminal_event(supplied_workflow, index, status, provider))
    state_bytes = serialize_workflow_execution_state_json(state_model).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    decision = progression(
        supplied_workflow,
        index if index < len(supplied_workflow.steps) else index - 1,
    )
    return (
        decision,
        supplied_workflow,
        approval(decision),
        employee(decision),
        state_path,
        events_path,
        state_bytes,
        event_bytes,
    )


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


def failure(supplied_workflow: WorkflowDefinition, index: int = 3) -> PersistedExecutionOutcome:
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
    supplied_approval: object,
    supplied_employee: object,
    state: object,
    events: object,
    dependency: object,
) -> object:
    return route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary(
        result,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        phase123_function=dependency,
    )


def classification(
    caught: pytest.ExceptionInfo[
        ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ],
) -> str:
    return caught.value.detail.classification


def assert_rejected(
    result: object,
    supplied_workflow: object,
    supplied_approval: object,
    supplied_employee: object,
    state: object,
    events: object,
    expected: str,
    dependency: object,
) -> None:
    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        invoke(
            result,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            dependency,
        )
    assert classification(caught) == expected


def rewrite_event(events: Path, index: int, **changes: object) -> None:
    lines = events.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[index])
    payload.update(changes)
    lines[index] = json.dumps(payload, separators=(",", ":"))
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_public_signature_default_and_canonical_identity(tmp_path: Path) -> None:
    signature = inspect.signature(
        route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary
    )
    parameters = list(signature.parameters.values())
    assert [parameter.name for parameter in parameters] == [
        "result",
        "workflow",
        "approval",
        "employee",
        "state_path",
        "events_path",
        "phase123_function",
    ]
    assert all(parameter.annotation is object for parameter in parameters[:6])
    assert [parameter.kind for parameter in parameters[:6]] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD
    ] * 6
    assert parameters[6].kind is inspect.Parameter.KEYWORD_ONLY
    assert (
        parameters[6].default
        is route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary
    )

    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    value = prepared(supplied_workflow, decision, supplied_employee)
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        return value

    assert (
        invoke(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            fake,
        )
        is value
    )
    assert calls == [
        (
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
        )
    ]


def test_source_audit_uses_only_public_phase123_dependency() -> None:
    source = Path(
        "src/ai_office/engine/"
        "progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert (
        "route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary"
        in source
    )
    assert "phase116" not in source.lower()
    assert (
        "route_progression_to_approved_preparation_cycle_handoff_reentry_continuation_boundary"
        not in source
    )
    assert "route_progression_to_approved_preparation_cycle_reentry_continuation_boundary" not in source
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


@pytest.mark.parametrize("index", [3, 4], ids=["current-three", "current-four"])
def test_prepare_route_delegates_once_and_returns_exact_prepared_identity(
    tmp_path: Path, index: int
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, before_state, before_events = setup(
        tmp_path, index=index
    )
    value = prepared(supplied_workflow, decision, supplied_employee)
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return value

    assert (
        invoke(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            fake,
        )
        is value
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("index", [1, 2], ids=["index-one", "index-two"])
def test_prepare_current_index_below_three_is_rejected_before_phase123(
    tmp_path: Path, index: int
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path, index=index
    )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "decision_contract",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize("route", ["complete", "failure"])
def test_stop_routes_are_identity_preserving_zero_call_stops_without_stricter_provider(
    tmp_path: Path, route: str
) -> None:
    supplied_workflow = workflow()
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("Phase 123 must not be called")

    if route == "complete":
        result = completion(supplied_workflow)
        _, _, _, _, state, events, before_state, before_events = setup(
            tmp_path, index=len(supplied_workflow.steps), provider="other"
        )
    else:
        result = failure(supplied_workflow)
        _, _, _, _, state, events, before_state, before_events = setup(
            tmp_path, status="failed", provider="other"
        )
    assert invoke(result, supplied_workflow, None, None, state, events, forbidden) is result
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    "bad",
    [
        object(),
        PreparedWorkflowStep("w", "four", 4, "d", "employee instructions", "four", "model-name", ("tool-one", "tool-two")),
        WorkflowExecutionPersistenceResult(Path("state"), Path("events"), 1, 1),
        PersistedExecutionOutcome("persisted_success", "w", "three", 3, "c", None),
        SimpleNamespace(
            decision="prepare_next_step",
            workflow_id="w",
            current_step_id="three",
            current_step_index=3,
            current_employee_id="c",
            next_step_id="four",
            next_step_index=4,
            next_employee_id="d",
            reason="next_step_available",
        ),
    ],
)
def test_only_exact_result_models_are_accepted_before_phase123(
    tmp_path: Path, bad: object
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    expected = "failure_contract" if isinstance(bad, PersistedExecutionOutcome) else "result_type"
    assert_rejected(
        bad,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        expected,
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize("kind", ["result", "workflow", "approval", "employee"])
def test_subclass_models_are_rejected_before_phase123(tmp_path: Path, kind: str) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    values: dict[str, object] = {
        "result": DecisionChild(*decision.__dict__.values()),
        "workflow": WorkflowChild.model_validate(supplied_workflow.model_dump()),
        "approval": ApprovalChild(*supplied_approval.__dict__.values()),
        "employee": EmployeeChild.model_validate(supplied_employee.model_dump()),
    }
    supplied: dict[str, object] = {
        "result": decision,
        "workflow": supplied_workflow,
        "approval": supplied_approval,
        "employee": supplied_employee,
    }
    supplied[kind] = values[kind]
    expected = "result_type" if kind == "result" else (
        "workflow_definition" if kind == "workflow" else f"{kind}_contract"
    )
    assert_rejected(
        supplied["result"],
        supplied["workflow"],
        supplied["approval"],
        supplied["employee"],
        state,
        events,
        expected,
        lambda *_: pytest.fail("called"),
    )


@pytest.mark.parametrize("substitute", ["subclass", "attribute-compatible"])
def test_persisted_failure_subclasses_and_substitutes_are_rejected_before_phase123(
    tmp_path: Path, substitute: str
) -> None:
    supplied_workflow = workflow()
    result = failure(supplied_workflow)
    if substitute == "subclass":
        supplied_result: object = OutcomeChild(*result.__dict__.values())
    else:
        supplied_result = SimpleNamespace(**result.__dict__)
    _, _, _, _, state, events, *_ = setup(tmp_path, status="failed")
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        supplied_result,
        supplied_workflow,
        None,
        None,
        state,
        events,
        "result_type",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize("kind", ["result", "workflow", "approval", "employee"])
def test_attribute_compatible_substitutes_are_rejected_before_phase123(
    tmp_path: Path, kind: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    values: dict[str, object] = {
        "result": decision,
        "workflow": supplied_workflow,
        "approval": supplied_approval,
        "employee": supplied_employee,
    }
    original = values[kind]
    substitute = SimpleNamespace(**original.__dict__)
    values[kind] = substitute
    expected = "result_type" if kind == "result" else (
        "workflow_definition" if kind == "workflow" else f"{kind}_contract"
    )
    assert_rejected(
        values["result"],
        values["workflow"],
        values["approval"],
        values["employee"],
        state,
        events,
        expected,
        lambda *_: pytest.fail("called"),
    )


def test_workflow_step_attribute_substitute_is_rejected_before_phase123(
    tmp_path: Path,
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    substitute = SimpleNamespace(
        id="one", name="One", employee="a", instructions="one"
    )
    supplied = supplied_workflow.model_copy(
        update={"steps": [substitute, *supplied_workflow.steps[1:]]}
    )
    assert type(supplied) is WorkflowDefinition
    assert_rejected(
        decision,
        supplied,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "workflow_definition",
        lambda *_: pytest.fail("called"),
    )


def test_workflow_step_subclass_is_rejected_before_phase123(tmp_path: Path) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    original_step = supplied_workflow.steps[0]
    subclass_step = StepChild.model_validate(original_step.model_dump())
    supplied = supplied_workflow.model_copy(
        update={"steps": [subclass_step, *supplied_workflow.steps[1:]]}
    )
    assert type(supplied) is WorkflowDefinition
    assert type(supplied.steps[0]) is StepChild
    assert supplied.steps[0].model_dump() == original_step.model_dump()
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        decision,
        supplied,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "workflow_definition",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "unsupported"),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_step_index", IntChild(3)),
        ("current_step_index", 2),
        ("current_employee_id", "other"),
        ("next_step_id", "other"),
        ("next_step_index", 5),
        ("next_step_index", True),
        ("next_step_index", IntChild(4)),
        ("next_employee_id", "other"),
        ("reason", "wrong"),
    ],
)
def test_prepare_decision_contract_matrix(
    tmp_path: Path, field: str, value: object
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    bad = replace(decision, **{field: value})
    assert_rejected(
        bad,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "decision_contract",
        lambda *_: pytest.fail("called"),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approved", False),
        ("approved", 1),
        ("workflow_id", "other"),
        ("current_step_id", "other"),
        ("current_step_index", True),
        ("current_step_index", IntChild(3)),
        ("next_step_id", "other"),
        ("next_step_index", True),
        ("next_step_index", IntChild(4)),
        ("next_employee_id", "other"),
    ],
)
def test_approval_contract_matrix(tmp_path: Path, field: str, value: object) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    bad = replace(supplied_approval, **{field: value})
    assert_rejected(
        decision,
        supplied_workflow,
        bad,
        supplied_employee,
        state,
        events,
        "approval_contract",
        lambda *_: pytest.fail("called"),
    )


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
def test_employee_contract_matrix(tmp_path: Path, field: str, value: object) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    bad = supplied_employee.model_copy(update={field: value})
    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        bad,
        state,
        events,
        "employee_contract",
        lambda *_: pytest.fail("called"),
    )


@pytest.mark.parametrize("provider", ["other", 4])
def test_prepare_terminal_provider_mismatch_is_rejected_before_phase123(
    tmp_path: Path, provider: object
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path, provider=provider
    )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "terminal_contract",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize(
    "mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"]
)
def test_terminal_history_matrix_is_rejected_before_phase123(
    tmp_path: Path, mode: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    lines = events.read_text(encoding="utf-8").splitlines()
    if mode == "duplicate":
        lines = [lines[0], lines[0], *lines[1:]]
    elif mode == "missing":
        lines = lines[:-1]
    elif mode == "reordered":
        lines = [lines[1], lines[0], *lines[2:]]
    elif mode == "unrelated":
        payload = json.loads(lines[0])
        payload["workflow_id"] = "other"
        lines[0] = json.dumps(payload, separators=(",", ":"))
    elif mode == "malformed":
        events.write_bytes(b"{not-json}\n")
        lines = []
    elif mode == "extra":
        lines = [*lines, lines[-1]]
    if mode != "malformed":
        events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
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
def test_prepare_terminal_event_linkage_matrix(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    rewrite_event(events, 2, **changes)
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "terminal_contract",
        forbidden,
    )
    assert calls == 0


def test_persisted_terminal_state_mismatch_is_rejected_before_phase123(
    tmp_path: Path,
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    bad_state = WorkflowExecutionState(
        "w", "succeeded", "two", 2, "b", ("one", "two"), None
    )
    state.write_bytes(serialize_workflow_execution_state_json(bad_state).encode("utf-8"))
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
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
        ("current_step_index", IntChild(3)),
        ("current_employee_id", "other"),
        ("next_step_id", "other"),
        ("next_step_index", 5),
        ("next_employee_id", "other"),
        ("reason", "wrong"),
    ],
)
def test_workflow_complete_stop_contract_matrix(
    tmp_path: Path, field: str, value: object
) -> None:
    supplied_workflow = workflow()
    result = replace(completion(supplied_workflow), **{field: value})
    _, _, _, _, state, events, *_ = setup(tmp_path, index=len(supplied_workflow.steps))
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        None,
        None,
        state,
        events,
        "approval_contract" if field == "decision" else "completion_contract",
        forbidden,
    )
    assert calls == 0


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
    result = replace(failure(supplied_workflow), **{field: value})
    _, _, _, _, state, events, *_ = setup(tmp_path, status="failed")
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        None,
        None,
        state,
        events,
        "failure_contract",
        forbidden,
    )
    assert calls == 0


def test_stop_routes_reject_non_none_approval_or_employee_before_phase123(
    tmp_path: Path,
) -> None:
    supplied_workflow = workflow()
    result = completion(supplied_workflow)
    _, _, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path, index=len(supplied_workflow.steps)
    )
    for extra_approval, extra_employee in (
        (supplied_approval, None),
        (None, supplied_employee),
        (supplied_approval, supplied_employee),
    ):
        calls = 0

        def forbidden(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(
            result,
            supplied_workflow,
            extra_approval,
            extra_employee,
            state,
            events,
            "completion_contract",
            forbidden,
        )
        assert calls == 0


def test_persisted_failure_stop_rejects_non_none_approval_or_employee_before_phase123(
    tmp_path: Path,
) -> None:
    supplied_workflow = workflow()
    result = failure(supplied_workflow)
    _, _, supplied_approval, supplied_employee, state, events, before_state, before_events = setup(
        tmp_path, status="failed"
    )
    for extra_approval, extra_employee in (
        (supplied_approval, None),
        (None, supplied_employee),
        (supplied_approval, supplied_employee),
    ):
        calls = 0

        def forbidden(*_: object) -> object:
            nonlocal calls
            calls += 1
            return object()

        assert_rejected(
            result,
            supplied_workflow,
            extra_approval,
            extra_employee,
            state,
            events,
            "failure_contract",
            forbidden,
        )
        assert calls == 0
        assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("bad", [object(), SimpleNamespace(decision="prepare_next_step")])
def test_unsupported_progression_is_rejected_before_phase123(
    tmp_path: Path, bad: object
) -> None:
    supplied_workflow = workflow()
    _, _, supplied_approval, supplied_employee, state, events, *_ = setup(tmp_path)
    if isinstance(bad, WorkflowProgressionDecision):
        result = bad
    else:
        result = WorkflowProgressionDecision(
            "stopped_failed", "w", "three", 3, "c", None, None, None, "latest_step_failed"
        )
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert_rejected(
        result,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "decision_contract",
        forbidden,
    )
    assert calls == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "other"),
        ("step_id", "other"),
        ("step_index", IntChild(4)),
        ("step_index", True),
        ("employee_id", "other"),
        ("employee_instructions", 4),
        ("step_instructions", 4),
        ("model", 4),
        ("allowed_tool_names", ["tool-one", "tool-two"]),
        ("allowed_tool_names", ("tool-one", "other")),
    ],
)
def test_prepared_return_contract_matrix(
    tmp_path: Path, field: str, value: object
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    bad = replace(
        prepared(supplied_workflow, decision, supplied_employee), **{field: value}
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "prepared_contract",
        dependency,
    )
    assert calls == 1


def test_prepared_subclass_and_attribute_compatible_substitute_are_rejected(
    tmp_path: Path,
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    exact = prepared(supplied_workflow, decision, supplied_employee)
    values = [
        PreparedChild(*exact.__dict__.values()),
        SimpleNamespace(**exact.__dict__),
    ]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return values.pop(0)

    for _ in range(2):
        assert_rejected(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            "prepared_contract",
            dependency,
        )
    assert calls == 2


@pytest.mark.parametrize("return_kind", ["decision", "failure", "object"])
def test_unsupported_phase123_return_is_rejected_after_one_call(
    tmp_path: Path, return_kind: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    returned: object = {
        "decision": decision,
        "failure": failure(supplied_workflow),
        "object": object(),
    }[return_kind]
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return returned

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "prepared_contract",
        dependency,
    )
    assert calls == 1


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_dependency_return_target_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, before_state, before_events = setup(
        tmp_path
    )
    exact = prepared(supplied_workflow, decision, supplied_employee)
    malformed = SimpleNamespace(**exact.__dict__)
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        return malformed

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "prepared_contract",
        dependency,
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_normal_dependency_target_mutation_is_compensated_without_retry(
    tmp_path: Path, mutation: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, before_state, before_events = setup(
        tmp_path
    )
    value = prepared(supplied_workflow, decision, supplied_employee)
    calls = 0

    def dependency(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        return value

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "prepared_contract",
        dependency,
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("employee_instructions", "wrong"),
        ("step_instructions", "wrong"),
        ("model", "wrong"),
    ],
)
def test_prepared_return_semantic_value_mismatch_is_rejected_after_one_call(
    tmp_path: Path, field: str, value: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    bad = replace(
        prepared(supplied_workflow, decision, supplied_employee), **{field: value}
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "prepared_contract",
        dependency,
    )
    assert calls == 1


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_phase123_error_identity_and_compensation(
    tmp_path: Path, mutation: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, before_state, before_events = setup(
        tmp_path
    )
    safe_error = Phase123Error("safe detail")
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        raise safe_error

    with pytest.raises(Phase123Error) as caught:
        invoke(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            dependency,
        )
    assert caught.value is safe_error
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_phase123_error_is_sanitized_and_compensated(
    tmp_path: Path, mutation: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, before_state, before_events = setup(
        tmp_path
    )
    calls = 0

    def dependency(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            state.write_bytes(b"mutated-state")
        if mutation in {"events", "both"}:
            events.write_bytes(b"mutated-events")
        raise RuntimeError("secret detail")

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        invoke(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            dependency,
        )
    assert classification(caught) == "dependency_error"
    assert "secret detail" not in str(caught.value)
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("failed_targets", [{"state"}, {"events"}, {"state", "events"}])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_targets: set[str],
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, before_state, before_events = setup(
        tmp_path
    )
    original_write = _TestPath.write_bytes
    attempts: list[str] = []
    armed = False
    calls = 0

    def write(path: Path, data: bytes) -> int:
        nonlocal armed
        if armed and data == before_state:
            attempts.append("state")
            if "state" in failed_targets:
                raise OSError("state rollback secret")
        if armed and data == before_events:
            attempts.append("events")
            if "events" in failed_targets:
                raise OSError("events rollback secret")
        return original_write(path, data)

    monkeypatch.setattr(_TestPath, "write_bytes", write)

    def dependency(*_: object) -> object:
        nonlocal calls, armed
        calls += 1
        original_write(state, b"changed-state")
        original_write(events, b"changed-events")
        armed = True
        return object()

    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "dependency_rollback",
        dependency,
    )
    assert calls == 1
    assert attempts.count("state") == 1
    assert attempts.count("events") == 1
    assert "rollback secret" not in str(attempts)


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_non_regular_targets_are_rejected_before_phase123(
    tmp_path: Path, target: str, kind: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    selected = state if target == "state" else events
    selected.unlink()
    if kind == "directory":
        selected.mkdir()
    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "state_target" if target == "state" else "event_target",
        lambda *_: pytest.fail("called"),
    )


@pytest.mark.parametrize("target", ["state", "events"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_target_oserrors_are_separately_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    operation: str,
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    selected = state if target == "state" else events
    original = getattr(_TestPath, operation)

    def fail(path: Path, *args: object, **kwargs: object):
        if path == selected:
            raise OSError("target-secret")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(_TestPath, operation, fail)
    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "state_target" if target == "state" else "event_target",
        lambda *_: pytest.fail("called"),
    )


def test_target_conflict_and_noncallable_dependency_are_zero_call(
    tmp_path: Path,
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        state,
        "target_conflict",
        lambda *_: pytest.fail("called"),
    )
    assert_rejected(
        decision,
        supplied_workflow,
        supplied_approval,
        supplied_employee,
        state,
        events,
        "dependency_error",
        None,
    )


def test_prepare_route_accepts_exact_empty_success_output_without_mutation(
    tmp_path: Path,
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    rewrite_event(events, 2, output_text="")
    rewrite_event(events, 0, provider="legacy-provider", request_id="legacy-request")
    before_state, before_events = state.read_bytes(), events.read_bytes()
    expected = prepared(supplied_workflow, decision, supplied_employee)
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert (
        invoke(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            fake,
        )
        is expected
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("field", ["current_step_id", "current_employee_id"])
def test_empty_success_fallback_preserves_workflow_current_linkage(
    tmp_path: Path, field: str
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    rewrite_event(events, 2, output_text="", **{field: "wrong"})
    state_payload = json.loads(state.read_text(encoding="utf-8"))
    state_payload[field] = "wrong"
    if field == "current_step_id":
        state_payload["completed_step_ids"][-1] = "wrong"
    state.write_text(json.dumps(state_payload, separators=(",", ":")) + "\n", encoding="utf-8")
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        invoke(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            fake,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("provider", ["other", 4])
def test_empty_success_fallback_still_rejects_terminal_provider(
    tmp_path: Path, provider: object
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    rewrite_event(events, 2, output_text="", provider=provider)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        invoke(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            fake,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


@pytest.mark.parametrize("field,value", [("response_id", ""), ("response_id", None), ("response_id", 4), ("request_id", ""), ("request_id", 4)])
def test_empty_success_fallback_preserves_terminal_response_and_request_contract(
    tmp_path: Path, field: str, value: object
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    rewrite_event(events, 2, output_text="", **{field: value})
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        invoke(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            fake,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_workflow_complete_empty_success_output_is_rejected_zero_call(
    tmp_path: Path,
) -> None:
    supplied_workflow = workflow()
    _, _, _, _, state, events, *_ = setup(
        tmp_path, index=len(supplied_workflow.steps)
    )
    result = completion(supplied_workflow)
    rewrite_event(events, len(supplied_workflow.steps) - 1, output_text="")
    before_state, before_events = state.read_bytes(), events.read_bytes()
    calls = 0

    def forbidden(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationCompatibilityError
    ) as caught:
        invoke(
            result,
            supplied_workflow,
            None,
            None,
            state,
            events,
            forbidden,
        )
    assert classification(caught) == "terminal_contract"
    assert calls == 0
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)


def test_empty_success_fallback_accepts_optional_none_request_id(
    tmp_path: Path,
) -> None:
    decision, supplied_workflow, supplied_approval, supplied_employee, state, events, *_ = setup(
        tmp_path
    )
    rewrite_event(events, 2, output_text="", request_id=None)
    before_state, before_events = state.read_bytes(), events.read_bytes()
    expected = prepared(supplied_workflow, decision, supplied_employee)
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert (
        invoke(
            decision,
            supplied_workflow,
            supplied_approval,
            supplied_employee,
            state,
            events,
            fake,
        )
        is expected
    )
    assert calls == 1
    assert (state.read_bytes(), events.read_bytes()) == (before_state, before_events)
