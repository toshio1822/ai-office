"""Focused Phase-212 persisted-terminal bounded-resume tests.

The Phase-212 boundary has four inputs; its persisted-target composition uses
Phase 38's three-business-input canonical classification and routing owner
before the Phase-192 handoff. Test-only monkeypatching of those canonical
boundaries observes malformed/error behavior without restoring a public
dependency-injection seam. Real-route cases use deterministic synthetic
transports only; no provider, network, or paid API is called.

Requirement-to-test mapping:
- public API and removed seams -> test_01_public_api_and_removed_dependency_seams
- single execution and prepare handoff -> test_02_prepare_result_advances_once_without_retry_or_duplicate_execution
- deferred context validation -> test_03_prepare_context_validation_stops_before_bounded_execution
- minimum result-family/discriminator guards -> test_04_minimum_result_guards_only
- persisted-failure terminal routing -> test_05_persisted_failure_is_value_terminal and test_06_persisted_failure_terminal_ignores_malformed_contexts
- final-success terminal stop -> test_07_final_success_terminal_ignores_malformed_contexts
- restart, finite contexts, and no replay -> test_08_restart_success_does_not_replay_completed_steps through test_11_ready_and_running_are_rejected_without_replay
- lower-owner errors and read-only behavior -> test_12_corrupt_or_mismatched_history_stays_lower_owned and test_13_default_lower_safe_errors_and_read_only_targets_are_preserved
- durable ownership and no outer rollback -> test_14_phase192_owned_change_is_never_rolled_back
"""

# ruff: noqa: E501,F401,F811,I001

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedWorkflowContinuationContext,
    PersistedExecutionOutcome,
    PersistedExecutionOutcomeCompatibilityError,
    PersistedExecutionOutcomeRoutingCompatibilityError,
    PersistedTerminalWorkflowBoundedRunnerCompatibilityError,
    PersistedTerminalWorkflowBoundedRunnerError,
    WorkflowProgressionDecision,
    route_bounded_approved_workflow_continuation,
    route_persisted_terminal_workflow_bounded,
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
    ModelInvocationRequest,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey, OpenAIResponsesRawHttpResponse
from ai_office.runtime import (
    RuntimeStepEvent,
    WorkflowExecutionState,
    WorkflowRunBinding,
)
from ai_office.storage import (
    WorkflowExecutionLoadError,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from tests._phase260_test_support import synthetic_continuation_facts
from tests._run_test_support import TestRun as RunFixture, create_test_run


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


def _failure_outcome(
    workflow: WorkflowDefinition,
    index: int = 1,
    category: str = "api_error",
    *,
    binding: WorkflowRunBinding | None = None,
) -> PersistedExecutionOutcome:
    step = workflow.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure",
        workflow.id,
        step.id,
        index,
        step.employee,
        category,
        binding=binding,
    )


def _prepare(
    workflow: WorkflowDefinition,
    index: int = 1,
    *,
    binding: WorkflowRunBinding | None = None,
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
        binding=binding,
    )


def _test_run(
    tmp_path: Path, workflow: WorkflowDefinition, run_id: str = "run-terminal"
) -> RunFixture:
    return create_test_run(
        tmp_path,
        run_id,
        workflow,
        tuple(
            _employee(workflow, index) for index in range(1, len(workflow.steps) + 1)
        ),
    )


def _run_for_paths(state_path: Path, workflow: WorkflowDefinition) -> RunFixture:
    run_id = state_path.name.removesuffix(".state.json")
    return _test_run(state_path.parent, workflow, run_id)


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
    run: RunFixture | None = None,
) -> ApprovedWorkflowBootstrapContext:
    step = workflow.steps[0]
    employee = _employee(workflow, 1)
    request = ModelInvocationRequest(
        employee.model,
        employee.instructions,
        step.instructions,
        (),
        binding=None if run is None else run.binding,
        run_input=None if run is None else f"input-{run.binding.run_id}",
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
        binding=None if run is None else run.binding,
        manifest_store=None if run is None else run.store,
    )


