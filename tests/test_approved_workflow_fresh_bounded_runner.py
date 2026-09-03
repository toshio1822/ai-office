"""Focused Phase-210 fresh-start plus bounded-continuation runner tests.

Exactly 20 top-level ``test_*`` definitions; exactly 20 collected tests.
All provider-facing transports below are deterministic synthetic functions.

Requirement-to-test mapping:
1 public API/source audit -> test_01_public_api_and_source_audit
2 context container prevalidation -> test_02_context_container_rejected_before_phase208
3 context element exactness -> test_03_context_elements_rejected_before_phase208
4 dependency configuration -> test_04_dependencies_must_be_callable_before_phase208
5 Phase208 delegation -> test_05_phase208_is_called_once_with_canonical_arguments
6/7 terminal Phase208 identity and zero Phase192 -> tests 06/07
8 prepare Phase192 delegation -> test_08_prepare_delegates_phase192_once
9/10 malformed and unexpected Phase208 dependency -> tests 09/10
11 default Phase208 safe ownership -> test_11_default_phase208_safe_errors_keep_durable_ownership
12/13 malformed and unexpected Phase192 dependency -> tests 12/13
14 default Phase192/190 safe ownership -> test_14_default_phase192_safe_errors_keep_later_ownership
15 thin result family/linkage seam -> test_15_result_family_and_linkage_are_strict
16/17/18 real bounded routes -> tests 16/17/18
19/20 terminal real fresh routes -> tests 19/20
"""

# ruff: noqa: E501,F401,F811,I001

from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, astuple, replace
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedFreshWorkflowBoundedRunnerClassification,
    ApprovedFreshWorkflowBoundedRunnerCompatibilityError,
    ApprovedFreshWorkflowBoundedRunnerError,
    ApprovedFreshWorkflowBoundedRunnerFailureDetail,
    ApprovedWorkflowBootstrapContext,
    ApprovedWorkflowContinuationContext,
    InitialStepPreparationApproval,
    route_approved_fresh_workflow_bounded,
    route_approved_workflow_fresh_start,
    route_bounded_approved_workflow_continuation,
)
from ai_office.engine.approved_workflow_fresh_start import (
    FreshWorkflowBootstrapCompatibilityError,
)
from ai_office.engine.next_step_preparation import NextStepPreparationApproval
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145CompatibilityError,
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase145Error,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase155CompatibilityError,
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationFailureCategory,
    ModelInvocationRequest,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.runtime.persisted_start_execution import (
    PersistedStartExecutionCompatibilityError,
)
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_state,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_MODULE_PATH = Path(route_approved_fresh_workflow_bounded.__code__.co_filename)
_MODULE_SOURCE = _MODULE_PATH.read_text(encoding="utf-8")
_MISSING = object()


def workflow(count: int = 3) -> WorkflowDefinition:
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


def _employee(wf: WorkflowDefinition, index: int) -> EmployeeDefinition:
    step = wf.steps[index - 1]
    return EmployeeDefinition(
        id=step.employee,
        name=f"Employee {index}",
        role="synthetic worker",
        instructions=f"employee instructions {index}",
        model="synthetic-model",
        allowed_tools=[],
    )


def _success_transport(calls: list[str], label: str):
    def transport(_: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(label)
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", f"request-{label}"),),
            (
                f'{{"id":"response-{label}","object":"response",'
                '"status":"completed","output":[{"type":"message",'
                '"content":[{"type":"output_text","text":"ok"}]}]}'
            ).encode(),
        )

    return transport


