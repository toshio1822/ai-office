"""Focused Phase-210 fresh-start plus bounded-continuation runner tests.

The public boundary has five business inputs and uses the canonical fresh-start
and bounded-continuation owners directly.  Provider-facing transports below
are deterministic synthetic functions.  Test-only monkeypatching of those
canonical owner names observes malformed/error behavior without restoring a
public dependency-injection seam.
"""

# ruff: noqa: E501,I001

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, astuple, replace
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
import ai_office.engine.approved_workflow_fresh_bounded_runner as runner_module
from ai_office.engine import (
    ApprovedFreshWorkflowBoundedRunnerClassification,
    ApprovedFreshWorkflowBoundedRunnerCompatibilityError,
    ApprovedFreshWorkflowBoundedRunnerError,
    ApprovedWorkflowBootstrapContext,
    ApprovedWorkflowContinuationContext,
    InitialStepPreparationApproval,
    route_approved_fresh_workflow_bounded,
)
from ai_office.engine.approved_workflow_fresh_start import (
    FreshWorkflowBootstrapCompatibilityError,
)
from ai_office.engine.next_step_preparation import NextStepPreparationApproval
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationFailureCategory,
    ModelInvocationRequest,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.runtime import WorkflowExecutionState
from ai_office.runtime.persisted_start_execution import (
    PersistedStartExecutionCompatibilityError,
)
from ai_office.storage import load_workflow_execution_state
from tests._phase260_test_support import synthetic_continuation_facts

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
    upstream = UpstreamStepOutput(
        wf.id,
        wf.steps[index - 2].id,
        index - 1,
        wf.steps[index - 2].employee,
        "ok",
    )
    request = ModelInvocationRequest(
        employee.model,
        employee.instructions,
        wf.steps[index - 1].instructions,
        (),
        (upstream,),
        synthetic_continuation_facts(
            workflow_id=wf.id,
            predecessor_step_id=wf.steps[index - 2].id,
            predecessor_step_index=index - 1,
            predecessor_employee_id=wf.steps[index - 2].employee,
            completed_step_ids=tuple(step.id for step in wf.steps[: index - 1]),
            output_text="ok",
            response_id=f"response-step-{index - 1}",
            request_id=f"request-step-{index - 1}",
            next_step_index=index,
        ),
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


def test_01_public_api_and_removed_dependency_seams() -> None:
    signature = inspect.signature(route_approved_fresh_workflow_bounded)
    assert list(signature.parameters) == [
        "workflow",
        "state_path",
        "events_path",
        "bootstrap_context",
        "continuation_contexts",
    ]
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in signature.parameters.values()
    )
    assert get_args(ApprovedFreshWorkflowBoundedRunnerClassification) == (
        "contexts_type",
        "context_type",
        "fresh_start_contract",
        "bounded_continuation_contract",
    )
    assert issubclass(
        ApprovedFreshWorkflowBoundedRunnerCompatibilityError,
        ApprovedFreshWorkflowBoundedRunnerError,
    )
    assert issubclass(ApprovedFreshWorkflowBoundedRunnerError, ValueError)
    assert {
        "route_approved_fresh_workflow_bounded",
        "ApprovedFreshWorkflowBoundedRunnerClassification",
        "ApprovedFreshWorkflowBoundedRunnerFailureDetail",
        "ApprovedFreshWorkflowBoundedRunnerError",
        "ApprovedFreshWorkflowBoundedRunnerCompatibilityError",
    } == {
        name
        for name in (
            "route_approved_fresh_workflow_bounded",
            "ApprovedFreshWorkflowBoundedRunnerClassification",
            "ApprovedFreshWorkflowBoundedRunnerFailureDetail",
            "ApprovedFreshWorkflowBoundedRunnerError",
            "ApprovedFreshWorkflowBoundedRunnerCompatibilityError",
        )
        if hasattr(__import__("ai_office.engine", fromlist=[name]), name)
    }
    value = _opaque_context()
    with pytest.raises(FrozenInstanceError):
        value.employee = object()  # type: ignore[misc]

    wf = workflow()
    for removed_keyword in ("fresh_start_function", "bounded_continuation_function"):
        with pytest.raises(TypeError):
            route_approved_fresh_workflow_bounded(
                wf,
                object(),
                object(),
                object(),
                (),
                **{removed_keyword: object()},
            )


def test_02_context_container_rejected_before_canonical_fresh_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow()
    context = _opaque_context()
    fresh_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return _prepare(wf)

    monkeypatch.setattr(runner_module, "route_approved_workflow_fresh_start", fresh)

    class TupleSubclass(tuple):
        pass

    for index, contexts in enumerate(([context], TupleSubclass((context,)))):
        _runner_error(
            lambda contexts=contexts, index=index: (
                route_approved_fresh_workflow_bounded(
                    wf,
                    tmp_path / f"state-{index}",
                    tmp_path / f"events-{index}",
                    object(),
                    contexts,
                )
            ),
            "contexts_type",
        )
    assert fresh_calls == []
    assert not any(tmp_path.iterdir())