def _continuation_context(
    workflow: WorkflowDefinition,
    index: int,
    calls: list[str],
    *,
    status: int = 200,
    run: RunFixture | None = None,
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
        synthetic_continuation_facts(
            workflow_id=workflow.id,
            predecessor_step_id=workflow.steps[index - 2].id,
            predecessor_step_index=index - 1,
            predecessor_employee_id=workflow.steps[index - 2].employee,
            completed_step_ids=tuple(step.id for step in workflow.steps[: index - 1]),
            output_text="ok",
            response_id=f"response-step-{index - 1}",
            request_id=f"request-step-{index - 1}",
            next_step_index=index,
            binding=None if run is None else run.binding,
        ),
        binding=None if run is None else run.binding,
        run_input=None if run is None else f"input-{run.binding.run_id}",
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
) -> tuple[Path, Path, RunFixture]:
    run = _test_run(tmp_path, workflow)
    state_path, events_path = run.state_path, run.events_path
    first = route_approved_workflow_fresh_start(
        workflow,
        state_path,
        events_path,
        _bootstrap_context(workflow, calls, run=run),
    )
    assert type(first) is WorkflowProgressionDecision
    assert first.decision == "prepare_next_step"
    second = route_bounded_approved_workflow_continuation(
        first,
        workflow,
        state_path,
        events_path,
        (_continuation_context(workflow, 2, calls, run=run),),
    )
    assert type(second) is WorkflowProgressionDecision
    assert second.decision == "prepare_next_step"
    assert second.current_step_index == 2
    return state_path, events_path, run


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
    with pytest.raises(
        PersistedTerminalWorkflowBoundedRunnerCompatibilityError
    ) as caught:
        call()
    assert caught.value.detail.classification == classification
    assert str(caught.value)
    assert "secret" not in str(caught.value)


def test_01_public_api_and_removed_dependency_seams() -> None:
    signature = inspect.signature(route_persisted_terminal_workflow_bounded)
    assert list(signature.parameters) == [
        "workflow",
        "state_path",
        "events_path",
        "continuation_contexts",
    ]
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in signature.parameters.values()
    )
    assert issubclass(
        PersistedTerminalWorkflowBoundedRunnerCompatibilityError,
        PersistedTerminalWorkflowBoundedRunnerError,
    )
    assert issubclass(PersistedTerminalWorkflowBoundedRunnerError, ValueError)

    workflow = _workflow(1)
    for removed_keyword in (
        "classification_function",
        "routing_function",
        "bounded_continuation_function",
    ):
        with pytest.raises(TypeError):
            route_persisted_terminal_workflow_bounded(
                workflow,
                object(),
                object(),
                (),
                **{removed_keyword: object()},
            )


def test_02_prepare_result_advances_once_without_retry_or_duplicate_execution(
    tmp_path: Path,
) -> None:
    workflow = _workflow(4)
    calls: list[str] = []
    state_path, events_path, run = _seed_two_step_prefix(tmp_path, workflow, calls)

    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        (
            _continuation_context(workflow, 3, calls, run=run),
            _continuation_context(workflow, 4, calls, run=run),
        ),
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_index == 4
    assert calls == ["step-1", "step-2", "step-3", "step-4"]
    event_ids = [
        json.loads(line)["step_id"] for line in events_path.read_text().splitlines()
    ]
    assert event_ids == ["step-1", "step-2", "step-3", "step-4"]
    assert load_workflow_execution_state(state_path).completed_step_ids == (
        "step-1",
        "step-2",
        "step-3",
        "step-4",
    )


def test_03_prepare_context_validation_stops_before_bounded_execution(
    tmp_path: Path,
) -> None:
    workflow = _workflow(4)
    context = _opaque_context()

    class TupleSubclass(tuple):
        pass

    cases = (
        ([], "contexts_type"),
        (TupleSubclass((context,)), "contexts_type"),
        ((context, object()), "context_type"),
    )
    for index, (contexts, classification_name) in enumerate(cases):
        directory = tmp_path / str(index)
        directory.mkdir()
        calls: list[str] = []
        state_path, events_path, run = _seed_two_step_prefix(directory, workflow, calls)
        before = (state_path.read_bytes(), events_path.read_bytes())
        _assert_runner_error(
            lambda contexts=contexts: route_persisted_terminal_workflow_bounded(
                workflow,
                state_path,
                events_path,
                contexts,
            ),
            classification_name,
        )
        assert calls == ["step-1", "step-2"]
        assert (state_path.read_bytes(), events_path.read_bytes()) == before