def _failure_transport(calls: list[str], label: str):
    def transport(_: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(label)
        return OpenAIResponsesRawHttpResponse(
            500,
            "synthetic error",
            (("x-request-id", f"request-{label}"),),
            b'{"error":{"message":"safe failure","type":"server_error",'
            b'"param":null,"code":null}}',
        )

    return transport


def _bootstrap_context(
    wf: WorkflowDefinition,
    calls: list[str] | None = None,
    *,
    preparation_approval: object = _MISSING,
    employee_value: object = _MISSING,
    execution_approval: object = _MISSING,
    transport: object = _MISSING,
) -> ApprovedWorkflowBootstrapContext:
    employee = _employee(wf, 1)
    request = ModelInvocationRequest(
        employee.model,
        employee.instructions,
        wf.steps[0].instructions,
        (),
    )
    approved_execution = approve_model_invocation_execution(
        request,
        (),
        provider="openai",
        approved_by="synthetic-reviewer",
        approval_id="synthetic-approval-1",
    )
    context = ApprovedWorkflowBootstrapContext(
        InitialStepPreparationApproval(
            True, wf.id, wf.steps[0].id, 1, wf.steps[0].employee
        ),
        employee,
        (),
        OpenAIApiKey(value=SecretStr("synthetic-key")),
        approved_execution,
        _success_transport(calls, "step-1") if calls is not None else object(),
    )
    if preparation_approval is not _MISSING:
        context = replace(context, preparation_approval=preparation_approval)
    if employee_value is not _MISSING:
        context = replace(context, employee=employee_value)
    if execution_approval is not _MISSING:
        context = replace(context, execution_approval=execution_approval)
    if transport is not _MISSING:
        context = replace(context, transport=transport)
    return context


def _continuation_context(
    wf: WorkflowDefinition,
    index: int,
    calls: list[str] | None = None,
    *,
    preparation_approval: object = _MISSING,
    execution_approval: object = _MISSING,
    transport: object = _MISSING,
) -> ApprovedWorkflowContinuationContext:
    employee = _employee(wf, index)
    request = ModelInvocationRequest(
        employee.model,
        employee.instructions,
        wf.steps[index - 1].instructions,
        (),
    )
    approved_execution = approve_model_invocation_execution(
        request,
        (),
        provider="openai",
        approved_by="synthetic-reviewer",
        approval_id=f"synthetic-approval-{index}",
    )
    context = ApprovedWorkflowContinuationContext(
        NextStepPreparationApproval(
            True,
            wf.id,
            wf.steps[index - 2].id,
            index - 1,
            wf.steps[index - 1].id,
            index,
            wf.steps[index - 1].employee,
        ),
        employee,
        (),
        OpenAIApiKey(value=SecretStr("synthetic-key")),
        approved_execution,
        _success_transport(calls, f"step-{index}") if calls is not None else object(),
    )
    if preparation_approval is not _MISSING:
        context = replace(context, preparation_approval=preparation_approval)
    if execution_approval is not _MISSING:
        context = replace(context, execution_approval=execution_approval)
    if transport is not _MISSING:
        context = replace(context, transport=transport)
    return context


def _opaque_context() -> ApprovedWorkflowContinuationContext:
    return ApprovedWorkflowContinuationContext(
        object(), object(), object(), object(), object(), object()
    )


def _prepare(wf: WorkflowDefinition, index: int = 1) -> WorkflowProgressionDecision:
    current = wf.steps[index - 1]
    next_step = wf.steps[index]
    return WorkflowProgressionDecision(
        "prepare_next_step",
        wf.id,
        current.id,
        index,
        current.employee,
        next_step.id,
        index + 1,
        next_step.employee,
        "next_step_available",
    )


def _complete(wf: WorkflowDefinition) -> WorkflowProgressionDecision:
    final = wf.steps[-1]
    return WorkflowProgressionDecision(
        "workflow_complete",
        wf.id,
        final.id,
        len(wf.steps),
        final.employee,
        None,
        None,
        None,
        "last_step_succeeded",
    )


def _failure(wf: WorkflowDefinition, index: int = 1) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure", wf.id, step.id, index, step.employee, "api_error"
    )


def _runner_error(call, classification: str) -> None:
    with pytest.raises(ApprovedFreshWorkflowBoundedRunnerCompatibilityError) as caught:
        call()
    assert caught.value.detail.classification == classification
    assert str(caught.value) == (
        "approved fresh workflow bounded runner inputs are incompatible"
    )
    assert "secret" not in str(caught.value)


def _run_step1(tmp_path: Path, wf: WorkflowDefinition, calls: list[str]):
    return route_approved_workflow_fresh_start(
        wf,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        _bootstrap_context(wf, calls),
    )