def test_03_context_elements_rejected_before_canonical_fresh_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow()
    fresh_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return _prepare(wf)

    monkeypatch.setattr(runner_module, "route_approved_workflow_fresh_start", fresh)

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
    for index, contexts in enumerate(values):
        _runner_error(
            lambda contexts=contexts, index=index: (
                route_approved_fresh_workflow_bounded(
                    wf,
                    tmp_path / f"state-{index}",
                    tmp_path / f"events-{index}",
                    object(),
                    contexts,
                )
            ),
            "context_type",
        )
    assert fresh_calls == []
    assert not any(tmp_path.iterdir())


def test_04_canonical_owners_are_used_once_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow()
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    bootstrap = object()
    contexts = (_opaque_context(),)
    fresh_result = _prepare(wf)
    bounded_result = _complete(wf)
    fresh_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    bounded_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        return fresh_result

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return bounded_result

    monkeypatch.setattr(runner_module, "route_approved_workflow_fresh_start", fresh)
    monkeypatch.setattr(
        runner_module, "route_bounded_approved_workflow_continuation", bounded
    )

    result = route_approved_fresh_workflow_bounded(
        wf, state_path, events_path, bootstrap, contexts
    )
    assert result is bounded_result
    assert fresh_calls == [((wf, state_path, events_path, bootstrap), {})]
    assert bounded_calls == [
        ((fresh_result, wf, state_path, events_path, contexts), {})
    ]


def test_05_fresh_terminal_results_short_circuit_bounded_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf = workflow()
    contexts = (_opaque_context(), _opaque_context())

    for terminal in (_complete(wf), _failure(wf, 1)):
        fresh_calls: list[object] = []
        bounded_calls: list[object] = []

        def fresh(*args: object, terminal=terminal, **kwargs: object) -> object:
            fresh_calls.append((args, kwargs))
            return terminal

        def bounded(*args: object, **kwargs: object) -> object:
            bounded_calls.append((args, kwargs))
            raise AssertionError("bounded continuation must not be called")

        monkeypatch.setattr(runner_module, "route_approved_workflow_fresh_start", fresh)
        monkeypatch.setattr(
            runner_module, "route_bounded_approved_workflow_continuation", bounded
        )
        result = route_approved_fresh_workflow_bounded(
            wf, object(), object(), object(), contexts
        )
        assert result is terminal
        assert len(fresh_calls) == 1
        assert bounded_calls == []


def test_06_fresh_result_validation_preserves_owner_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow()
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    malformed = replace(_prepare(wf), workflow_id="wrong-workflow")
    fresh_calls: list[object] = []
    bounded_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        state_path.write_bytes(b"fresh-owned-state")
        events_path.write_bytes(b"fresh-owned-events")
        return malformed

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        return _complete(wf)

    monkeypatch.setattr(runner_module, "route_approved_workflow_fresh_start", fresh)
    monkeypatch.setattr(
        runner_module, "route_bounded_approved_workflow_continuation", bounded
    )
    _runner_error(
        lambda: route_approved_fresh_workflow_bounded(
            wf, state_path, events_path, object(), (_opaque_context(),)
        ),
        "fresh_start_contract",
    )
    assert len(fresh_calls) == 1
    assert bounded_calls == []
    assert state_path.read_bytes() == b"fresh-owned-state"
    assert events_path.read_bytes() == b"fresh-owned-events"


def test_07_fresh_owner_errors_propagate_without_outer_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow()
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    fresh_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        fresh_calls.append((args, kwargs))
        state_path.write_bytes(b"fresh-owned-state")
        events_path.write_bytes(b"fresh-owned-events")
        raise RuntimeError("canonical fresh owner failure")

    monkeypatch.setattr(runner_module, "route_approved_workflow_fresh_start", fresh)
    with pytest.raises(RuntimeError, match="canonical fresh owner failure"):
        route_approved_fresh_workflow_bounded(
            wf, state_path, events_path, object(), (_opaque_context(),)
        )
    assert len(fresh_calls) == 1
    assert state_path.read_bytes() == b"fresh-owned-state"
    assert events_path.read_bytes() == b"fresh-owned-events"


def test_08_bounded_result_validation_preserves_owner_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow()
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    fresh_result = _prepare(wf)
    malformed = replace(_complete(wf), current_step_id="wrong-step")
    bounded_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        return fresh_result

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        state_path.write_bytes(b"bounded-owned-state")
        events_path.write_bytes(b"bounded-owned-events")
        return malformed

    monkeypatch.setattr(runner_module, "route_approved_workflow_fresh_start", fresh)
    monkeypatch.setattr(
        runner_module, "route_bounded_approved_workflow_continuation", bounded
    )
    _runner_error(
        lambda: route_approved_fresh_workflow_bounded(
            wf, state_path, events_path, object(), (_opaque_context(),)
        ),
        "bounded_continuation_contract",
    )
    assert len(bounded_calls) == 1
    assert state_path.read_bytes() == b"bounded-owned-state"
    assert events_path.read_bytes() == b"bounded-owned-events"


