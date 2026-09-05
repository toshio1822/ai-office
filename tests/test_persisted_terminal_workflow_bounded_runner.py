"""Focused Phase-212 persisted-terminal bounded-resume tests.

Exactly 20 top-level tests are kept in this module.  Real-route cases use only
small deterministic synthetic transports; no provider, network, or paid API is
called.

Requirement-to-test mapping:
1 public API/error family/signature -> test_01_public_api_error_family_and_signature
2 pre-read callable dependencies -> test_02_pre_read_dependencies_are_callable_before_any_phase
3 exact outer Phase-37 call -> test_03_outer_phase37_receives_exact_arguments_once
4 strict outer classification result -> test_04_outer_classification_result_is_strict
5 exact Phase-38 call -> test_05_phase38_receives_outer_identity_and_exact_arguments_once
6 persisted-failure routing identity -> test_06_persisted_failure_requires_phase38_identity
7 persisted-success routing result -> test_07_persisted_success_routing_result_is_strict
8 persisted-failure terminal stop -> test_08_persisted_failure_terminal_ignores_malformed_contexts
9 final-success terminal stop -> test_09_final_success_terminal_ignores_malformed_contexts
10 deferred prepare configuration -> test_10_prepare_configuration_is_deferred_until_after_routing
11 exact Phase-192 call -> test_11_phase192_receives_routed_identity_and_exact_arguments_once
12 thin Phase-192 output seam -> test_12_phase192_output_family_and_linkage_are_strict
13 four-step restart success -> test_13_restart_success_does_not_replay_completed_steps
14 finite context exhaustion -> test_14_restart_context_exhaustion_returns_exact_remaining_prepare
15 resumed deterministic failure -> test_15_restart_failure_does_not_consume_later_context
16 ready/running exclusion -> test_16_ready_and_running_are_rejected_without_replay
17 corrupt history lower ownership -> test_17_corrupt_or_mismatched_history_stays_lower_owned
18 default safe errors and read-only behavior -> test_18_default_lower_safe_errors_and_read_only_targets_are_preserved
19 no outer rollback after Phase-192 -> test_19_phase192_owned_change_is_never_rolled_back
20 no loop/retry/auto-selection/source audit -> test_20_source_audit_proves_thin_separate_bounded_ownership
"""

# ruff: noqa: E501,F401,F811,I001

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import FrozenInstanceError, astuple, replace
from pathlib import Path
from typing import Literal, get_args

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedWorkflowContinuationContext,
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeCompatibilityError,
    PersistedExecutionOutcomeRoutingCompatibilityError,
    PersistedTerminalWorkflowBoundedRunnerClassification,
    PersistedTerminalWorkflowBoundedRunnerCompatibilityError,
    PersistedTerminalWorkflowBoundedRunnerError,
    PersistedTerminalWorkflowBoundedRunnerFailureDetail,
    WorkflowProgressionDecision,
    classify_persisted_execution_outcome_reentry,
    route_bounded_approved_workflow_continuation,
    route_persisted_execution_outcome_reentry,
    route_persisted_terminal_workflow_bounded,
)
from ai_office.engine.persisted_success_progression import (
    decide_persisted_success_progression,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145Error,
)
from ai_office.engine.approved_workflow_fresh_start import (
    ApprovedWorkflowBootstrapContext,
    InitialStepPreparationApproval,
    route_approved_workflow_fresh_start,
)
from ai_office.invocation import (
    ModelInvocationFailureCategory,
    ModelInvocationRequest,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    WorkflowExecutionLoadError,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_MODULE_PATH = Path(route_persisted_terminal_workflow_bounded.__code__.co_filename)
_MODULE_SOURCE = _MODULE_PATH.read_text(encoding="utf-8")
_MISSING = object()


def _workflow(count: int = 4) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "workflow",
            "name": "Synthetic workflow",
            "description": "Synthetic deterministic workflow",
            "steps": [
                {
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": f"employee-{index}",
                    "instructions": f"instructions-{index}",
                }
                for index in range(1, count + 1)
            ],
        }
    )


def _employee(workflow: WorkflowDefinition, index: int) -> EmployeeDefinition:
    step = workflow.steps[index - 1]
    return EmployeeDefinition(
        id=step.employee,
        name=f"Employee {index}",
        role="synthetic worker",
        instructions=f"employee instructions {index}",
        model="synthetic-model",
        allowed_tools=[],
    )


def _success_outcome(
    workflow: WorkflowDefinition, index: int = 1
) -> PersistedExecutionOutcome:
    step = workflow.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_success",
        workflow.id,
        step.id,
        index,
        step.employee,
        None,
    )