def test_04_minimum_result_guards_only(monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = _workflow(2)
    state_path, events_path = object(), object()
    import ai_office.engine.persisted_terminal_workflow_bounded_runner as runner_module

    malformed_failure = PersistedExecutionOutcome(
        "persisted_failure", "other", "other", 0, "other", "not-a-category"
    )
    monkeypatch.setattr(
        runner_module,
        "route_persisted_execution_outcome_reentry",
        lambda *_args: malformed_failure,
    )
    assert (
        route_persisted_terminal_workflow_bounded(
            workflow, state_path, events_path, object()
        )
        == malformed_failure
    )

    malformed_prepare = WorkflowProgressionDecision(
        "prepare_next_step",
        "other",
        "other",
        0,
        "other",
        "other",
        -1,
        "other",
        "not-owned-by-phase-212",
    )
    malformed_bounded = WorkflowProgressionDecision(
        "workflow_complete",
        "other",
        "other",
        0,
        "other",
        "still-present",
        -1,
        "other",
        "not-owned-by-phase-212",
    )
    monkeypatch.setattr(
        runner_module,
        "route_persisted_execution_outcome_reentry",
        lambda *_args: malformed_prepare,
    )
    monkeypatch.setattr(
        runner_module,
        "route_bounded_approved_workflow_continuation",
        lambda *_args: malformed_bounded,
    )
    assert (
        route_persisted_terminal_workflow_bounded(
            workflow, state_path, events_path, (_opaque_context(),)
        )
        == malformed_bounded
    )

    monkeypatch.setattr(
        runner_module,
        "route_persisted_execution_outcome_reentry",
        lambda *_args: object(),
    )
    _assert_runner_error(
        lambda: route_persisted_terminal_workflow_bounded(
            workflow, state_path, events_path, object()
        ),
        "routing_contract",
    )

    monkeypatch.setattr(
        runner_module,
        "route_persisted_execution_outcome_reentry",
        lambda *_args: _prepare(workflow),
    )
    monkeypatch.setattr(
        runner_module,
        "route_bounded_approved_workflow_continuation",
        lambda *_args: object(),
    )
    _assert_runner_error(
        lambda: route_persisted_terminal_workflow_bounded(
            workflow, state_path, events_path, (_opaque_context(),)
        ),
        "bounded_continuation_contract",
    )


def test_05_persisted_failure_is_value_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _workflow(2)
    failure = _failure_outcome(workflow)
    import ai_office.engine.persisted_terminal_workflow_bounded_runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "route_persisted_execution_outcome_reentry",
        lambda *_args: failure,
    )
    monkeypatch.setattr(
        runner_module,
        "route_bounded_approved_workflow_continuation",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("terminal failure must bypass bounded continuation")
        ),
    )
    assert (
        route_persisted_terminal_workflow_bounded(
            workflow, object(), object(), object()
        )
        == failure
    )


def test_06_persisted_failure_terminal_ignores_malformed_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(1)
    calls: list[str] = []
    run = _test_run(tmp_path, workflow, "run-failure")
    state_path, events_path = run.state_path, run.events_path
    result = route_approved_workflow_fresh_start(
        workflow,
        state_path,
        events_path,
        _bootstrap_context(workflow, calls, status=500, run=run),
    )
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    before = (state_path.read_bytes(), events_path.read_bytes())
    bounded_calls: list[str] = []

    import ai_office.engine.persisted_terminal_workflow_bounded_runner as runner_module

    def bounded(*_: object) -> object:
        bounded_calls.append("bounded")
        raise AssertionError("terminal failure must bypass bounded continuation")

    monkeypatch.setattr(
        runner_module, "route_bounded_approved_workflow_continuation", bounded
    )
    final = route_persisted_terminal_workflow_bounded(
        workflow, state_path, events_path, [object(), object()]
    )
    assert type(final) is PersistedExecutionOutcome
    assert final.outcome == "persisted_failure"
    assert final.current_step_index == 1
    assert bounded_calls == []
    assert (state_path.read_bytes(), events_path.read_bytes()) == before
    assert calls == ["step-1"]


def test_07_final_success_terminal_ignores_malformed_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(4)
    calls: list[str] = []
    state_path, events_path, run = _seed_two_step_prefix(tmp_path, workflow, calls)
    completed = route_bounded_approved_workflow_continuation(
        _prepare(workflow, 2, binding=run.binding),
        workflow,
        state_path,
        events_path,
        (
            _continuation_context(workflow, 3, calls, run=run),
            _continuation_context(workflow, 4, calls, run=run),
        ),
    )
    assert type(completed) is WorkflowProgressionDecision
    assert completed.decision == "workflow_complete"
    before = (state_path.read_bytes(), events_path.read_bytes())
    bounded_calls: list[str] = []

    import ai_office.engine.persisted_terminal_workflow_bounded_runner as runner_module

    def bounded(*_: object) -> object:
        bounded_calls.append("bounded")
        raise AssertionError("final success must bypass bounded continuation")

    monkeypatch.setattr(
        runner_module, "route_bounded_approved_workflow_continuation", bounded
    )
    final = route_persisted_terminal_workflow_bounded(
        workflow, state_path, events_path, [object(), object()]
    )
    assert type(final) is WorkflowProgressionDecision
    assert final.decision == "workflow_complete"
    assert bounded_calls == []
    assert (state_path.read_bytes(), events_path.read_bytes()) == before
    assert calls == ["step-1", "step-2", "step-3", "step-4"]


