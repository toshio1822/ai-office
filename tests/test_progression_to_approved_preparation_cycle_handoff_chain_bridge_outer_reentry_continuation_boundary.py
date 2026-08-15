"""Focused tests for the Phase 137 outer progression-preparation bridge."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine import (
    NextStepPreparationApproval,
    PersistedExecutionOutcome,
    PreparedWorkflowStep,
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError,
    WorkflowProgressionDecision,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase137Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeReentryContinuationError as Phase130Error,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary as public_phase130,
)
from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
from ai_office.runtime import (
    RuntimeStepEvent,
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    RunningStatePersistenceResult,
    WorkflowExecutionPersistenceTargets,
    WorkflowExecutionPersistenceResult,
    load_workflow_execution_history,
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


class StartChild(PreparedStepExecutionStart):
    pass


class IntChild(int):
    pass


class TupleChild(tuple):
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


def progression(supplied_workflow: WorkflowDefinition, index: int = 4) -> WorkflowProgressionDecision:
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


def predecessor_event(
    step: WorkflowStepDefinition,
    index: int,
    *,
    provider: object = "other",
    request_id: object = "request",
    response_id: object = "response",
    output_text: object = "output",
) -> RuntimeStepEvent:
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step.id,
        index,
        step.employee,
        "running",
        "succeeded",
        provider,
        None,
        response_id,
        request_id,
        output_text,
        None,
    )


def terminal_event(
    step: WorkflowStepDefinition,
    index: int,
    *,
    status: str = "succeeded",
    provider: object = "openai",
    output_text: object = "output",
    request_id: object = "request",
    response_id: object = "response",
    failure_category: object = None,
    message: object = None,
) -> RuntimeStepEvent:
    failed = status == "failed"
    return RuntimeStepEvent(
        "step_failed" if failed else "step_succeeded",
        "w",
        step.id,
        index,
        step.employee,
        "running",
        status,
        provider,
        "api_error" if failed else failure_category,
        None if failed else response_id,
        request_id,
        None if failed else output_text,
        "safe failure" if failed else message,
    )


def data(
    tmp_path: Path,
    *,
    index: int = 4,
    status: str = "succeeded",
    terminal_provider: object = "openai",
    terminal_output: object = "output",
    terminal_request_id: object = "request",
    terminal_response_id: object = "response",
    predecessor_providers: dict[int, object] | None = None,
) -> dict[str, object]:
    supplied_workflow = workflow()
    step = supplied_workflow.steps[index - 1]
    predecessor_providers = predecessor_providers or {}
    predecessors = tuple(
        predecessor_event(
            prior,
            position,
            provider=predecessor_providers.get(
                position, "openai" if position == index - 1 else "other"
            ),
        )
        for position, prior in enumerate(supplied_workflow.steps[: index - 1], 1)
    )
    state = WorkflowExecutionState(
        "w",
        status,
        step.id,
        index,
        step.employee,
        tuple(item.id for item in supplied_workflow.steps[:index])
        if status == "succeeded"
        else tuple(item.id for item in supplied_workflow.steps[: index - 1]),
        None if status == "succeeded" else "api_error",
    )
    terminal = terminal_event(
        step,
        index,
        status=status,
        provider=terminal_provider,
        output_text=terminal_output,
        request_id=terminal_request_id,
        response_id=terminal_response_id,
    )
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result: object = (
        progression(supplied_workflow, index)
        if status == "succeeded" and index < len(supplied_workflow.steps)
        else PersistedExecutionOutcome(
            "persisted_failure",
            "w",
            step.id,
            index,
            step.employee,
            "api_error",
        )
    )
    return {
        "result": result,
        "workflow": supplied_workflow,
        "approval": approval(result) if type(result) is WorkflowProgressionDecision else None,
        "employee": employee(result) if type(result) is WorkflowProgressionDecision else None,
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }


def completion_data(tmp_path: Path, *, provider: object = "other", output: object = "output") -> dict[str, object]:
    value = data(tmp_path, index=5, terminal_provider=provider, terminal_output=output)
    value["result"] = WorkflowProgressionDecision(
        "workflow_complete", "w", "five", 5, "e", None, None, None, "last_step_succeeded"
    )
    value["approval"] = None
    value["employee"] = None
    return value


def failure_data(tmp_path: Path, *, provider: object = "other") -> dict[str, object]:
    return data(tmp_path, index=1, status="failed", terminal_provider=provider)


def invoke(value: dict[str, object], dependency: object) -> object:
    return route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        value["result"],
        value["workflow"],
        value["approval"],
        value["employee"],
        value["state_path"],
        value["events_path"],
        phase130_function=dependency,
    )


def before(value: dict[str, object]) -> tuple[bytes, bytes]:
    return value["state_path"].read_bytes(), value["events_path"].read_bytes()


def unchanged(value: dict[str, object]) -> None:
    assert before(value) == (value["before_state"], value["before_events"])


def target_snapshot(
    value: dict[str, object],
) -> tuple[tuple[str, bytes | None], tuple[str, bytes | None]]:
    def snapshot(path: Path) -> tuple[str, bytes | None]:
        if path.is_file():
            return "file", path.read_bytes()
        if path.is_dir():
            return "directory", None
        return "missing", None

    return snapshot(value["state_path"]), snapshot(value["events_path"])


def rewrite_state(path: Path, **changes: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    path.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def rewrite_events(path: Path, mutate: object) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    result = mutate(lines)
    path.write_text("\n".join(result) + "\n", encoding="utf-8")


def rewrite_event(path: Path, index: int, **changes: object) -> None:
    def mutate(lines: list[str]) -> list[str]:
        payload = json.loads(lines[index])
        payload.update(changes)
        lines[index] = json.dumps(payload, separators=(",", ":"))
        return lines

    rewrite_events(path, mutate)


def assert_rejected(value: dict[str, object], expected: str, dependency: object) -> None:
    expected_targets = target_snapshot(value)
    calls = 0

    def counted(*_: object) -> object:
        nonlocal calls
        calls += 1
        return dependency(*_) if callable(dependency) else object()

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        invoke(value, counted)
    assert type(caught.value) is ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    assert caught.value.detail.classification == expected
    assert calls == 0
    assert target_snapshot(value) == expected_targets


def patch_loaded_event(
    value: dict[str, object], monkeypatch: pytest.MonkeyPatch, index: int, **changes: object
) -> None:
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(value["state_path"], value["events_path"])
    )
    events = list(loaded.events)
    events[index] = replace(events[index], **changes)

    def fake_loader(_targets: object) -> LoadedWorkflowExecutionHistory:
        return LoadedWorkflowExecutionHistory(loaded.state, tuple(events))

    import ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as phase137_module

    monkeypatch.setattr(phase137_module, "load_workflow_execution_history", fake_loader)


def test_public_signature_default_and_source_audit() -> None:
    function = route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary
    parameters = list(inspect.signature(function).parameters.values())
    assert [item.name for item in parameters] == [
        "result", "workflow", "approval", "employee", "state_path", "events_path", "phase130_function"
    ]
    assert all(item.annotation is object for item in parameters[:6])
    assert [item.kind for item in parameters[:6]] == [inspect.Parameter.POSITIONAL_OR_KEYWORD] * 6
    assert parameters[6].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[6].default is public_phase130
    source = Path(
        "src/ai_office/engine/"
        "progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary" in source
    assert "phase123" not in source.lower()
    assert "phase131" not in source.lower()
    assert "._validate_" not in source
    assert "._top" not in source
    assert "._raise" not in source


def test_prepare_delegates_canonical_six_arguments_once_and_returns_identity(tmp_path: Path) -> None:
    value = data(tmp_path, terminal_output="")
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        return expected

    assert invoke(value, fake) is expected
    assert calls == [
        (
            value["result"], value["workflow"], value["approval"], value["employee"],
            value["state_path"], value["events_path"],
        )
    ]
    unchanged(value)


@pytest.mark.parametrize("index", [1, 2, 3])
def test_prepare_indices_one_two_three_are_zero_call(tmp_path: Path, index: int) -> None:
    value = data(tmp_path, index=index)
    assert_rejected(value, "decision_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("kind", ["completion", "failure"])
def test_stop_routes_are_identity_preserving_zero_call_and_allow_nonopenai(
    tmp_path: Path, kind: str
) -> None:
    value = completion_data(tmp_path) if kind == "completion" else failure_data(tmp_path)
    result = value["result"]
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    assert invoke(value, fake) is result
    assert calls == 0
    unchanged(value)


@pytest.mark.parametrize("route", ["completion", "failure"])
@pytest.mark.parametrize("context", ["approval", "employee"])
def test_stop_routes_reject_non_none_context_before_dependency(
    tmp_path: Path, route: str, context: str
) -> None:
    value = completion_data(tmp_path) if route == "completion" else failure_data(tmp_path)
    context_result = progression(value["workflow"])
    value[context] = (
        approval(context_result)
        if context == "approval"
        else employee(context_result)
    )
    assert_rejected(value, "completion_contract" if route == "completion" else "failure_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("route", ["completion", "failure"])
@pytest.mark.parametrize("kind", ["subclass", "substitute"])
def test_stop_result_subclasses_and_full_substitutes_are_result_type_zero_call(
    tmp_path: Path, route: str, kind: str
) -> None:
    value = completion_data(tmp_path) if route == "completion" else failure_data(tmp_path)
    original = value["result"]
    if route == "completion":
        value["result"] = (
            DecisionChild(*original.__dict__.values())
            if kind == "subclass"
            else SimpleNamespace(**original.__dict__)
        )
    else:
        value["result"] = (
            OutcomeChild(*original.__dict__.values())
            if kind == "subclass"
            else SimpleNamespace(**original.__dict__)
        )
    assert_rejected(value, "result_type", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("route", ["completion", "failure"])
@pytest.mark.parametrize("index", [True, IntChild(5)])
def test_stop_bool_and_int_subclass_indices_are_zero_call(
    tmp_path: Path, route: str, index: int
) -> None:
    value = completion_data(tmp_path) if route == "completion" else failure_data(tmp_path)
    value["result"] = replace(value["result"], current_step_index=index)
    assert_rejected(
        value,
        "completion_contract" if route == "completion" else "failure_contract",
        lambda *_: pytest.fail("called"),
    )


def test_workflow_complete_empty_success_output_is_rejected_zero_call(tmp_path: Path) -> None:
    value = completion_data(tmp_path, output="")
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("kind", ["subclass", "substitute"])
def test_prepare_result_subclass_and_fully_compatible_substitute_are_zero_call(
    tmp_path: Path, kind: str
) -> None:
    value = data(tmp_path)
    original = value["result"]
    value["result"] = (
        DecisionChild(*original.__dict__.values())
        if kind == "subclass"
        else SimpleNamespace(**original.__dict__)
    )
    assert_rejected(value, "result_type", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("kind", ["workflow", "step", "approval", "employee"])
def test_prepare_model_subclasses_are_zero_call(tmp_path: Path, kind: str) -> None:
    value = data(tmp_path)
    if kind == "workflow":
        value["workflow"] = WorkflowChild.model_validate(value["workflow"].model_dump())
    elif kind == "step":
        original = value["workflow"]
        value["workflow"] = WorkflowDefinition.model_construct(
            id=original.id,
            name=original.name,
            description=original.description,
            steps=[
                StepChild.model_validate(step.model_dump()) if pos == 3 else step
                for pos, step in enumerate(original.steps, 1)
            ],
        )
    elif kind == "approval":
        original = value["approval"]
        value["approval"] = ApprovalChild(*original.__dict__.values())
    else:
        original = value["employee"]
        value["employee"] = EmployeeChild.model_validate(original.model_dump())
    expected = "workflow_definition" if kind in {"workflow", "step"} else f"{kind}_contract"
    assert_rejected(value, expected, lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("kind", ["workflow", "step", "approval", "employee"])
def test_prepare_fully_attribute_compatible_substitutes_are_zero_call(
    tmp_path: Path, kind: str
) -> None:
    value = data(tmp_path)
    if kind == "workflow":
        original = value["workflow"]
        value["workflow"] = SimpleNamespace(
            id=original.id,
            name=original.name,
            description=original.description,
            steps=original.steps,
        )
    elif kind == "step":
        original = value["workflow"]
        steps = list(original.steps)
        step = steps[2]
        steps[2] = SimpleNamespace(
            id=step.id, name=step.name, employee=step.employee, instructions=step.instructions
        )
        value["workflow"] = WorkflowDefinition.model_construct(
            id=original.id, name=original.name, description=original.description, steps=steps
        )
    elif kind == "approval":
        original = value["approval"]
        value["approval"] = SimpleNamespace(**original.__dict__)
    else:
        original = value["employee"]
        value["employee"] = SimpleNamespace(**original.__dict__)
    expected = "workflow_definition" if kind in {"workflow", "step"} else f"{kind}_contract"
    assert_rejected(value, expected, lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("bad", [
    PreparedWorkflowStep("w", "five", 5, "e", "employee instructions", "five", "model-name", ("tool-one", "tool-two")),
    PreparedStepExecutionStart(
        SimpleNamespace(model="model-name", system_instructions="employee instructions", task_instructions="five", allowed_tools=("tool-one", "tool-two")),
        WorkflowExecutionState("w", "running", "five", 5, "e", ("one", "two", "three", "four"), None),
    ),
    WorkflowExecutionPersistenceResult(Path("state"), Path("events"), 1, 1),
    RunningStatePersistenceResult(1),
    StepRuntimeExecutionSuccess("w", "five", 5, "e", ModelInvocationSuccess("openai", "response", "request", "completed", ("text",), "text")),
    StepRuntimeExecutionFailure("w", "five", 5, "e", ModelInvocationFailure("openai", "api_error", "safe", "request", None, None, None)),
])
def test_direct_unsupported_inputs_are_result_type_zero_call(tmp_path: Path, bad: object) -> None:
    value = data(tmp_path)
    value["result"] = bad
    assert_rejected(value, "result_type", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("field", ["decision", "workflow_id", "current_step_id", "current_step_index", "current_employee_id", "next_step_id", "next_step_index", "next_employee_id", "reason"])
def test_prepare_decision_fields_are_strict(tmp_path: Path, field: str) -> None:
    value = data(tmp_path)
    result = value["result"]
    changes = {field: True if field.endswith("index") else "wrong"}
    value["result"] = replace(result, **changes)
    assert_rejected(value, "decision_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("field", ["approved", "workflow_id", "current_step_id", "current_step_index", "next_step_id", "next_step_index", "next_employee_id"])
def test_approval_linkage_is_strict(tmp_path: Path, field: str) -> None:
    value = data(tmp_path)
    original = value["approval"]
    value["approval"] = replace(
        original,
        **{
            field: False
            if field == "approved"
            else True
            if field.endswith("index")
            else "wrong"
        },
    )
    assert_rejected(value, "approval_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("field", ["id", "name", "role", "instructions", "model", "allowed_tools"])
def test_employee_contract_is_strict(tmp_path: Path, field: str) -> None:
    value = data(tmp_path)
    original = value["employee"]
    bad = ("tool-one", "wrong-tool") if field == "allowed_tools" else (True if field == "id" else 4)
    value["employee"] = original.model_copy(update={field: bad})
    assert_rejected(value, "employee_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("provider", ["other", "", 4])
def test_immediate_predecessor_provider_is_strict(tmp_path: Path, provider: object) -> None:
    value = data(tmp_path)
    rewrite_event(value["events_path"], 2, provider=provider)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("provider", ["", 4])
def test_earlier_predecessor_provider_requires_nonempty_exact_string(tmp_path: Path, provider: object) -> None:
    value = data(tmp_path)
    rewrite_event(value["events_path"], 0, provider=provider)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


def test_earlier_nonopenai_predecessor_remains_valid(tmp_path: Path) -> None:
    value = data(tmp_path, predecessor_providers={1: "other", 2: "anthropic"})
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1


def test_prepare_index_six_immediate_predecessor_none_request_id_delegates_once(
    tmp_path: Path,
) -> None:
    # Phase-155 provenance: seven-step workflow, step six succeeded, immediate
    # predecessor (step five) has request_id None.  Phase 137 must accept the
    # None request_id on the prepare route when index >= 6 and the event is the
    # immediate predecessor only.
    supplied_workflow = WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{position}",
                    "name": f"Step {position}",
                    "employee": f"e{position}",
                    "instructions": f"step-{position}",
                }
                for position in range(1, 8)
            ],
        }
    )
    index = 6
    step = supplied_workflow.steps[index - 1]
    predecessors = tuple(
        predecessor_event(
            prior,
            position,
            provider="openai" if position == index - 1 else "other",
            request_id=None if position == index - 1 else "request",
        )
        for position, prior in enumerate(supplied_workflow.steps[: index - 1], 1)
    )
    state = WorkflowExecutionState(
        "w",
        "succeeded",
        step.id,
        index,
        step.employee,
        tuple(item.id for item in supplied_workflow.steps[:index]),
        None,
    )
    terminal = terminal_event(step, index)
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = progression(supplied_workflow, index)
    value = {
        "result": result,
        "workflow": supplied_workflow,
        "approval": approval(result),
        "employee": employee(result),
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }
    expected = prepared(supplied_workflow, result, employee(result))
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)

    # --- Review-required narrowness subcases (inline, +0 collected) ---
    # (a) normal non-empty request IDs remain accepted (delegates once).
    rewrite_event(value["events_path"], 4, request_id="request")
    value["before_events"] = value["events_path"].read_bytes()
    calls = 0

    def fake_nonempty(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake_nonempty) is expected
    assert calls == 1
    unchanged(value)

    # (b) empty predecessor output remains accepted on the prepare route
    # (Phase 137 allows empty predecessor output before the stop boundary).
    rewrite_event(value["events_path"], 0, output_text="")
    value["before_events"] = value["events_path"].read_bytes()
    calls = 0

    def fake_empty_output(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake_empty_output) is expected
    assert calls == 1
    unchanged(value)

    # (c) earlier (non-immediate) predecessor request_id=None is rejected: the
    # prepare relaxation is immediate-predecessor-only.
    rewrite_event(value["events_path"], 0, request_id=None)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))

    # Restore the earlier predecessor to its valid baseline before the next
    # subcase so each rejection proves exactly one independent bad condition.
    rewrite_event(value["events_path"], 0, request_id="request")

    # (d) immediate predecessor empty request_id is rejected as the sole bad
    # condition (earlier predecessor back to baseline).
    rewrite_event(value["events_path"], 4, request_id="")
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))

    # Restore the immediate predecessor to its valid baseline.
    rewrite_event(value["events_path"], 4, request_id="request")

    # (e) immediate predecessor invalid request-id type is rejected as the
    # sole bad condition.
    rewrite_event(value["events_path"], 4, request_id=4)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))

    # (f) prepare route at current_step_index=5 still rejects the immediate
    # predecessor request_id=None: the None relaxation requires index >= 6
    # (terminal_contract / zero dependency call / bytes unchanged).
    five_index = 5
    five_step = supplied_workflow.steps[five_index - 1]
    five_predecessors = tuple(
        predecessor_event(
            prior,
            position,
            provider="openai" if position == five_index - 1 else "other",
        )
        for position, prior in enumerate(
            supplied_workflow.steps[: five_index - 1], 1
        )
    )
    five_state = WorkflowExecutionState(
        "w",
        "succeeded",
        five_step.id,
        five_index,
        five_step.employee,
        tuple(item.id for item in supplied_workflow.steps[:five_index]),
        None,
    )
    five_terminal = terminal_event(five_step, five_index)
    five_state_bytes = serialize_workflow_execution_state_json(five_state).encode(
        "utf-8"
    )
    five_event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*five_predecessors, five_terminal)
    )
    five_state_path, five_events_path = (
        tmp_path / "five-state.json",
        tmp_path / "five-events.jsonl",
    )
    five_state_path.write_bytes(five_state_bytes)
    five_events_path.write_bytes(five_event_bytes)
    five_result = progression(supplied_workflow, five_index)
    five_value = {
        "result": five_result,
        "workflow": supplied_workflow,
        "approval": approval(five_result),
        "employee": employee(five_result),
        "state_path": five_state_path,
        "events_path": five_events_path,
        "before_state": five_state_bytes,
        "before_events": five_event_bytes,
    }
    rewrite_event(five_value["events_path"], 3, request_id=None)
    assert_rejected(five_value, "terminal_contract", lambda *_: pytest.fail("called"))


def test_stop_route_rejects_immediate_predecessor_none_request_id_zero_call(
    tmp_path: Path,
) -> None:
    # The prepare-only relaxation must never leak into the stop routes: a
    # workflow_complete result with an immediate predecessor whose request_id
    # is None is rejected before any dependency call.
    value = completion_data(tmp_path)
    rewrite_event(value["events_path"], 3, request_id=None)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))

    # --- Review-required narrowness subcases (inline, +0 collected) ---
    # persisted_failure is equally a stop route: the immediate predecessor
    # request_id=None relaxation must not apply there either.
    failed = data(tmp_path, index=5, status="failed")
    rewrite_event(failed["events_path"], 3, request_id=None)
    assert_rejected(failed, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("field,value_field", [("request_id", ""), ("request_id", 4), ("response_id", ""), ("response_id", 4), ("output_text", 4)])
def test_predecessor_runtime_fields_are_strict(tmp_path: Path, field: str, value_field: object) -> None:
    value = data(tmp_path)
    rewrite_event(value["events_path"], 2, **{field: value_field})
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


def test_immediate_predecessor_empty_output_text_delegates_once_canonical_order(
    tmp_path: Path,
) -> None:
    value = data(tmp_path)
    rewrite_event(value["events_path"], 2, output_text="")
    value["before_events"] = value["events_path"].read_bytes()
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls: list[tuple[object, ...]] = []

    def fake(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        return expected

    assert invoke(value, fake) is expected
    assert calls == [
        (
            value["result"], value["workflow"], value["approval"], value["employee"],
            value["state_path"], value["events_path"],
        )
    ]
    unchanged(value)


def test_earlier_empty_output_text_survives_later_succeeded_predecessors(
    tmp_path: Path,
) -> None:
    value = data(tmp_path)
    rewrite_event(value["events_path"], 0, output_text="")
    value["before_events"] = value["events_path"].read_bytes()
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)


def test_predecessor_nonempty_output_text_remains_accepted(tmp_path: Path) -> None:
    value = data(tmp_path)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        return expected

    assert invoke(value, fake) is expected
    assert calls == 1
    unchanged(value)


def test_completion_stop_route_rejects_empty_predecessor_output_zero_call(
    tmp_path: Path,
) -> None:
    value = completion_data(tmp_path)
    rewrite_event(value["events_path"], 3, output_text="")
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


def test_failure_stop_route_rejects_empty_predecessor_output_zero_call(
    tmp_path: Path,
) -> None:
    value = data(tmp_path, index=4, status="failed")
    rewrite_event(value["events_path"], 2, output_text="")
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("field,value", [("provider", "other"), ("provider", ""), ("provider", 4), ("response_id", ""), ("response_id", 4), ("response_id", None), ("request_id", ""), ("request_id", 4), ("output_text", 4), ("failure_category", "api_error"), ("message", "bad")])
def test_prepare_terminal_event_contract_is_strict(tmp_path: Path, field: str, value: object) -> None:
    value_set = data(tmp_path)
    rewrite_event(value_set["events_path"], 3, **{field: value})
    assert_rejected(value_set, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "wrong"),
        ("step_id", "wrong"),
        ("step_index", True),
        ("employee_id", "wrong"),
        ("employee_instructions", "wrong"),
        ("step_instructions", "wrong"),
        ("model", "wrong"),
        ("allowed_tool_names", ["tool-one", "tool-two"]),
        ("allowed_tool_names", ("tool-one", 4)),
    ],
)
def test_prepared_return_contract_is_strict_after_one_call(
    tmp_path: Path, field: str, value: object
) -> None:
    data_set = data(tmp_path)
    expected = prepared(data_set["workflow"], data_set["result"], data_set["employee"])
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return replace(expected, **{field: value})

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        invoke(data_set, fake)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(data_set)


@pytest.mark.parametrize("kind", ["subclass", "substitute"])
def test_prepared_return_subclass_and_full_substitute_are_rejected_after_one_call(
    tmp_path: Path, kind: str
) -> None:
    data_set = data(tmp_path)
    expected = prepared(data_set["workflow"], data_set["result"], data_set["employee"])
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return (
            PreparedChild(*expected.__dict__.values())
            if kind == "subclass"
            else SimpleNamespace(**expected.__dict__)
        )

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        invoke(data_set, fake)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(data_set)


@pytest.mark.parametrize("bad", [WorkflowProgressionDecision("workflow_complete", "w", "five", 5, "e", None, None, None, "last_step_succeeded"), PersistedExecutionOutcome("persisted_failure", "w", "four", 4, "d", "api_error"), object()])
def test_dependency_return_must_be_exact_prepared_step(tmp_path: Path, bad: object) -> None:
    data_set = data(tmp_path)
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return bad

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        invoke(data_set, fake)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(data_set)


def test_state_and_event_same_wrong_step_still_fail_workflow_linkage(tmp_path: Path) -> None:
    value = data(tmp_path)
    rewrite_state(value["state_path"], current_step_id="wrong", completed_step_ids=["one", "two", "wrong", "four"])
    rewrite_event(value["events_path"], 3, step_id="wrong")
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


def test_state_and_event_same_wrong_employee_still_fail_workflow_linkage(tmp_path: Path) -> None:
    value = data(tmp_path)
    rewrite_state(value["state_path"], current_employee_id="wrong")
    rewrite_event(value["events_path"], 3, employee_id="wrong")
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "failed", "completed_step_ids": ["one", "two", "three"], "last_failure_category": "api_error"},
        {"completed_step_ids": ["one", "two", "wrong", "four"]},
        {"last_failure_category": "api_error"},
        {"current_step_index": True},
        {"current_step_index": 5, "current_step_id": "five", "current_employee_id": "e", "completed_step_ids": ["one", "two", "three", "four", "five"]},
    ],
)
def test_persisted_terminal_state_contract_is_strict(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    value = data(tmp_path)
    if changes.get("status") == "failed":
        rewrite_event(
            value["events_path"],
            3,
            event_type="step_failed",
            next_status="failed",
            failure_category="api_error",
            response_id=None,
            output_text=None,
            message="safe failure",
        )
    if changes.get("current_step_index") == 5:
        rewrite_event(
            value["events_path"],
            3,
            step_id="five",
            step_index=5,
            employee_id="e",
        )
    rewrite_state(value["state_path"], **changes)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("mode", ["duplicate", "missing", "reordered", "unrelated", "malformed", "extra"])
def test_history_matrix_is_rejected_before_phase130(tmp_path: Path, mode: str) -> None:
    value = data(tmp_path)

    def mutate(lines: list[str]) -> list[str]:
        if mode == "duplicate":
            return [lines[0], lines[0], *lines[1:]]
        if mode == "missing":
            return lines[1:]
        if mode == "reordered":
            return [lines[1], lines[0], *lines[2:]]
        if mode == "unrelated":
            payload = json.loads(lines[0])
            payload["workflow_id"] = "other"
            return [*lines[:-1], json.dumps(payload, separators=(",", ":")), lines[-1]]
        if mode == "malformed":
            return ["{", *lines[1:]]
        return [*lines, lines[-1]]

    rewrite_events(value["events_path"], mutate)
    assert_rejected(value, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("kind", ["state", "events", "both"])
def test_normal_dependency_mutation_is_compensated_without_retry(tmp_path: Path, kind: str) -> None:
    value = data(tmp_path)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        if kind in {"state", "both"}:
            value["state_path"].write_bytes(b"mutated")
        if kind in {"events", "both"}:
            value["events_path"].write_bytes(b"mutated\n")
        return expected

    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("kind", ["malformed", "substitute"])
@pytest.mark.parametrize("mutation", ["state", "events", "both"])
def test_malformed_dependency_return_and_mutation_are_compensated(
    tmp_path: Path, kind: str, mutation: str
) -> None:
    value = data(tmp_path)

    def fake(*_: object) -> object:
        if mutation in {"state", "both"}:
            value["state_path"].write_bytes(b"mutated")
        if mutation in {"events", "both"}:
            value["events_path"].write_bytes(b"mutated\n")
        if kind == "substitute":
            return SimpleNamespace(**prepared(value["workflow"], value["result"], value["employee"]).__dict__)
        return object()

    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "prepared_contract"
    unchanged(value)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_safe_phase130_error_identity_is_preserved_after_compensation(tmp_path: Path, mutation: str) -> None:
    value = data(tmp_path)
    safe_error = Phase130Error("safe")
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        if mutation in {"state", "both"}:
            value["state_path"].write_bytes(b"mutated")
        if mutation in {"events", "both"}:
            value["events_path"].write_bytes(b"mutated\n")
        raise safe_error

    with pytest.raises(Phase130Error) as caught:
        invoke(value, fake)
    assert caught.value is safe_error
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("mutation", ["unchanged", "state", "events", "both"])
def test_unexpected_phase130_error_is_sanitized_and_compensated(tmp_path: Path, mutation: str) -> None:
    value = data(tmp_path)

    def fake(*_: object) -> object:
        if mutation in {"state", "both"}:
            value["state_path"].write_bytes(b"mutated")
        if mutation in {"events", "both"}:
            value["events_path"].write_bytes(b"mutated\n")
        raise RuntimeError("secret detail")

    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret detail" not in str(caught.value)
    unchanged(value)


@pytest.mark.parametrize("failed", [{"state"}, {"events"}, {"state", "events"}])
def test_rollback_failure_attempts_both_targets_once_without_retry(
    tmp_path: Path, failed: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    value = data(tmp_path)
    expected = prepared(value["workflow"], value["result"], value["employee"])
    calls = 0
    writes: dict[Path, int] = {value["state_path"]: 0, value["events_path"]: 0}
    original_write = Path.write_bytes

    def write(path: Path, contents: bytes) -> int:
        writes[path] += 1
        if path.stem in failed and writes[path] >= 2:
            raise OSError("rollback")
        return original_write(path, contents)

    monkeypatch.setattr(Path, "write_bytes", write)

    def fake(*_: object) -> PreparedWorkflowStep:
        nonlocal calls
        calls += 1
        value["state_path"].write_bytes(b"mutated")
        value["events_path"].write_bytes(b"mutated\n")
        return expected

    with pytest.raises(ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError) as caught:
        invoke(value, fake)
    assert caught.value.detail.classification == "dependency_rollback"
    assert calls == 1
    assert writes[value["state_path"]] == 2
    assert writes[value["events_path"]] == 2


@pytest.mark.parametrize("target", ["state_path", "events_path"])
@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_missing_and_nonregular_targets_are_zero_call(tmp_path: Path, target: str, kind: str) -> None:
    value = data(tmp_path)
    path = value[target]
    if kind == "missing":
        path.unlink()
    else:
        path.unlink()
        path.mkdir()
    assert_rejected(value, "state_target" if target == "state_path" else "event_target", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("target", ["state_path", "events_path"])
@pytest.mark.parametrize("operation", ["is_file", "read_bytes"])
def test_target_oserrors_are_separately_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    operation: str,
) -> None:
    value = data(tmp_path)
    selected = value[target]
    original = getattr(Path, operation)
    original_state = Path.read_bytes(value["state_path"])
    original_events = Path.read_bytes(value["events_path"])

    def fail(path: Path, *args: object, **kwargs: object) -> object:
        if path == selected:
            raise OSError("target-secret")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, fail)
    calls = 0
    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        invoke(value, lambda *_: pytest.fail("called"))
    assert caught.value.detail.classification == (
        "state_target" if target == "state_path" else "event_target"
    )
    assert calls == 0
    monkeypatch.setattr(Path, operation, original)
    assert Path.read_bytes(value["state_path"]) == original_state
    assert Path.read_bytes(value["events_path"]) == original_events


def test_target_conflict_and_noncallable_dependency_are_zero_call(tmp_path: Path) -> None:
    value = data(tmp_path)
    value["events_path"] = value["state_path"]
    assert_rejected(value, "target_conflict", lambda *_: pytest.fail("called"))
    value = data(tmp_path)
    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            value["result"],
            value["workflow"],
            value["approval"],
            value["employee"],
            value["state_path"],
            value["events_path"],
            phase130_function=object(),
        )
    assert caught.value.detail.classification == "dependency_error"
    unchanged(value)


def test_direct_phase130_safe_error_type_is_not_exposed_in_public_message() -> None:
    error = ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError("dependency_error")
    assert str(error) == "progression to approved preparation cycle handoff chain bridge outer inputs are incompatible"
    assert error.detail.classification == "dependency_error"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "wrong"),
        ("step_id", "wrong"),
        ("step_index", 4),
        ("employee_id", "wrong"),
        ("event_type", "step_failed"),
        ("previous_status", "succeeded"),
        ("next_status", "failed"),
        ("failure_category", "api_error"),
        ("message", "bad"),
    ],
)
def test_predecessor_provenance_contract_matrix_is_zero_call(
    tmp_path: Path, field: str, value: object
) -> None:
    data_set = data(tmp_path)
    rewrite_event(data_set["events_path"], 2, **{field: value})
    assert_rejected(data_set, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("replacement", [True, IntChild(3)])
def test_predecessor_step_index_requires_exact_builtin_int_zero_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: object,
) -> None:
    data_set = data(tmp_path)
    if type(replacement) is bool:
        rewrite_event(data_set["events_path"], 2, step_index=replacement)
    else:
        patch_loaded_event(data_set, monkeypatch, 2, step_index=replacement)
    assert_rejected(data_set, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow_id", "wrong"),
        ("step_id", "wrong"),
        ("step_index", 5),
        ("employee_id", "wrong"),
        ("event_type", "step_failed"),
        ("previous_status", "succeeded"),
        ("next_status", "failed"),
    ],
)
def test_current_terminal_event_contract_matrix_is_zero_call(
    tmp_path: Path, field: str, value: object
) -> None:
    data_set = data(tmp_path)
    rewrite_event(data_set["events_path"], 3, **{field: value})
    assert_rejected(data_set, "terminal_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize("replacement", [True, IntChild(4)])
def test_current_terminal_step_index_requires_exact_builtin_int_zero_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: object,
) -> None:
    data_set = data(tmp_path)
    if type(replacement) is bool:
        rewrite_event(data_set["events_path"], 3, step_index=replacement)
    else:
        patch_loaded_event(data_set, monkeypatch, 3, step_index=replacement)
    assert_rejected(data_set, "terminal_contract", lambda *_: pytest.fail("called"))


def test_prepare_employee_exact_string_value_mismatch_is_zero_call(tmp_path: Path) -> None:
    data_set = data(tmp_path)
    supplied_employee = data_set["employee"]
    data_set["employee"] = supplied_employee.model_copy(update={"id": "wrong"})
    assert type(data_set["employee"].id) is str
    assert data_set["employee"].id != data_set["result"].next_employee_id
    assert_rejected(data_set, "employee_contract", lambda *_: pytest.fail("called"))


@pytest.mark.parametrize(
    "allowed_tool_names",
    [TupleChild(("tool-one", "tool-two")), ("tool-one", "wrong-tool")],
)
def test_prepared_return_allowed_tool_names_requires_exact_tuple_and_values(
    tmp_path: Path, allowed_tool_names: object
) -> None:
    data_set = data(tmp_path)
    expected = prepared(data_set["workflow"], data_set["result"], data_set["employee"])
    calls = 0

    def fake(*_: object) -> object:
        nonlocal calls
        calls += 1
        return replace(expected, allowed_tool_names=allowed_tool_names)

    with pytest.raises(
        ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError
    ) as caught:
        invoke(data_set, fake)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(data_set)