def _failure_outcome(
    workflow: WorkflowDefinition,
    index: int = 1,
    category: str = "api_error",
) -> PersistedExecutionOutcome:
    step = workflow.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure",
        workflow.id,
        step.id,
        index,
        step.employee,
        category,
    )


def _prepare(
    workflow: WorkflowDefinition, index: int = 1
) -> WorkflowProgressionDecision:
    current = workflow.steps[index - 1]
    next_step = workflow.steps[index]
    return WorkflowProgressionDecision(
        "prepare_next_step",
        workflow.id,
        current.id,
        index,
        current.employee,
        next_step.id,
        index + 1,
        next_step.employee,
        "next_step_available",
    )


def _complete(workflow: WorkflowDefinition) -> WorkflowProgressionDecision:
    final = workflow.steps[-1]
    return WorkflowProgressionDecision(
        "workflow_complete",
        workflow.id,
        final.id,
        len(workflow.steps),
        final.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def _opaque_context() -> ApprovedWorkflowContinuationContext:
    return ApprovedWorkflowContinuationContext(
        object(), object(), object(), object(), object(), object()
    )


def _transport(calls: list[str], label: str, *, status: int = 200):
    def send(_: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(label)
        if status != 200:
            return OpenAIResponsesRawHttpResponse(
                status,
                "synthetic failure",
                (("x-request-id", f"request-{label}"),),
                b'{"error":{"message":"safe failure","type":"server_error",'
                b'"param":null,"code":null}}',
            )
        body = (
            f'{{"id":"response-{label}","object":"response",'
            '"status":"completed","output":[{"type":"message",'
            '"content":[{"type":"output_text","text":"ok"}]}]}'
        ).encode()
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", f"request-{label}"),),
            body,
        )

    return send


def _bootstrap_context(
    workflow: WorkflowDefinition,
    calls: list[str],
    *,
    status: int = 200,
) -> ApprovedWorkflowBootstrapContext:
    step = workflow.steps[0]
    employee = _employee(workflow, 1)
    request = ModelInvocationRequest(
        employee.model,
        employee.instructions,
        step.instructions,
        (),
    )
    approval = approve_model_invocation_execution(
        request,
        (),
        provider="openai",
        approved_by="synthetic-reviewer",
        approval_id="synthetic-bootstrap-approval",
    )
    return ApprovedWorkflowBootstrapContext(
        InitialStepPreparationApproval(
            True,
            workflow.id,
            step.id,
            1,
            step.employee,
        ),
        employee,
        (),
        OpenAIApiKey(value=SecretStr("synthetic-key")),
        approval,
        _transport(calls, "step-1", status=status),
    )


def _continuation_context(
    workflow: WorkflowDefinition,
    index: int,
    calls: list[str],
    *,
    status: int = 200,
) -> ApprovedWorkflowContinuationContext:
    current = workflow.steps[index - 2]
    next_step = workflow.steps[index - 1]
    employee = _employee(workflow, index)
    upstream = UpstreamStepOutput(
        workflow.id,
        workflow.steps[index - 2].id,
        index - 1,
        workflow.steps[index - 2].employee,
        "ok",
    )
    request = ModelInvocationRequest(
        employee.model,
        employee.instructions,
        next_step.instructions,
        (),
        (upstream,),
    )
    approval = approve_model_invocation_execution(
        request,
        (),
        provider="openai",
        approved_by="synthetic-reviewer",
        approval_id=f"synthetic-approval-{index}",
    )
    from ai_office.engine.next_step_preparation import NextStepPreparationApproval

    return ApprovedWorkflowContinuationContext(
        NextStepPreparationApproval(
            True,
            workflow.id,
            current.id,
            index - 1,
            next_step.id,
            index,
            next_step.employee,
        ),
        employee,
        (),
        OpenAIApiKey(value=SecretStr("synthetic-key")),
        approval,
        _transport(calls, f"step-{index}", status=status),
    )


def _seed_two_step_prefix(
    tmp_path: Path,
    workflow: WorkflowDefinition,
    calls: list[str],
) -> tuple[Path, Path]:
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    first = route_approved_workflow_fresh_start(
        workflow,
        state_path,
        events_path,
        _bootstrap_context(workflow, calls),
    )
    assert type(first) is WorkflowProgressionDecision
    assert first.decision == "prepare_next_step"
    second = route_bounded_approved_workflow_continuation(
        first,
        workflow,
        state_path,
        events_path,
        (_continuation_context(workflow, 2, calls),),
    )
    assert type(second) is WorkflowProgressionDecision
    assert second.decision == "prepare_next_step"
    assert second.current_step_index == 2
    return state_path, events_path


def _write_history(
    tmp_path: Path,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> tuple[Path, Path]:
    state_path = tmp_path / "state.json"
    events_path = tmp_path / "events.jsonl"
    state_path.write_bytes(
        serialize_workflow_execution_state_json(state).encode("utf-8")
    )
    events_path.write_bytes(
        b"".join(
            serialize_runtime_step_event_jsonl(event).encode("utf-8")
            for event in events
        )
    )
    return state_path, events_path


def _state(
    workflow: WorkflowDefinition,
    *,
    status: Literal["ready", "running", "succeeded", "failed"] = "succeeded",
    index: int = 1,
    completed: tuple[str, ...] = ("step-1",),
    category: str | None = None,
    workflow_id: str | None = None,
) -> WorkflowExecutionState:
    step = workflow.steps[index - 1]
    return WorkflowExecutionState(
        workflow_id or workflow.id,
        status,
        step.id,
        index,
        step.employee,
        completed,
        category,
    )


def _event(
    workflow: WorkflowDefinition,
    *,
    index: int = 1,
    event_type: Literal["step_succeeded", "step_failed"] = "step_succeeded",
    category: str | None = None,
    workflow_id: str | None = None,
) -> RuntimeStepEvent:
    step = workflow.steps[index - 1]
    failed = event_type == "step_failed"
    return RuntimeStepEvent(
        event_type,
        workflow_id or workflow.id,
        step.id,
        index,
        step.employee,
        "running",
        "failed" if failed else "succeeded",
        "openai",
        category,
        None if failed else f"response-step-{index}",
        None if failed else f"request-step-{index}",
        None if failed else "ok",
        "safe failure" if failed else None,
    )


def _assert_runner_error(call, classification: str) -> None:
    with pytest.raises(PersistedTerminalWorkflowBoundedRunnerCompatibilityError) as caught:
        call()
    assert caught.value.detail.classification == classification
    assert str(caught.value) == (
        "persisted terminal workflow bounded runner inputs are incompatible"
    )
    assert "secret" not in str(caught.value)


def test_01_public_api_error_family_and_signature() -> None:
    signature = inspect.signature(route_persisted_terminal_workflow_bounded)
    assert list(signature.parameters) == [
        "workflow",
        "state_path",
        "events_path",
        "continuation_contexts",
        "classification_function",
        "routing_function",
        "bounded_continuation_function",
    ]
    for name in (
        "classification_function",
        "routing_function",
        "bounded_continuation_function",
    ):
        assert signature.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert (
        signature.parameters["classification_function"].default
        is classify_persisted_execution_outcome_reentry
    )
    assert (
        signature.parameters["routing_function"].default
        is route_persisted_execution_outcome_reentry
    )
    assert (
        signature.parameters["bounded_continuation_function"].default
        is route_bounded_approved_workflow_continuation
    )
    assert get_args(PersistedTerminalWorkflowBoundedRunnerClassification) == (
        "configuration",
        "classification_contract",
        "routing_contract",
        "contexts_type",
        "context_type",
        "bounded_continuation_contract",
        "dependency_error",
    )
    assert issubclass(
        PersistedTerminalWorkflowBoundedRunnerCompatibilityError,
        PersistedTerminalWorkflowBoundedRunnerError,
    )
    assert issubclass(PersistedTerminalWorkflowBoundedRunnerError, ValueError)
    detail = PersistedTerminalWorkflowBoundedRunnerFailureDetail("configuration")
    assert detail.classification == "configuration"
    with pytest.raises(FrozenInstanceError):
        detail.classification = "dependency_error"  # type: ignore[misc]
    assert {
        "PersistedTerminalWorkflowBoundedRunnerClassification",
        "PersistedTerminalWorkflowBoundedRunnerFailureDetail",
        "PersistedTerminalWorkflowBoundedRunnerError",
        "PersistedTerminalWorkflowBoundedRunnerCompatibilityError",
        "route_persisted_terminal_workflow_bounded",
    } == set(
        __import__(
            "ai_office.engine.persisted_terminal_workflow_bounded_runner",
            fromlist=["__all__"],
        ).__all__
    )


def test_02_pre_read_dependencies_are_callable_before_any_phase() -> None:
    workflow = _workflow(1)
    calls = [0, 0, 0]

    def classification(*_: object) -> object:
        calls[0] += 1
        return _success_outcome(workflow)

    def routing(*_: object) -> object:
        calls[1] += 1
        return _complete(workflow)

    def bounded(*_: object) -> object:
        calls[2] += 1
        return _complete(workflow)

    _assert_runner_error(
        lambda: route_persisted_terminal_workflow_bounded(
            object(),
            object(),
            object(),
            object(),
            classification_function=None,
            routing_function=routing,
            bounded_continuation_function=bounded,
        ),
        "configuration",
    )
    assert calls == [0, 0, 0]
    _assert_runner_error(
        lambda: route_persisted_terminal_workflow_bounded(
            object(),
            object(),
            object(),
            object(),
            classification_function=classification,
            routing_function=None,
            bounded_continuation_function=bounded,
        ),
        "configuration",
    )
    assert calls == [0, 0, 0]


def test_03_outer_phase37_receives_exact_arguments_once() -> None:
    workflow = _workflow(1)
    state_path = object()
    events_path = object()
    classified = _success_outcome(workflow)
    routed = _complete(workflow)
    classification_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    routing_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def classification(*args: object, **kwargs: object) -> object:
        classification_calls.append((args, kwargs))
        return classified

    def routing(*args: object, **kwargs: object) -> object:
        routing_calls.append((args, kwargs))
        return routed

    def never_bounded(*_: object, **__: object) -> object:
        raise AssertionError("terminal routing result must bypass Phase 192")

    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        object(),
        classification_function=classification,
        routing_function=routing,
        bounded_continuation_function=never_bounded,
    )
    assert result is routed
    assert classification_calls == [((workflow, state_path, events_path), {})]
    assert routing_calls == [
        ((classified, workflow, state_path, events_path), {})
    ]


def test_04_outer_classification_result_is_strict() -> None:
    workflow = _workflow(2)
    state_path, events_path = object(), object()
    routing_calls = 0

    def route_valid(value: object, *_: object) -> object:
        nonlocal routing_calls
        routing_calls += 1
        return value if _get(value, "outcome") == "persisted_failure" else _prepare(workflow)

    for category in get_args(ModelInvocationFailureCategory):
        failure = _failure_outcome(workflow, category=category)
        result = route_persisted_terminal_workflow_bounded(
            workflow,
            state_path,
            events_path,
            object(),
            classification_function=lambda *_args, value=failure: value,
            routing_function=route_valid,
        )
        assert result is failure

    class OutcomeSubclass(PersistedExecutionOutcome):
        pass

    valid_success = _success_outcome(workflow)
    valid_failure = _failure_outcome(workflow)
    invalid_values: tuple[object, ...] = (
        object(),
        replace(valid_success, outcome="persisted_failure"),
        replace(valid_success, workflow_id="other"),
        replace(valid_success, current_step_id="other"),
        replace(valid_success, current_step_index=2),
        replace(valid_success, current_employee_id="other"),
        replace(valid_success, failure_category="api_error"),
        replace(valid_failure, failure_category="not-a-category"),
        replace(
            valid_failure,
            failure_category=type("CategorySubclass", (str,), {})("api_error"),
        ),
        OutcomeSubclass(*astuple(valid_success)),
    )
    for invalid in invalid_values:
        _assert_runner_error(
            lambda invalid=invalid: route_persisted_terminal_workflow_bounded(
                workflow,
                state_path,
                events_path,
                object(),
                classification_function=lambda *_args, value=invalid: value,
                routing_function=route_valid,
            ),
            "classification_contract",
        )
    assert routing_calls == len(get_args(ModelInvocationFailureCategory))


def test_05_phase38_receives_outer_identity_and_exact_arguments_once() -> None:
    workflow = _workflow(2)
    state_path, events_path = object(), object()
    classified = _success_outcome(workflow)
    routed = _prepare(workflow)
    contexts = (_opaque_context(),)
    routing_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    bounded_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    final = _complete(workflow)

    def routing(*args: object, **kwargs: object) -> object:
        routing_calls.append((args, kwargs))
        return routed

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return final

    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        contexts,
        classification_function=lambda *args: classified,
        routing_function=routing,
        bounded_continuation_function=bounded,
    )
    assert result is final
    assert routing_calls == [
        ((classified, workflow, state_path, events_path), {})
    ]
    assert bounded_calls == [
        ((routed, workflow, state_path, events_path, contexts), {})
    ]