def test_01_public_api_and_source_audit() -> None:
    signature = inspect.signature(route_approved_fresh_workflow_bounded)
    assert list(signature.parameters) == [
        "workflow",
        "state_path",
        "events_path",
        "bootstrap_context",
        "continuation_contexts",
        "fresh_start_function",
        "bounded_continuation_function",
    ]
    assert signature.parameters["fresh_start_function"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["bounded_continuation_function"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["fresh_start_function"].default is route_approved_workflow_fresh_start
    assert signature.parameters["bounded_continuation_function"].default is route_bounded_approved_workflow_continuation
    assert ApprovedFreshWorkflowBoundedRunnerClassification is not None
    assert issubclass(
        ApprovedFreshWorkflowBoundedRunnerCompatibilityError,
        ApprovedFreshWorkflowBoundedRunnerError,
    )
    assert issubclass(ApprovedFreshWorkflowBoundedRunnerError, ValueError)
    assert ApprovedFreshWorkflowBoundedRunnerFailureDetail("configuration")
    assert ApprovedFreshWorkflowBoundedRunnerCompatibilityError("configuration").detail.classification == "configuration"
    assert {
        "route_approved_fresh_workflow_bounded",
        "ApprovedFreshWorkflowBoundedRunnerClassification",
        "ApprovedFreshWorkflowBoundedRunnerFailureDetail",
        "ApprovedFreshWorkflowBoundedRunnerError",
        "ApprovedFreshWorkflowBoundedRunnerCompatibilityError",
    } == set(
        name
        for name in (
            "route_approved_fresh_workflow_bounded",
            "ApprovedFreshWorkflowBoundedRunnerClassification",
            "ApprovedFreshWorkflowBoundedRunnerFailureDetail",
            "ApprovedFreshWorkflowBoundedRunnerError",
            "ApprovedFreshWorkflowBoundedRunnerCompatibilityError",
        )
        if hasattr(__import__("ai_office.engine", fromlist=[name]), name)
    )
    value = _opaque_context()
    with pytest.raises(FrozenInstanceError):
        value.employee = object()  # type: ignore[misc]
    tree = ast.parse(_MODULE_SOURCE)
    assert not any(
        isinstance(node, (ast.For, ast.While, ast.AsyncFor, ast.AsyncFunctionDef))
        for node in ast.walk(tree)
    )
    assert _MODULE_SOURCE.count("fresh_start_function(") == 1
    assert _MODULE_SOURCE.count("bounded_continuation_function(") == 1
    assert "route_approved_workflow_continuation_cycle" not in _MODULE_SOURCE
    assert "ApprovedWorkflowContinuationContext(" not in _MODULE_SOURCE
    for forbidden in ("retry", "rollback", "restore", "lookup", "scheduler"):
        assert forbidden not in _MODULE_SOURCE.lower()


def test_02_context_container_rejected_before_phase208(tmp_path: Path) -> None:
    wf = workflow()
    context = _opaque_context()
    fresh_calls: list[object] = []
    bounded_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return _prepare(wf)

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return _complete(wf)

    class TupleSubclass(tuple):
        pass

    for value in ([context], TupleSubclass((context,))):
        _runner_error(
            lambda value=value: route_approved_fresh_workflow_bounded(
                wf,
                tmp_path / "state",
                tmp_path / "events",
                object(),
                value,
                fresh_start_function=fresh,
                bounded_continuation_function=bounded,
            ),
            "contexts_type",
        )
    assert fresh_calls == []
    assert bounded_calls == []
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "events").exists()


def test_03_context_elements_rejected_before_phase208(tmp_path: Path) -> None:
    wf = workflow()
    fresh_calls: list[object] = []
    bounded_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return _prepare(wf)

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return _complete(wf)

    class ContextSubclass(ApprovedWorkflowContinuationContext):
        pass

    substitute = SimpleNamespace(
        preparation_approval=object(),
        employee=object(),
        resolved_tools=object(),
        api_key=object(),
        execution_approval=object(),
        transport=object(),
    )
    values = (
        (object(),),
        (ContextSubclass(*astuple(_opaque_context())),),
        (substitute,),
    )
    for contexts in values:
        _runner_error(
            lambda contexts=contexts: route_approved_fresh_workflow_bounded(
                wf,
                tmp_path / "state",
                tmp_path / "events",
                object(),
                contexts,
                fresh_start_function=fresh,
                bounded_continuation_function=bounded,
            ),
            "context_type",
        )
    assert fresh_calls == []
    assert bounded_calls == []
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "events").exists()


def test_04_dependencies_must_be_callable_before_phase208(tmp_path: Path) -> None:
    wf = workflow()
    contexts = (_opaque_context(),)
    fresh_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return _prepare(wf)

    _runner_error(
        lambda: route_approved_fresh_workflow_bounded(
            wf,
            tmp_path / "state",
            tmp_path / "events",
            object(),
            contexts,
            fresh_start_function=None,
            bounded_continuation_function=lambda *args: _complete(wf),
        ),
        "configuration",
    )
    _runner_error(
        lambda: route_approved_fresh_workflow_bounded(
            wf,
            tmp_path / "state",
            tmp_path / "events",
            object(),
            contexts,
            fresh_start_function=fresh,
            bounded_continuation_function=None,
        ),
        "configuration",
    )
    assert fresh_calls == []
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "events").exists()