def test_08_restart_success_does_not_replay_completed_steps(tmp_path: Path) -> None:
    workflow = _workflow(4)
    calls: list[str] = []
    state_path, events_path, run = _seed_two_step_prefix(tmp_path, workflow, calls)
    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        (
            _continuation_context(workflow, 3, calls, run=run),
            _continuation_context(workflow, 4, calls, run=run),
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


def test_09_restart_context_exhaustion_returns_exact_remaining_prepare(
    tmp_path: Path,
) -> None:
    workflow = _workflow(4)
    calls: list[str] = []
    state_path, events_path, run = _seed_two_step_prefix(tmp_path, workflow, calls)
    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        (_continuation_context(workflow, 3, calls, run=run),),
    )
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


def test_10_restart_failure_does_not_consume_later_context(tmp_path: Path) -> None:
    workflow = _workflow(4)
    calls: list[str] = []
    state_path, events_path, run = _seed_two_step_prefix(tmp_path, workflow, calls)
    result = route_persisted_terminal_workflow_bounded(
        workflow,
        state_path,
        events_path,
        (
            _continuation_context(workflow, 3, calls, status=500, run=run),
            _continuation_context(workflow, 4, calls, run=run),
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


def test_11_ready_and_running_are_rejected_without_replay(tmp_path: Path) -> None:
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
        transport_calls: list[str] = []
        with pytest.raises(PersistedExecutionOutcomeCompatibilityError) as caught:
            route_persisted_terminal_workflow_bounded(
                workflow,
                state_path,
                events_path,
                (_continuation_context(workflow, 2, transport_calls),),
            )
        assert caught.value.detail.classification == "state_status"
        assert transport_calls == []
        assert events_path.read_bytes() == b""


def test_12_corrupt_or_mismatched_history_stays_lower_owned(tmp_path: Path) -> None:
    workflow = _workflow(2)
    cases: list[tuple[bytes, bytes]] = []
    cases.append((b"{", b""))
    mismatched_state = _state(workflow, workflow_id="other")
    cases.append(
        (
            serialize_workflow_execution_state_json(mismatched_state).encode(),
            serialize_runtime_step_event_jsonl(
                _event(workflow, workflow_id="other")
            ).encode(),
        )
    )
    for index, (state_bytes, event_bytes) in enumerate(cases):
        directory = tmp_path / str(index)
        directory.mkdir()
        state_path, events_path = directory / "state.json", directory / "events.jsonl"
        state_path.write_bytes(state_bytes)
        events_path.write_bytes(event_bytes)
        with pytest.raises(
            (PersistedExecutionOutcomeCompatibilityError, WorkflowExecutionLoadError)
        ):
            route_persisted_terminal_workflow_bounded(
                workflow,
                state_path,
                events_path,
                (_opaque_context(),),
            )


def test_13_default_lower_safe_errors_and_read_only_targets_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(3)
    import ai_office.engine.persisted_terminal_workflow_bounded_runner as runner_module

    with pytest.raises(PersistedExecutionOutcomeRoutingCompatibilityError):
        route_persisted_terminal_workflow_bounded(workflow, object(), object(), ())
    calls: list[str] = []
    state_path, events_path, run = _seed_two_step_prefix(tmp_path, workflow, calls)
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


def test_14_phase192_owned_change_is_never_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _workflow(2)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(b"before-state")
    events_path.write_bytes(b"before-events")
    routed = _prepare(workflow)
    contexts = (_opaque_context(),)
    import ai_office.engine.persisted_terminal_workflow_bounded_runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "route_persisted_execution_outcome_reentry",
        lambda *_args: routed,
    )
    calls: list[str] = []

    def mutating_return(*_: object) -> object:
        calls.append("return")
        state_path.write_bytes(b"phase192-owned-return")
        return object()

    monkeypatch.setattr(
        runner_module, "route_bounded_approved_workflow_continuation", mutating_return
    )
    _assert_runner_error(
        lambda: route_persisted_terminal_workflow_bounded(
            workflow, state_path, events_path, contexts
        ),
        "bounded_continuation_contract",
    )
    assert calls == ["return"]
    assert state_path.read_bytes() == b"phase192-owned-return"
    assert events_path.read_bytes() == b"before-events"

    def mutating_error(*_: object) -> object:
        calls.append("error")
        state_path.write_bytes(b"phase192-owned-error")
        raise RuntimeError("secret provider payload")

    monkeypatch.setattr(
        runner_module, "route_bounded_approved_workflow_continuation", mutating_error
    )
    with pytest.raises(RuntimeError, match="secret provider payload"):
        route_persisted_terminal_workflow_bounded(
            workflow, state_path, events_path, contexts
        )
    assert calls == ["return", "error"]
    assert state_path.read_bytes() == b"phase192-owned-error"
    assert events_path.read_bytes() == b"before-events"