def test_06_persisted_failure_requires_phase38_identity() -> None:
    workflow = _workflow(2)
    failure = _failure_outcome(workflow)
    for translated in (
        replace(failure),
        _prepare(workflow),
        _complete(workflow),
        _failure_outcome(workflow, category="transport_error"),
    ):
        _assert_runner_error(
            lambda translated=translated: route_persisted_terminal_workflow_bounded(
                workflow,
                object(),
                object(),
                object(),
                classification_function=lambda *_args: failure,
                routing_function=lambda *_args, translated=translated: translated,
            ),
            "routing_contract",
        )
    assert (
        route_persisted_terminal_workflow_bounded(
            workflow,
            object(),
            object(),
            object(),
            classification_function=lambda *_args: failure,
            routing_function=lambda *_args: failure,
            bounded_continuation_function=None,
        )
        is failure
    )


def test_07_persisted_success_routing_result_is_strict() -> None:
    workflow = _workflow(2)
    classified = _success_outcome(workflow)
    valid_prepare = _prepare(workflow)
    valid_complete = _complete(workflow)
    assert (
        route_persisted_terminal_workflow_bounded(
            workflow,
            object(),
            object(),
            (_opaque_context(),),
            classification_function=lambda *_args: classified,
            routing_function=lambda *_args: valid_prepare,
            bounded_continuation_function=lambda *_args: valid_complete,
        )
        is valid_complete
    )
    one_step_workflow = _workflow(1)
    one_step_classified = _success_outcome(one_step_workflow)
    one_step_complete = _complete(one_step_workflow)
    assert (
        route_persisted_terminal_workflow_bounded(
            one_step_workflow,
            object(),
            object(),
            object(),
            classification_function=lambda *_args: one_step_classified,
            routing_function=lambda *_args: one_step_complete,
            bounded_continuation_function=None,
        )
        is one_step_complete
    )

    class DecisionSubclass(WorkflowProgressionDecision):
        pass

    class DiscriminatorSubclass(str):
        pass

    invalid_values: tuple[object, ...] = (
        object(),
        _failure_outcome(workflow),
        replace(valid_prepare, next_step_id="other"),
        replace(valid_prepare, next_step_index=3),
        replace(valid_prepare, next_employee_id="other"),
        replace(valid_prepare, reason="other"),
        replace(valid_prepare, decision=DiscriminatorSubclass("prepare_next_step")),
        replace(valid_complete, current_step_id="other"),
        DecisionSubclass(*astuple(valid_prepare)),
    )
    for invalid in invalid_values:
        _assert_runner_error(
            lambda invalid=invalid: route_persisted_terminal_workflow_bounded(
                workflow,
                object(),
                object(),
                object(),
                classification_function=lambda *_args: classified,
                routing_function=lambda *_args, invalid=invalid: invalid,
                bounded_continuation_function=lambda *_args: valid_complete,
            ),
            "routing_contract",
        )