def test_05_phase208_is_called_once_with_canonical_arguments(tmp_path: Path) -> None:
    wf = workflow()
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    bootstrap = object()
    contexts = (_opaque_context(),)
    fresh_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    bounded_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    fresh_result = _prepare(wf)
    bounded_result = _complete(wf)

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return fresh_result

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return bounded_result

    result = route_approved_fresh_workflow_bounded(
        wf,
        state_path,
        events_path,
        bootstrap,
        contexts,
        fresh_start_function=fresh,
        bounded_continuation_function=bounded,
    )
    assert result is bounded_result
    assert fresh_calls == [((wf, state_path, events_path, bootstrap), {})]
    assert bounded_calls == [((fresh_result, wf, state_path, events_path, contexts), {})]


def test_06_phase208_complete_returns_identity_and_skips_phase192() -> None:
    wf = workflow()
    fresh_result = _complete(wf)
    fresh_calls: list[object] = []
    bounded_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return fresh_result

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        raise AssertionError("Phase192 must not be called")

    bomb = object()
    contexts = (
        ApprovedWorkflowContinuationContext(bomb, bomb, bomb, bomb, bomb, bomb),
    )
    result = route_approved_fresh_workflow_bounded(
        wf,
        object(),
        object(),
        bomb,
        contexts,
        fresh_start_function=fresh,
        bounded_continuation_function=bounded,
    )
    assert result is fresh_result
    assert len(fresh_calls) == 1
    assert bounded_calls == []


def test_07_phase208_failure_returns_identity_and_skips_phase192() -> None:
    wf = workflow()
    fresh_result = _failure(wf, 1)
    bounded_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        return fresh_result

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        raise AssertionError("Phase192 must not be called")

    result = route_approved_fresh_workflow_bounded(
        wf,
        object(),
        object(),
        object(),
        (_opaque_context(),),
        fresh_start_function=fresh,
        bounded_continuation_function=bounded,
    )
    assert result is fresh_result
    assert bounded_calls == []


def test_08_prepare_delegates_phase192_once(tmp_path: Path) -> None:
    wf = workflow()
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    bootstrap = object()
    contexts = (_opaque_context(),)
    fresh_result = _prepare(wf)
    bounded_result = _failure(wf, 2)
    fresh_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    bounded_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return fresh_result

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return bounded_result

    result = route_approved_fresh_workflow_bounded(
        wf,
        state_path,
        events_path,
        bootstrap,
        contexts,
        fresh_start_function=fresh,
        bounded_continuation_function=bounded,
    )
    assert result is bounded_result
    assert fresh_calls == [((wf, state_path, events_path, bootstrap), {})]
    assert bounded_calls == [((fresh_result, wf, state_path, events_path, contexts), {})]


def test_09_malformed_phase208_output_is_local_failure_without_retry(tmp_path: Path) -> None:
    wf = workflow()
    contexts = (_opaque_context(),)
    fresh_calls: list[object] = []
    bounded_calls: list[object] = []
    owned_state = tmp_path / "state"
    owned_events = tmp_path / "events"
    malformed = replace(_prepare(wf), workflow_id="wrong-workflow")

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        owned_state.write_bytes(b"fresh-owned-state")
        owned_events.write_bytes(b"fresh-owned-events")
        return malformed

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return _complete(wf)

    _runner_error(
        lambda: route_approved_fresh_workflow_bounded(
            wf,
            owned_state,
            owned_events,
            object(),
            contexts,
            fresh_start_function=fresh,
            bounded_continuation_function=bounded,
        ),
        "fresh_start_contract",
    )
    assert len(fresh_calls) == 1
    assert bounded_calls == []
    assert owned_state.read_bytes() == b"fresh-owned-state"
    assert owned_events.read_bytes() == b"fresh-owned-events"

    for value in (
        object(),
        replace(_prepare(wf), reason="wrong-reason"),
        replace(_failure(wf), failure_category="not-a-category"),
    ):
        _runner_error(
            lambda value=value: route_approved_fresh_workflow_bounded(
                wf,
                tmp_path / "other-state",
                tmp_path / "other-events",
                object(),
                contexts,
                fresh_start_function=lambda *args, value=value: value,
                bounded_continuation_function=bounded,
            ),
            "fresh_start_contract",
        )
    assert bounded_calls == []