def test_09_bounded_owner_errors_propagate_without_retry_or_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow()
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    bounded_calls: list[object] = []

    def fresh(*args: object, **kwargs: object) -> object:
        return _prepare(wf)

    def bounded(*args: object, **kwargs: object) -> object:
        bounded_calls.append((args, kwargs))
        state_path.write_bytes(b"bounded-owned-state")
        events_path.write_bytes(b"bounded-owned-events")
        raise RuntimeError("canonical bounded owner failure")

    monkeypatch.setattr(runner_module, "route_approved_workflow_fresh_start", fresh)
    monkeypatch.setattr(
        runner_module, "route_bounded_approved_workflow_continuation", bounded
    )
    with pytest.raises(RuntimeError, match="canonical bounded owner failure"):
        route_approved_fresh_workflow_bounded(
            wf, state_path, events_path, object(), (_opaque_context(),)
        )
    assert len(bounded_calls) == 1
    assert state_path.read_bytes() == b"bounded-owned-state"
    assert events_path.read_bytes() == b"bounded-owned-events"


def test_10_result_family_linkage_and_failure_categories_remain_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wf = workflow(2)
    contexts = (_opaque_context(),)
    preparation = _prepare(wf)

    for category in get_args(ModelInvocationFailureCategory):
        fresh_failure = replace(_failure(wf, 1), failure_category=category)
        monkeypatch.setattr(
            runner_module,
            "route_approved_workflow_fresh_start",
            lambda *args, result=fresh_failure, **kwargs: result,
        )
        bounded_calls: list[object] = []

        def bounded(*args: object, **kwargs: object) -> object:
            bounded_calls.append((args, kwargs))
            raise AssertionError("fresh failure must short-circuit")

        monkeypatch.setattr(
            runner_module, "route_bounded_approved_workflow_continuation", bounded
        )
        assert (
            route_approved_fresh_workflow_bounded(
                wf, object(), object(), object(), contexts
            )
            is fresh_failure
        )
        assert bounded_calls == []

        bounded_failure = replace(_failure(wf, 2), failure_category=category)
        monkeypatch.setattr(
            runner_module,
            "route_approved_workflow_fresh_start",
            lambda *args: preparation,
        )
        monkeypatch.setattr(
            runner_module,
            "route_bounded_approved_workflow_continuation",
            lambda *args, result=bounded_failure: result,
        )
        assert (
            route_approved_fresh_workflow_bounded(
                wf, object(), object(), object(), contexts
            )
            is bounded_failure
        )

    malformed = replace(preparation, workflow_id="wrong-workflow")
    monkeypatch.setattr(
        runner_module,
        "route_approved_workflow_fresh_start",
        lambda *args: malformed,
    )
    _runner_error(
        lambda: route_approved_fresh_workflow_bounded(
            wf, object(), object(), object(), contexts
        ),
        "fresh_start_contract",
    )


def test_11_default_fresh_safe_errors_keep_durable_ownership(tmp_path: Path) -> None:
    wf = workflow(2)
    contexts = (_opaque_context(),)

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
        )
    assert running_error.value.detail.classification == "request_data"
    assert load_workflow_execution_state(running_state) == WorkflowExecutionState(
        wf.id, "running", wf.steps[0].id, 1, wf.steps[0].employee, (), None
    )
    assert running_events.read_bytes() == b""


def test_12_real_default_three_step_success_is_bounded_and_durable(
    tmp_path: Path,
) -> None:
    wf = workflow(3)
    calls: list[str] = []
    contexts = (
        _continuation_context(wf, 2, calls),
        _continuation_context(wf, 3, calls),
    )

    result = route_approved_fresh_workflow_bounded(
        wf,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        _bootstrap_context(wf, calls),
        contexts,
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_index == 3
    assert calls == ["step-1", "step-2", "step-3"]
    state = load_workflow_execution_state(tmp_path / "state.json")
    assert state.status == "succeeded"
    assert state.completed_step_ids == ("step-1", "step-2", "step-3")
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 3


def test_13_real_default_context_exhaustion_returns_exact_next_prepare(
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


def test_14_real_default_step2_failure_does_not_consume_later_context(
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


def test_15_real_one_step_success_skips_bounded_continuation_and_contexts(
    tmp_path: Path,
) -> None:
    wf = workflow(1)
    calls: list[str] = []
    result = route_approved_fresh_workflow_bounded(
        wf,
        tmp_path / "state.json",
        tmp_path / "events.jsonl",
        _bootstrap_context(wf, calls),
        (_opaque_context(), _opaque_context()),
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_index == 1
    assert calls == ["step-1"]
    assert load_workflow_execution_state(
        tmp_path / "state.json"
    ).completed_step_ids == ("step-1",)
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 1


def test_16_real_step1_failure_skips_bounded_continuation_and_contexts(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    calls: list[str] = []
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
    )
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    assert result.current_step_index == 1
    assert result.failure_category == "api_error"
    assert calls == ["step-1"]
    state = load_workflow_execution_state(tmp_path / "state.json")
    assert state.status == "failed"
    assert state.completed_step_ids == ()
    assert len((tmp_path / "events.jsonl").read_text().splitlines()) == 1