def test_08_persisted_failure_terminal_ignores_malformed_contexts(
    tmp_path: Path,
) -> None:
    workflow = _workflow(1)
    calls: list[str] = []
    result = route_approved_workflow_fresh_start(
        workflow,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        _bootstrap_context(workflow, calls, status=500),
    )
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    classified_results: list[object] = []
    outer_calls = 0
    inner_calls = 0
    routing_calls = 0
    progression_calls = 0

    def outer(*args: object) -> object:
        nonlocal outer_calls
        outer_calls += 1
        value = classify_persisted_execution_outcome_reentry(*args)
        classified_results.append(value)
        return value

    def inner(*args: object) -> object:
        nonlocal inner_calls
        inner_calls += 1
        return classify_persisted_execution_outcome_reentry(*args)

    def progression(*args: object) -> object:
        nonlocal progression_calls
        progression_calls += 1
        return decide_persisted_success_progression(*args)

    def routing(*args: object) -> object:
        nonlocal routing_calls
        routing_calls += 1
        return route_persisted_execution_outcome_reentry(
            *args,
            classification_function=inner,
            progression_function=progression,
        )

    final = route_persisted_terminal_workflow_bounded(
        workflow,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        [object(), object()],
        classification_function=outer,
        routing_function=routing,
        bounded_continuation_function=None,
    )
    assert final is classified_results[0]
    assert final is not result
    assert calls == ["step-1"]
    assert (outer_calls, routing_calls, inner_calls, progression_calls) == (1, 1, 1, 0)
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 1