def test_10_unexpected_phase208_error_is_sanitized_without_rollback(tmp_path: Path) -> None:
    wf = workflow()
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    fresh_calls: list[object] = []
    bounded_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        state_path.write_bytes(b"dependency-owned-state")
        events_path.write_bytes(b"dependency-owned-events")
        raise RuntimeError("secret provider payload")

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return _complete(wf)

    _runner_error(
        lambda: route_approved_fresh_workflow_bounded(
            wf,
            state_path,
            events_path,
            object(),
            (_opaque_context(),),
            fresh_start_function=fresh,
            bounded_continuation_function=bounded,
        ),
        "dependency_error",
    )
    assert len(fresh_calls) == 1
    assert bounded_calls == []
    assert state_path.read_bytes() == b"dependency-owned-state"
    assert events_path.read_bytes() == b"dependency-owned-events"


def test_11_default_phase208_safe_errors_keep_durable_ownership(tmp_path: Path) -> None:
    wf = workflow(2)
    contexts = (_opaque_context(),)
    bounded_calls: list[object] = []

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return _complete(wf)

    preexisting_state = tmp_path / "preexisting-state"
    preexisting_events = tmp_path / "preexisting-events"
    preexisting_state.write_bytes(b"preexisting-state")
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError) as preexisting_error:
        route_approved_fresh_workflow_bounded(
            wf,
            preexisting_state,
            preexisting_events,
            _bootstrap_context(wf),
            contexts,
            bounded_continuation_function=bounded,
        )
    assert preexisting_error.value.detail.classification == "target_exists"
    assert preexisting_state.read_bytes() == b"preexisting-state"
    assert not preexisting_events.exists()

    ready_state = tmp_path / "ready-state"
    ready_events = tmp_path / "ready-events"
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError) as ready_error:
        route_approved_fresh_workflow_bounded(
            wf,
            ready_state,
            ready_events,
            _bootstrap_context(wf, preparation_approval=object()),
            contexts,
            bounded_continuation_function=bounded,
        )
    assert ready_error.value.detail.classification == "preparation_approval"
    assert load_workflow_execution_state(ready_state).status == "ready"
    assert ready_events.read_bytes() == b""

    running_state = tmp_path / "running-state"
    running_events = tmp_path / "running-events"
    with pytest.raises(PersistedStartExecutionCompatibilityError) as running_error:
        route_approved_fresh_workflow_bounded(
            wf,
            running_state,
            running_events,
            _bootstrap_context(wf, execution_approval=object()),
            contexts,
            bounded_continuation_function=bounded,
        )
    assert running_error.value.detail.classification == "request_data"
    running = load_workflow_execution_state(running_state)
    assert running == WorkflowExecutionState(
        wf.id, "running", wf.steps[0].id, 1, wf.steps[0].employee, (), None
    )
    assert running_events.read_bytes() == b""
    assert bounded_calls == []


def test_12_malformed_phase192_output_is_local_failure_without_retry(tmp_path: Path) -> None:
    wf = workflow()
    fresh_result = _prepare(wf)
    contexts = (_opaque_context(),)

    class DecisionSubclass(WorkflowProgressionDecision):
        pass

    malformed_values = (
        object(),
        replace(_complete(wf), current_step_id="wrong-step"),
        DecisionSubclass(*astuple(_complete(wf))),
    )
    for index, malformed in enumerate(malformed_values):
        state_path = tmp_path / f"state-{index}"
        events_path = tmp_path / f"events-{index}"
        fresh_calls: list[object] = []
        bounded_calls: list[object] = []

        def fresh(*args: object, **kwargs: object) -> object:
            fresh_calls.append((args, kwargs))
            return fresh_result

        def bounded(*args: object, malformed=malformed, **kwargs: object) -> object:
            bounded_calls.append((args, kwargs))
            state_path.write_bytes(b"phase192-owned-state")
            events_path.write_bytes(b"phase192-owned-events")
            return malformed

        _runner_error(
            lambda: route_approved_fresh_workflow_bounded(
                wf,
                state_path,
                events_path,
                object(),
                contexts,
                fresh_start_function=fresh,
                bounded_continuation_function=bounded,
            ),
            "bounded_continuation_contract",
        )
        assert len(fresh_calls) == 1
        assert len(bounded_calls) == 1
        assert state_path.read_bytes() == b"phase192-owned-state"
        assert events_path.read_bytes() == b"phase192-owned-events"


