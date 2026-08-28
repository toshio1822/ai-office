"""Focused Phase-192 bounded explicit-context runner tests."""

# ruff: noqa: E501,E702,F401,I001

from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, astuple, replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.approved_workflow_continuation_cycle import (
    ApprovedWorkflowContinuationCycleError as Phase190BaseError,
    ApprovedWorkflowContinuationCycleCompatibilityError as Phase190Error,
    route_approved_workflow_continuation_cycle as phase190,
)
from ai_office.engine.bounded_approved_workflow_runner import (
    ApprovedWorkflowContinuationContext,
    BoundedApprovedWorkflowRunnerCompatibilityError as RunnerError,
    BoundedApprovedWorkflowRunnerError,
    route_bounded_approved_workflow_continuation,
)
from ai_office.engine.next_step_preparation import NextStepPreparationApproval
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.prepared_step_execution_start import PreparedStepExecutionStart
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationRequest,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155Error,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
)


def workflow(count: int = 4) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "Workflow",
            "description": "Synthetic workflow",
            "steps": [
                {
                    "id": f"step-{i}",
                    "name": f"Step {i}",
                    "employee": f"e{i}",
                    "instructions": f"instructions-{i}",
                }
                for i in range(1, count + 1)
            ],
        }
    )


def preparation(wf: WorkflowDefinition, current: int) -> WorkflowProgressionDecision:
    step = wf.steps[current - 1]
    next_step = wf.steps[current]
    return WorkflowProgressionDecision(
        "prepare_next_step", wf.id, step.id, current, step.employee,
        next_step.id, current + 1, next_step.employee, "next_step_available",
    )


def complete(wf: WorkflowDefinition) -> WorkflowProgressionDecision:
    step = wf.steps[-1]
    return WorkflowProgressionDecision(
        "workflow_complete", wf.id, step.id, len(wf.steps), step.employee,
        None, None, None, "last_step_succeeded",
    )


def failure(wf: WorkflowDefinition, index: int = 2) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure", wf.id, step.id, index, step.employee, "api_error"
    )


def context(tag: str) -> ApprovedWorkflowContinuationContext:
    return ApprovedWorkflowContinuationContext(
        f"preparation-{tag}", f"employee-{tag}", f"tools-{tag}",
        f"key-{tag}", f"execution-{tag}", f"transport-{tag}",
    )


def targets(tmp_path: Path) -> tuple[Path, Path]:
    state = tmp_path / "state.json"
    events = tmp_path / "events.jsonl"
    state.write_bytes(b"state")
    events.write_bytes(b"events")
    return state, events


def employee(wf: WorkflowDefinition, index: int) -> EmployeeDefinition:
    step = wf.steps[index - 1]
    return EmployeeDefinition(
        id=step.employee,
        name=f"Employee {index}",
        role="worker",
        instructions=f"employee-instructions-{index}",
        model="model",
        allowed_tools=[],
    )


def preparation_approval(
    wf: WorkflowDefinition, index: int
) -> NextStepPreparationApproval:
    prior = preparation(wf, index - 1)
    return NextStepPreparationApproval(
        True,
        prior.workflow_id,
        prior.current_step_id,
        prior.current_step_index,
        prior.next_step_id,
        prior.next_step_index,
        prior.next_employee_id,
    )


def _history_event(wf: WorkflowDefinition, index: int, **changes: object) -> RuntimeStepEvent:
    step = wf.steps[index - 1]
    return replace(
        RuntimeStepEvent(
            "step_succeeded", wf.id, step.id, index, step.employee,
            "running", "succeeded", "openai", None,
            f"response-{step.id}", f"request-{step.id}", f"output-{step.id}", None,
        ),
        **changes,  # type: ignore[arg-type]
    )