def test_09_final_success_terminal_ignores_malformed_contexts(tmp_path: Path) -> None:
    workflow = _workflow(4)
    calls: list[str] = []
    state_path, events_path = _seed_two_step_prefix(tmp_path, workflow, calls)
    completed = route_bounded_approved_workflow_continuation(
        _prepare(workflow, 2),
        workflow,
        state_path,
        events_path,
        (
            _continuation_context(workflow, 3, calls),
            _continuation_context(workflow, 4, calls),
        ),
    )
    assert type(completed) is WorkflowProgressionDecision
    assert completed.decision == "workflow_complete"
    assert load_workflow_execution_state(state_path).status == "succeeded"
    outer_calls = 0
    inner_calls = 0
    routing_calls = 0
    progression_calls = 0
    phase31_result: list[object] = []
    phase38_result: list[object] = []

    def outer(*args: object) -> object:
        nonlocal outer_calls
        outer_calls += 1
        return classify_persisted_execution_outcome_reentry(*args)

    def inner(*args: object) -> object:
        nonlocal inner_calls
        inner_calls += 1
        return classify_persisted_execution_outcome_reentry(*args)

    def progression(*args: object) -> object:
        nonlocal progression_calls
        progression_calls += 1
        value = decide_persisted_success_progression(*args)
        phase31_result.append(value)
        return value

    def routing(*args: object) -> object:
        nonlocal routing_calls
        routing_calls += 1
        value = route_persisted_execution_outcome_reentry(
            *args,
            classification_function=inner,
            progression_function=progression,
        )
        phase38_result.append(value)
        return value

    final = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        [object(), object()],
        classification_function=outer,
        routing_function=routing,
        bounded_continuation_function=None,
    )
    assert type(final) is WorkflowProgressionDecision
    assert final.decision == "workflow_complete"
    assert len(phase31_result) == 1
    assert len(phase38_result) == 1
    assert phase31_result == [final]
    assert final is phase31_result[0]
    assert final is phase38_result[0]
    assert phase31_result[0] is phase38_result[0]
    assert (outer_calls, routing_calls, inner_calls, progression_calls) == (1, 1, 1, 1)
    assert calls == ["step-1", "step-2", "step-3", "step-4"]
    assert len(events_path.read_text().splitlines()) == 4