def test_13_unexpected_phase192_error_is_sanitized_without_rollback(tmp_path: Path) -> None:
    wf = workflow()
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    fresh_calls: list[object] = []
    bounded_calls: list[object] = []
    fresh_result = _prepare(wf)

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return fresh_result

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        state_path.write_bytes(b"phase192-owned-state")
        events_path.write_bytes(b"phase192-owned-events")
        raise RuntimeError("secret lower dependency error")

    _runner_error(
        lambda: route_approved_fresh_workflow_bounded(
            wf,
            state_path,
            events_path,
            object(),
            (_opaque_context(),),
            fresh_start_function=fresh,
            bounded_continuation_function=bounded,
        ),
        "dependency_error",
    )
    assert len(fresh_calls) == 1
    assert len(bounded_calls) == 1
    assert state_path.read_bytes() == b"phase192-owned-state"
    assert events_path.read_bytes() == b"phase192-owned-events"


def test_14_default_phase192_safe_errors_keep_later_ownership(tmp_path: Path) -> None:
    wf = workflow(3)
    prep_directory = tmp_path / "preparation"
    prep_directory.mkdir()
    prep_step1_calls: list[str] = []
    prep_step2_calls: list[str] = []
    prep_later_calls: list[str] = []
    bad_preparation = _continuation_context(
        wf, 2, prep_step2_calls, preparation_approval=object()
    )
    bad_preparation = replace(
        bad_preparation,
        transport=_failure_transport(prep_step2_calls, "step-2"),
    )
    later_preparation = replace(
        _continuation_context(wf, 3, prep_later_calls),
        transport=_failure_transport(prep_later_calls, "step-3"),
    )
    with pytest.raises(Phase145Error) as preparation_error:
        route_approved_fresh_workflow_bounded(
            wf,
            prep_directory / "state.json",
            prep_directory / "events.jsonl",
            _bootstrap_context(wf, prep_step1_calls),
            (bad_preparation, later_preparation),
            fresh_start_function=route_approved_workflow_fresh_start,
            bounded_continuation_function=route_bounded_approved_workflow_continuation,
        )
    assert type(preparation_error.value) is Phase145CompatibilityError
    assert not isinstance(
        preparation_error.value, ApprovedFreshWorkflowBoundedRunnerError
    )
    assert preparation_error.value.detail.classification == "approval_contract"
    assert prep_step1_calls == ["step-1"]
    assert prep_step2_calls == []
    assert prep_later_calls == []
    preparation_state = load_workflow_execution_state(
        prep_directory / "state.json"
    )
    assert preparation_state == WorkflowExecutionState(
        wf.id,
        "succeeded",
        wf.steps[0].id,
        1,
        wf.steps[0].employee,
        (wf.steps[0].id,),
        None,
    )
    assert (prep_directory / "state.json").read_bytes() == (
        serialize_workflow_execution_state_json(preparation_state).encode()
    )
    preparation_history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            prep_directory / "state.json", prep_directory / "events.jsonl"
        )
    )
    assert len(preparation_history.events) == 1
    expected_step1_event = RuntimeStepEvent(
        "step_succeeded",
        wf.id,
        wf.steps[0].id,
        1,
        wf.steps[0].employee,
        "running",
        "succeeded",
        "openai",
        None,
        "response-step-1",
        "request-step-1",
        "ok",
        None,
    )
    assert preparation_history.events == (expected_step1_event,)
    assert (prep_directory / "events.jsonl").read_bytes() == (
        serialize_runtime_step_event_jsonl(expected_step1_event).encode()
    )

    execution_directory = tmp_path / "execution"
    execution_directory.mkdir()
    execution_step1_calls: list[str] = []
    execution_step2_calls: list[str] = []
    execution_later_calls: list[str] = []
    bad_execution = _continuation_context(
        wf, 2, execution_step2_calls, execution_approval=object()
    )
    bad_execution = replace(
        bad_execution,
        transport=_failure_transport(execution_step2_calls, "step-2"),
    )
    later_execution = replace(
        _continuation_context(wf, 3, execution_later_calls),
        transport=_failure_transport(execution_later_calls, "step-3"),
    )
    with pytest.raises(Phase155Error) as execution_error:
        route_approved_fresh_workflow_bounded(
            wf,
            execution_directory / "state.json",
            execution_directory / "events.jsonl",
            _bootstrap_context(wf, execution_step1_calls),
            (bad_execution, later_execution),
            fresh_start_function=route_approved_workflow_fresh_start,
            bounded_continuation_function=route_bounded_approved_workflow_continuation,
        )
    assert type(execution_error.value) is Phase155CompatibilityError
    assert not isinstance(
        execution_error.value, ApprovedFreshWorkflowBoundedRunnerError
    )
    assert execution_error.value.detail.classification == "approval_contract"
    assert execution_step1_calls == ["step-1"]
    assert execution_step2_calls == []
    assert execution_later_calls == []
    state = load_workflow_execution_state(execution_directory / "state.json")
    assert state == WorkflowExecutionState(
        wf.id,
        "running",
        wf.steps[1].id,
        2,
        wf.steps[1].employee,
        (wf.steps[0].id,),
        None,
    )
    assert (execution_directory / "state.json").read_bytes() == (
        serialize_workflow_execution_state_json(state).encode()
    )
    execution_history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            execution_directory / "state.json",
            execution_directory / "events.jsonl",
        )
    )
    assert len(execution_history.events) == 1
    assert execution_history.events == (expected_step1_event,)
    assert (execution_directory / "events.jsonl").read_bytes() == (
        serialize_runtime_step_event_jsonl(expected_step1_event).encode()
    )