def real_setup(tmp_path: Path, *, current: int = 9, count: int = 11) -> dict[str, object]:
    wf = workflow(count)
    state = WorkflowExecutionState(
        wf.id, "succeeded", wf.steps[current - 1].id, current,
        wf.steps[current - 1].employee,
        tuple(step.id for step in wf.steps[:current]), None,
    )
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode())
    events = []
    for index in range(1, current + 1):
        if index == current:
            events.append(_history_event(wf, index, output_text="output"))
        elif index == current - 1:
            events.append(_history_event(wf, index, output_text="", request_id=None))
        elif index in (2, 3, 4):
            events.append(_history_event(wf, index, output_text=""))
        else:
            events.append(_history_event(wf, index))
    events_path.write_bytes(
        b"".join(serialize_runtime_step_event_jsonl(event).encode() for event in events)
    )
    return {"workflow": wf, "state_path": state_path, "events_path": events_path}


def real_context(
    wf: WorkflowDefinition, index: int, *, bad_execution_approval: bool = False
) -> ApprovedWorkflowContinuationContext:
    emp = employee(wf, index)
    prepared_request = ModelInvocationRequest(
        emp.model, emp.instructions, wf.steps[index - 1].instructions, ()
    )
    approval = approve_model_invocation_execution(
        prepared_request, (), provider="openai", approved_by="reviewer", approval_id="approval-1"
    )
    return ApprovedWorkflowContinuationContext(
        preparation_approval(wf, index),
        emp,
        (),
        OpenAIApiKey(value="test-key"),
        object() if bad_execution_approval else approval,
        None,
    )