def test_10_prepare_configuration_is_deferred_until_after_routing() -> None:
    workflow = _workflow(2)
    classified = _success_outcome(workflow)
    routed = _prepare(workflow)
    for contexts, bounded, expected in (
        ([], lambda *_args: routed, "contexts_type"),
        ((_opaque_context(), object()), lambda *_args: routed, "context_type"),
        ((_opaque_context(),), None, "configuration"),
    ):
        observed = [False, False, 0]

        def classification(*_: object) -> object:
            observed[0] = True
            return classified

        def routing(*_: object) -> object:
            observed[1] = observed[0]
            return routed

        def bounded_function(*_: object) -> object:
            observed[2] += 1
            return _complete(workflow)

        _assert_runner_error(
            lambda contexts=contexts, bounded=bounded: route_persisted_terminal_workflow_bounded(
                workflow,
                object(),
                object(),
                contexts,
                classification_function=classification,
                routing_function=routing,
                bounded_continuation_function=(
                    bounded if bounded is not None else bounded_function
                ),
            )
            if bounded is not None
            else route_persisted_terminal_workflow_bounded(
                workflow,
                object(),
                object(),
                contexts,
                classification_function=classification,
                routing_function=routing,
                bounded_continuation_function=None,
            ),
            expected,
        )
        assert observed == [True, True, 0]


def test_11_phase192_receives_routed_identity_and_exact_arguments_once() -> None:
    workflow = _workflow(2)
    state_path = object()
    events_path = object()
    classified = _success_outcome(workflow)
    routed = _prepare(workflow)
    contexts = (_opaque_context(),)
    bounded_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    returned = _failure_outcome(workflow, index=2, category="invalid_output")

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return returned

    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        contexts,
        classification_function=lambda *_args: classified,
        routing_function=lambda *_args: routed,
        bounded_continuation_function=bounded,
    )
    assert result is returned
    assert len(bounded_calls) == 1
    args, kwargs = bounded_calls[0]
    assert len(args) == 5
    assert kwargs == {}
    assert args[0] is routed
    assert args[1] is workflow
    assert args[2] is state_path
    assert args[3] is events_path
    assert args[4] is contexts


def test_12_phase192_output_family_and_linkage_are_strict() -> None:
    workflow = _workflow(3)
    classified = _success_outcome(workflow)
    routed = _prepare(workflow, 1)
    contexts = (_opaque_context(),)
    valid_prepare = _prepare(workflow, 2)
    valid_failure = _failure_outcome(workflow, index=2, category="api_error")

    class DecisionSubclass(WorkflowProgressionDecision):
        pass

    class OutcomeSubclass(PersistedExecutionOutcome):
        pass

    invalid_values: tuple[object, ...] = (
        object(),
        routed,
        replace(valid_prepare, workflow_id="other"),
        replace(valid_prepare, current_step_id="other"),
        replace(valid_prepare, current_step_index=3),
        replace(valid_prepare, current_employee_id="other"),
        replace(valid_prepare, next_step_id="other"),
        replace(valid_prepare, next_step_index=1),
        replace(valid_prepare, next_employee_id="other"),
        replace(valid_prepare, reason="other"),
        _success_outcome(workflow, index=2),
        replace(valid_failure, failure_category="not-a-category"),
        DecisionSubclass(*astuple(valid_prepare)),
        OutcomeSubclass(*astuple(valid_failure)),
    )
    for invalid in invalid_values:
        _assert_runner_error(
            lambda invalid=invalid: route_persisted_terminal_workflow_bounded(
                workflow,
                object(),
                object(),
                contexts,
                classification_function=lambda *_args: classified,
                routing_function=lambda *_args: routed,
                bounded_continuation_function=lambda *_args, invalid=invalid: invalid,
            ),
            "bounded_continuation_contract",
        )
    assert (
        route_persisted_terminal_workflow_bounded(
            workflow,
            object(),
            object(),
            contexts,
            classification_function=lambda *_args: classified,
            routing_function=lambda *_args: routed,
            bounded_continuation_function=lambda *_args: valid_prepare,
        )
        is valid_prepare
    )
    assert (
        route_persisted_terminal_workflow_bounded(
            workflow,
            object(),
            object(),
            contexts,
            classification_function=lambda *_args: classified,
            routing_function=lambda *_args: routed,
            bounded_continuation_function=lambda *_args: valid_failure,
        )
        is valid_failure
    )