def test_15_result_family_and_linkage_are_strict() -> None:
    wf = workflow(2)
    contexts = (_opaque_context(),)
    prep = _prepare(wf)
    complete = _complete(wf)
    fresh_failed = _failure(wf, 1)
    failed = _failure(wf, 2)

    for terminal in (complete, fresh_failed):
        result = route_approved_fresh_workflow_bounded(
            wf,
            object(),
            object(),
            object(),
            contexts,
            fresh_start_function=lambda *args, terminal=terminal: terminal,
            bounded_continuation_function=lambda *args: object(),
        )
        assert result is terminal

    allowed_categories = tuple(get_args(ModelInvocationFailureCategory))
    for category in allowed_categories:
        fresh_failure = replace(fresh_failed, failure_category=category)
        result = route_approved_fresh_workflow_bounded(
            wf,
            object(),
            object(),
            object(),
            contexts,
            fresh_start_function=lambda *args, result=fresh_failure: result,
            bounded_continuation_function=lambda *args: object(),
        )
        assert result is fresh_failure

        bounded_failure = replace(failed, failure_category=category)
        result = route_approved_fresh_workflow_bounded(
            wf,
            object(),
            object(),
            object(),
            contexts,
            fresh_start_function=lambda *args: prep,
            bounded_continuation_function=lambda *args, result=bounded_failure: result,
        )
        assert result is bounded_failure

    for terminal in (complete, failed):
        result = route_approved_fresh_workflow_bounded(
            wf,
            object(),
            object(),
            object(),
            contexts,
            fresh_start_function=lambda *args: prep,
            bounded_continuation_function=lambda *args, terminal=terminal: terminal,
        )
        assert result is terminal

    class FailureSubclass(PersistedExecutionOutcome):
        pass

    class DecisionSubclass(WorkflowProgressionDecision):
        pass

    malformed_fresh = (
        replace(prep, reason="not-next-step-available"),
        replace(prep, next_step_id="wrong-step"),
        replace(complete, next_step_id="unexpected"),
        replace(fresh_failed, failure_category="unknown"),
        FailureSubclass(*astuple(failed)),
        DecisionSubclass(*astuple(prep)),
    )
    for malformed in malformed_fresh:
        _runner_error(
            lambda malformed=malformed: route_approved_fresh_workflow_bounded(
                wf,
                object(),
                object(),
                object(),
                contexts,
                fresh_start_function=lambda *args, malformed=malformed: malformed,
                bounded_continuation_function=lambda *args: complete,
            ),
            "fresh_start_contract",
        )

    malformed_bounded = (
        replace(complete, reason="not-last-step-succeeded"),
        replace(complete, next_step_index=1),
        replace(failed, current_step_index=1),
        replace(failed, failure_category="unknown"),
        FailureSubclass(*astuple(failed)),
        DecisionSubclass(*astuple(prep)),
    )
    for malformed in malformed_bounded:
        _runner_error(
            lambda malformed=malformed: route_approved_fresh_workflow_bounded(
                wf,
                object(),
                object(),
                object(),
                contexts,
                fresh_start_function=lambda *args: prep,
                bounded_continuation_function=lambda *args, malformed=malformed: malformed,
            ),
            "bounded_continuation_contract",
        )