def synthetic_transport(calls: list[object], *, status: int = 200):
    def transport(request: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(request)
        if status == 500:
            return OpenAIResponsesRawHttpResponse(
                500, "synthetic error", (("x-request-id", "request_123"),),
                b'{"error":{"message":"safe failure","type":"server_error","param":null,"code":null}}',
            )
        return OpenAIResponsesRawHttpResponse(
            200, "synthetic", (("x-request-id", "request_123"),),
            b'{"id":"resp_123","object":"response","status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}]}',
        )
    return transport


def call_args(
    result: object, wf: WorkflowDefinition, state: Path, events: Path, contexts: tuple[ApprovedWorkflowContinuationContext, ...]
) -> tuple[object, ...]:
    return result, wf, state, events, contexts


def err(call, classification: str) -> None:
    with pytest.raises(RunnerError) as caught:
        call()
    assert caught.value.detail.classification == classification
    assert "secret" not in str(caught.value)


def test_01_public_api_context_is_frozen_and_bounded_source_audit() -> None:
    signature = inspect.signature(route_bounded_approved_workflow_continuation)
    assert list(signature.parameters)[:5] == ["result", "workflow", "state_path", "events_path", "contexts"]
    assert signature.parameters["phase190_function"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["phase190_function"].default is phase190
    assert issubclass(RunnerError, BoundedApprovedWorkflowRunnerError)
    value = context("x")
    with pytest.raises(FrozenInstanceError):
        value.employee = "mutated"  # type: ignore[misc]
    source = Path(route_bounded_approved_workflow_continuation.__code__.co_filename).read_text()
    tree = ast.parse(source)
    assert not any(isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree))
    assert source.count("phase190_function(") == 1
    assert "route_approved_workflow_continuation_cycle(" not in source
    for forbidden in ("approve_model_invocation", "OpenAIApiKey", "create", "lookup", "scheduler"):
        assert forbidden not in source


def test_02_two_explicit_contexts_call_phase190_once_each_in_order(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    values = real_setup(tmp_path, current=9, count=11); wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    calls: list[object] = []
    contexts_value = tuple(
        ApprovedWorkflowContinuationContext(
            item.preparation_approval, item.employee, item.resolved_tools,
            item.api_key, item.execution_approval,
            synthetic_transport(calls),
        )
        for item in (real_context(wf, 10), real_context(wf, 11))
    )
    result = route_bounded_approved_workflow_continuation(
        preparation(wf, 9), wf, values["state_path"], values["events_path"], contexts_value
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete" and result.current_step_index == 11
    assert len(calls) == 2
    assert load_workflow_execution_state(values["state_path"]).completed_step_ids == tuple(
        step.id for step in wf.steps
    )
    assert len(values["events_path"].read_text().splitlines()) == 11


def test_03_tuple_length_is_exact_bound_and_exhaustion_preserves_identity(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    values = real_setup(tmp_path, current=9, count=11); wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    calls: list[object] = []
    supplied = real_context(wf, 10)
    supplied = ApprovedWorkflowContinuationContext(
        supplied.preparation_approval, supplied.employee, supplied.resolved_tools,
        supplied.api_key, supplied.execution_approval, synthetic_transport(calls),
    )
    initial = preparation(wf, 9)
    result = route_bounded_approved_workflow_continuation(
        initial, wf, values["state_path"], values["events_path"], (supplied,)
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.current_step_index == 10 and result.next_step_index == 11
    assert len(calls) == 1
    assert load_workflow_execution_state(values["state_path"]).current_step_index == 10
    assert len(values["events_path"].read_text().splitlines()) == 10


def test_04_empty_tuple_returns_prepare_identity_without_dependency_or_target_inspection(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); first = preparation(wf, 2); calls = []
    before = (state.read_bytes(), events.read_bytes())
    result = route_bounded_approved_workflow_continuation(first, wf, state, events, (), phase190_function=lambda *args: calls.append(args))
    assert result is first and calls == [] and (state.read_bytes(), events.read_bytes()) == before


def test_05_terminal_inputs_ignore_operational_arguments_and_contexts(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); calls = []
    for stop in (complete(wf), failure(wf)):
        result = route_bounded_approved_workflow_continuation(stop, wf, object(), object(), [object()], phase190_function=None)
        assert result is stop
    assert calls == []


def test_06_terminal_validation_rejects_malformed_terminal_objects_before_operational_inputs() -> None:
    wf = workflow(); bad = replace(complete(wf), current_step_id="wrong")
    missing = object.__new__(WorkflowProgressionDecision)
    missing_failure = object.__new__(PersistedExecutionOutcome)
    err(lambda: route_bounded_approved_workflow_continuation(bad, wf, object(), object(), object()), "result_type")
    err(lambda: route_bounded_approved_workflow_continuation(missing, wf, object(), object(), object()), "result_type")
    err(lambda: route_bounded_approved_workflow_continuation(missing_failure, wf, object(), object(), object()), "result_type")


def test_07_invalid_initial_result_workflow_and_targets_are_rejected(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); first = preparation(wf, 2)
    malformed_workflow = object.__new__(WorkflowDefinition)
    malformed_result = object.__new__(WorkflowProgressionDecision)
    err(lambda: route_bounded_approved_workflow_continuation(malformed_result, wf, state, events, ()), "result_type")
    err(lambda: route_bounded_approved_workflow_continuation(object(), wf, state, events, ()), "result_type")
    err(lambda: route_bounded_approved_workflow_continuation(first, malformed_workflow, state, events, ()), "workflow_definition")
    err(lambda: route_bounded_approved_workflow_continuation(first, object(), state, events, ()), "workflow_definition")
    err(lambda: route_bounded_approved_workflow_continuation(first, wf, object(), events, ()), "state_target")
    err(lambda: route_bounded_approved_workflow_continuation(first, wf, state, object(), ()), "event_target")


def test_08_context_container_must_be_exact_tuple(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); first = preparation(wf, 2)
    class TupleSubclass(tuple):
        pass
    for value in ([context("x")], TupleSubclass((context("x"),)), (item for item in (context("x"),))):
        err(lambda value=value: route_bounded_approved_workflow_continuation(first, wf, state, events, value), "contexts_type")


def test_09_context_members_must_be_exact_public_dataclass(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); first = preparation(wf, 2); calls = []
    class ContextSubclass(ApprovedWorkflowContinuationContext):
        pass
    malformed = object.__new__(ApprovedWorkflowContinuationContext)
    def fake(*args: object) -> object:
        calls.append(args)
        return complete(wf)
    err(lambda: route_bounded_approved_workflow_continuation(first, wf, state, events, (object(),)), "context_type")
    err(lambda: route_bounded_approved_workflow_continuation(first, wf, state, events, (ContextSubclass(*astuple(context("x"))),)), "context_type")
    err(lambda: route_bounded_approved_workflow_continuation(first, wf, state, events, (malformed,)), "context_type")
    assert calls == []


def test_10_non_callable_dependency_is_rejected_before_phase190(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); first = preparation(wf, 2); calls = []
    err(lambda: route_bounded_approved_workflow_continuation(*call_args(first, wf, state, events, (context("x"),)), phase190_function=None), "configuration")
    assert calls == []


def test_11_real_default_first_context_failure_stops_without_retry(tmp_path: Path) -> None:
    values = real_setup(tmp_path, current=9, count=11); wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    calls: list[object] = []
    first = real_context(wf, 10)
    first = ApprovedWorkflowContinuationContext(
        first.preparation_approval, first.employee, first.resolved_tools,
        first.api_key, first.execution_approval, synthetic_transport(calls, status=500),
    )
    later = real_context(wf, 11)
    later = ApprovedWorkflowContinuationContext(
        later.preparation_approval, later.employee, later.resolved_tools,
        later.api_key, later.execution_approval, synthetic_transport(calls),
    )
    result = route_bounded_approved_workflow_continuation(
        preparation(wf, 9), wf, values["state_path"], values["events_path"], (first, later)
    )
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure" and result.current_step_index == 10
    assert len(calls) == 1
    state = load_workflow_execution_state(values["state_path"])
    assert state.status == "failed" and state.current_step_index == 10
    assert len(values["events_path"].read_text().splitlines()) == 10


def test_12_unexpected_dependency_error_is_sanitized_without_runner_rollback(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); before = (state.read_bytes(), events.read_bytes()); first = preparation(wf, 2); calls = []
    def fake(*args: object) -> object:
        calls.append(args)
        state.write_bytes(b"phase190-owned")
        raise Phase190BaseError("secret")
    with pytest.raises(RunnerError) as caught:
        route_bounded_approved_workflow_continuation(*call_args(first, wf, state, events, (context("x"), context("later"))), phase190_function=fake)
    assert caught.value.detail.classification == "dependency_error"
    assert "secret" not in str(caught.value)
    assert "secret" not in repr(caught.value.detail)
    assert len(calls) == 1
    assert (state.read_bytes(), events.read_bytes()) == (b"phase190-owned", before[1])


def test_13_malformed_phase190_result_is_local_contract_failure_and_consumes_one(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); first = preparation(wf, 2); later = context("later"); calls = []
    def fake(*args: object) -> object:
        calls.append(args)
        return object()
    err(lambda: route_bounded_approved_workflow_continuation(*call_args(first, wf, state, events, (context("first"), later)), phase190_function=fake), "phase190_contract")
    assert len(calls) == 1


def test_14_malformed_phase190_transition_is_rejected_before_next_context(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); first = preparation(wf, 2); calls = []
    def fake(*args: object) -> object:
        calls.append(args)
        return preparation(wf, 2)
    err(lambda: route_bounded_approved_workflow_continuation(*call_args(first, wf, state, events, (context("first"), context("second"))), phase190_function=fake), "phase190_contract")
    assert len(calls) == 1

    calls.clear()
    malformed_current = preparation(wf, 2)
    object.__delattr__(malformed_current, "next_step_index")
    def break_current(*args: object) -> object:
        calls.append(args)
        return complete(wf)
    err(lambda: route_bounded_approved_workflow_continuation(*call_args(malformed_current, wf, state, events, (context("first"), context("second"))), phase190_function=break_current), "result_type")
    assert len(calls) == 0

    calls.clear()
    initial = preparation(wf, 2)
    def break_previous(*args: object) -> object:
        calls.append(args)
        object.__delattr__(args[0], "next_step_index")
        return complete(wf)
    err(lambda: route_bounded_approved_workflow_continuation(*call_args(initial, wf, state, events, (context("first"), context("second"))), phase190_function=break_previous), "phase190_contract")
    assert len(calls) == 1


def test_15_wrong_step_context_is_delegated_once_without_repair(tmp_path: Path) -> None:
    values = real_setup(tmp_path, current=9, count=11); wf = values["workflow"]
    assert isinstance(wf, WorkflowDefinition)
    calls: list[object] = []
    wrong = real_context(wf, 11)
    wrong = ApprovedWorkflowContinuationContext(
        wrong.preparation_approval, wrong.employee, wrong.resolved_tools,
        wrong.api_key, wrong.execution_approval, synthetic_transport(calls),
    )
    with pytest.raises(Phase145Error):
        route_bounded_approved_workflow_continuation(
            preparation(wf, 9), wf, values["state_path"], values["events_path"],
            (wrong, context("later")),
        )
    assert len(calls) == 0
    assert len(values["events_path"].read_text().splitlines()) == 9


def test_16_terminal_output_stops_before_later_context(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); first = preparation(wf, 2); stop = failure(wf, 3); calls = []
    def fake(*args: object) -> object:
        calls.append(args)
        return stop
    result = route_bounded_approved_workflow_continuation(*call_args(first, wf, state, events, (context("first"), context("later"))), phase190_function=fake)
    assert result is stop and len(calls) == 1


def test_17_terminal_output_workflow_complete_stops_before_later_context(tmp_path: Path) -> None:
    wf = workflow(); first = preparation(wf, 3); state, events = targets(tmp_path); stop = complete(wf); calls = []
    def fake(*args: object) -> object:
        calls.append(args)
        return stop
    result = route_bounded_approved_workflow_continuation(*call_args(first, wf, state, events, (context("first"), context("later"))), phase190_function=fake)
    assert result is stop and len(calls) == 1


def test_18_fields_are_structural_and_passed_without_runner_semantic_validation(tmp_path: Path) -> None:
    wf = workflow(3); state, events = targets(tmp_path); first = preparation(wf, 2); value = context("opaque"); result = complete(wf); seen = []
    def fake(*args: object) -> object:
        seen.append(args)
        return result
    assert route_bounded_approved_workflow_continuation(*call_args(first, wf, state, events, (value,)), phase190_function=fake) is result
    assert seen[0][2:] == ("preparation-opaque", "employee-opaque", state, events, "tools-opaque", "key-opaque", "execution-opaque", "transport-opaque")


def test_19_result_subclasses_and_discriminator_subclasses_are_rejected(tmp_path: Path) -> None:
    wf = workflow(); state, events = targets(tmp_path); first = preparation(wf, 2)
    class DecisionSubclass(WorkflowProgressionDecision):
        pass
    class StringSubclass(str):
        pass
    bad = replace(first, decision=StringSubclass("prepare_next_step"))
    err(lambda: route_bounded_approved_workflow_continuation(DecisionSubclass(*astuple(first)), wf, state, events, ()), "result_type")
    err(lambda: route_bounded_approved_workflow_continuation(bad, wf, state, events, ()), "result_type")


def test_20_real_default_second_context_failures_preserve_phase_190_ownership(tmp_path: Path) -> None:
    for mode in ("preparation", "execution"):
        (tmp_path / mode).mkdir(parents=True, exist_ok=True)
        values = real_setup(tmp_path / mode, current=9, count=11); wf = values["workflow"]
        assert isinstance(wf, WorkflowDefinition)
        calls: list[object] = []
        first = real_context(wf, 10)
        first = ApprovedWorkflowContinuationContext(
            first.preparation_approval, first.employee, first.resolved_tools,
            first.api_key, first.execution_approval, synthetic_transport(calls),
        )
        second = real_context(wf, 11, bad_execution_approval=mode == "execution")
        if mode == "preparation":
            second = ApprovedWorkflowContinuationContext(
                object(), second.employee, second.resolved_tools, second.api_key,
                second.execution_approval, synthetic_transport(calls),
            )
        else:
            second = ApprovedWorkflowContinuationContext(
                second.preparation_approval, second.employee, second.resolved_tools,
                second.api_key, second.execution_approval, synthetic_transport(calls),
            )
        with pytest.raises(Phase145Error if mode == "preparation" else Phase155Error):
            route_bounded_approved_workflow_continuation(
                preparation(wf, 9), wf, values["state_path"], values["events_path"],
                (first, second, context("unused")),
            )
        state = load_workflow_execution_state(values["state_path"])
        assert len(calls) == 1
        if mode == "preparation":
            assert state.status == "succeeded" and state.current_step_index == 10
        else:
            assert state.status == "running" and state.current_step_index == 11
        assert len(values["events_path"].read_text().splitlines()) == 10