def test_13_restart_success_does_not_replay_completed_steps(tmp_path: Path) -> None:
    workflow = _workflow(4)
    calls: list[str] = []
    state_path, events_path = _seed_two_step_prefix(tmp_path, workflow, calls)
    # The in-memory prepare decision from before restart is intentionally not
    # passed to Phase 212; only the durable files and new future contexts are.
    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        (
            _continuation_context(workflow, 3, calls),
            _continuation_context(workflow, 4, calls),
        ),
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_index == 4
    assert calls == ["step-1", "step-2", "step-3", "step-4"]
    state = load_workflow_execution_state(state_path)
    assert state.status == "succeeded"
    assert state.completed_step_ids == ("step-1", "step-2", "step-3", "step-4")
    event_ids = [
        json.loads(line)["step_id"] for line in events_path.read_text().splitlines()
    ]
    assert event_ids == ["step-1", "step-2", "step-3", "step-4"]


def test_14_restart_context_exhaustion_returns_exact_remaining_prepare(
    tmp_path: Path,
) -> None:
    workflow = _workflow(4)
    calls: list[str] = []
    state_path, events_path = _seed_two_step_prefix(tmp_path, workflow, calls)
    bounded_results: list[object] = []

    def bounded(*args: object) -> object:
        value = route_bounded_approved_workflow_continuation(*args)
        bounded_results.append(value)
        return value

    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        (_continuation_context(workflow, 3, calls),),
        bounded_continuation_function=bounded,
    )
    assert result is bounded_results[0]
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "prepare_next_step"
    assert result.current_step_index == 3
    assert result.next_step_index == 4
    assert calls == ["step-1", "step-2", "step-3"]
    assert load_workflow_execution_state(state_path).completed_step_ids == (
        "step-1",
        "step-2",
        "step-3",
    )
    assert len(events_path.read_text().splitlines()) == 3


def test_15_restart_failure_does_not_consume_later_context(tmp_path: Path) -> None:
    workflow = _workflow(4)
    calls: list[str] = []
    state_path, events_path = _seed_two_step_prefix(tmp_path, workflow, calls)
    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        (
            _continuation_context(workflow, 3, calls, status=500),
            _continuation_context(workflow, 4, calls),
        ),
    )
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    assert result.current_step_index == 3
    assert result.failure_category == "api_error"
    assert calls == ["step-1", "step-2", "step-3"]
    state = load_workflow_execution_state(state_path)
    assert state.status == "failed"
    assert state.current_step_index == 3
    assert state.completed_step_ids == ("step-1", "step-2")
    assert len(events_path.read_text().splitlines()) == 3


def test_16_ready_and_running_are_rejected_without_replay(tmp_path: Path) -> None:
    workflow = _workflow(2)
    for status in ("ready", "running"):
        directory = tmp_path / status
        directory.mkdir()
        state_path, events_path = _write_history(
            directory,
            _state(
                workflow,
                status=status,  # type: ignore[arg-type]
                index=1,
                completed=(),
            ),
            (),
        )
        calls = [0, 0]
        transport_calls: list[str] = []

        def routing(*_: object) -> object:
            calls[0] += 1
            raise AssertionError("Phase 38 must not be called")

        def bounded(*_: object) -> object:
            calls[1] += 1
            raise AssertionError("Phase 192 must not be called")

        with pytest.raises(PersistedExecutionOutcomeCompatibilityError) as caught:
            route_persisted_terminal_workflow_bounded(
                workflow,
                state_path,
                events_path,
                (_continuation_context(workflow, 2, transport_calls),),
                routing_function=routing,
                bounded_continuation_function=bounded,
            )
        assert caught.value.detail.classification == "state_status"
        assert calls == [0, 0]
        assert transport_calls == []
        assert events_path.read_bytes() == b""