def test_16_real_default_three_step_success_has_one_call_per_owner_and_step(
    tmp_path: Path,
) -> None:
    wf = workflow(3)
    calls: list[str] = []
    fresh_calls: list[object] = []
    bounded_calls: list[object] = []
    fresh_result: list[object] = []
    bounded_result: list[object] = []
    contexts = (
        _continuation_context(wf, 2, calls),
        _continuation_context(wf, 3, calls),
    )

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        result = route_approved_workflow_fresh_start(*args)
        fresh_result.append(result)
        return result

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        result = route_bounded_approved_workflow_continuation(*args)
        bounded_result.append(result)
        return result

    result = route_approved_fresh_workflow_bounded(
        wf,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        _bootstrap_context(wf, calls),
        contexts,
        fresh_start_function=fresh,
        bounded_continuation_function=bounded,
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_index == 3
    assert len(fresh_calls) == 1
    assert len(bounded_calls) == 1
    assert bounded_calls[0][0][0] is fresh_result[0]
    assert result is bounded_result[0]
    assert calls == ["step-1", "step-2", "step-3"]
    state = load_workflow_execution_state(tmp_path / "state.json")
    assert state.status == "succeeded"
    assert state.completed_step_ids == ("step-1", "step-2", "step-3")
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 3


def test_17_real_default_context_exhaustion_returns_exact_next_prepare(
    tmp_path: Path,
) -> None:
    wf = workflow(3)
    calls: list[str] = []
    result = route_approved_fresh_workflow_bounded(
        wf,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        _bootstrap_context(wf, calls),
        (_continuation_context(wf, 2, calls),),
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "prepare_next_step"
    assert result.current_step_index == 2
    assert result.next_step_id == "step-3"
    assert result.next_step_index == 3
    assert calls == ["step-1", "step-2"]
    state = load_workflow_execution_state(tmp_path / "state.json")
    assert state.status == "succeeded"
    assert state.current_step_index == 2
    assert state.completed_step_ids == ("step-1", "step-2")
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 2


def test_18_real_default_step2_failure_does_not_consume_later_context(
    tmp_path: Path,
) -> None:
    wf = workflow(3)
    calls: list[str] = []
    first = _continuation_context(wf, 2, calls)
    first = replace(first, transport=_failure_transport(calls, "step-2"))
    later = _continuation_context(wf, 3, calls)
    result = route_approved_fresh_workflow_bounded(
        wf,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        _bootstrap_context(wf, calls),
        (first, later),
    )
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    assert result.current_step_index == 2
    assert result.failure_category == "api_error"
    assert calls == ["step-1", "step-2"]
    state = load_workflow_execution_state(tmp_path / "state.json")
    assert state.status == "failed"
    assert state.current_step_index == 2
    assert state.completed_step_ids == ("step-1",)
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 2


def test_19_real_one_step_success_skips_phase192_and_later_opaque_contexts(
    tmp_path: Path,
) -> None:
    wf = workflow(1)
    calls: list[str] = []
    bounded_calls: list[object] = []

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        raise AssertionError("terminal Phase208 result must bypass Phase192")

    result = route_approved_fresh_workflow_bounded(
        wf,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        _bootstrap_context(wf, calls),
        (_opaque_context(), _opaque_context()),
        bounded_continuation_function=bounded,
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_index == 1
    assert calls == ["step-1"]
    assert bounded_calls == []
    assert load_workflow_execution_state(tmp_path / "state.json").completed_step_ids == (
        "step-1",
    )
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 1


def test_20_real_step1_failure_skips_phase192_and_later_opaque_contexts(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    calls: list[str] = []
    bounded_calls: list[object] = []

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        raise AssertionError("terminal Phase208 result must bypass Phase192")

    result = route_approved_fresh_workflow_bounded(
        wf,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        _bootstrap_context(
            wf,
            calls,
            transport=_failure_transport(calls, "step-1"),
        ),
        (_opaque_context(), _opaque_context()),
        bounded_continuation_function=bounded,
    )
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    assert result.current_step_index == 1
    assert result.failure_category == "api_error"
    assert calls == ["step-1"]
    assert bounded_calls == []
    state = load_workflow_execution_state(tmp_path / "state.json")
    assert state.status == "failed"
    assert state.completed_step_ids == ()
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 1