def test_17_corrupt_or_mismatched_history_stays_lower_owned(tmp_path: Path) -> None:
    workflow = _workflow(2)
    cases: list[tuple[bytes, bytes]] = []
    cases.append((b"{", b""))
    mismatched_state = _state(workflow, workflow_id="other")
    cases.append(
        (
            serialize_workflow_execution_state_json(mismatched_state).encode(),
            serialize_runtime_step_event_jsonl(_event(workflow, workflow_id="other")).encode(),
        )
    )
    for index, (state_bytes, event_bytes) in enumerate(cases):
        directory = tmp_path / str(index)
        directory.mkdir()
        state_path, events_path = directory / "state.json", directory / "events.jsonl"
        state_path.write_bytes(state_bytes)
        events_path.write_bytes(event_bytes)
        calls = [0, 0]

        def routing(*_: object) -> object:
            calls[0] += 1
            raise AssertionError("lower classification failure must stop before Phase 38")

        def bounded(*_: object) -> object:
            calls[1] += 1
            raise AssertionError("Phase 192 must not be called")

        with pytest.raises((PersistedExecutionOutcomeCompatibilityError, WorkflowExecutionLoadError)):
            route_persisted_terminal_workflow_bounded(
                workflow,
                state_path,
                events_path,
                (_opaque_context(),),
                routing_function=routing,
                bounded_continuation_function=bounded,
            )
        assert calls == [0, 0]


def test_18_default_lower_safe_errors_and_read_only_targets_are_preserved(
    tmp_path: Path,
) -> None:
    workflow = _workflow(3)
    classified = _success_outcome(workflow)
    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError):
        route_persisted_terminal_workflow_bounded(
            workflow,
            object(),
            object(),
            (),
            classification_function=lambda *_args: classified,
            routing_function=route_persisted_execution_outcome_reentry,
        )

    calls: list[str] = []
    state_path, events_path = _seed_two_step_prefix(tmp_path, workflow, calls)
    before = (state_path.read_bytes(), events_path.read_bytes())
    with pytest.raises(Phase145Error):
        route_persisted_terminal_workflow_bounded(
            workflow,
            state_path,
            events_path,
            (_opaque_context(),),
        )
    assert (state_path.read_bytes(), events_path.read_bytes()) == before
    assert calls == ["step-1", "step-2"]


def test_19_phase192_owned_change_is_never_rolled_back(tmp_path: Path) -> None:
    workflow = _workflow(2)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(b"before-state")
    events_path.write_bytes(b"before-events")
    classified = _success_outcome(workflow)
    routed = _prepare(workflow)
    contexts = (_opaque_context(),)

    def mutating_return(*_: object) -> object:
        state_path.write_bytes(b"phase192-owned-return")
        return object()

    _assert_runner_error(
        lambda: route_persisted_terminal_workflow_bounded(
            workflow,
            state_path,
            events_path,
            contexts,
            classification_function=lambda *_args: classified,
            routing_function=lambda *_args: routed,
            bounded_continuation_function=mutating_return,
        ),
        "bounded_continuation_contract",
    )
    assert state_path.read_bytes() == b"phase192-owned-return"
    assert events_path.read_bytes() == b"before-events"

    def mutating_error(*_: object) -> object:
        state_path.write_bytes(b"phase192-owned-error")
        raise RuntimeError("secret provider payload")

    _assert_runner_error(
        lambda: route_persisted_terminal_workflow_bounded(
            workflow,
            state_path,
            events_path,
            contexts,
            classification_function=lambda *_args: classified,
            routing_function=lambda *_args: routed,
            bounded_continuation_function=mutating_error,
        ),
        "dependency_error",
    )
    assert state_path.read_bytes() == b"phase192-owned-error"
    assert events_path.read_bytes() == b"before-events"


def test_20_source_audit_proves_thin_separate_bounded_ownership() -> None:
    tree = ast.parse(_MODULE_SOURCE)
    assert not any(
        isinstance(node, (ast.For, ast.AsyncFor, ast.While))
        for node in ast.walk(tree)
    )
    assert _MODULE_SOURCE.count("classification_function(") == 1
    assert _MODULE_SOURCE.count("routing_function(") == 1
    assert _MODULE_SOURCE.count("bounded_continuation_function(") == 1
    for forbidden in (
        "route_approved_fresh_workflow_bounded",
        "fresh_or_resume",
        "route_approved_workflow_fresh_start",
        "approve_model_invocation",
        "OpenAIApiKey",
        "retry",
        "scheduler",
        "read_bytes",
        "write_bytes",
        "restore",
        "Phase190",
    ):
        assert forbidden not in _MODULE_SOURCE
    assert "for context in" not in _MODULE_SOURCE
    assert "while " not in _MODULE_SOURCE
    assert "route_persisted_terminal_workflow_bounded" in _MODULE_SOURCE


def _get(value: object, name: str) -> object:
    try:
        return getattr(value, name)
    except Exception:
        return _MISSING
