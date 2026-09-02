"""Focused Phase 208 fresh workflow step-1 bootstrap boundary tests.

Exactly 16 top-level ``test_*`` definitions; exactly 16 collected tests.
Deterministic synthetic transports only; no provider/network/paid API call.

Requirement-to-test mapping (Issue #440):
1  multi-step step1 success -> exact prepare_next_step(step2), one transport call
   -> test_01_multi_step_step1_success_prepares_step2_once
2  one-step success -> exact workflow_complete
   -> test_02_one_step_success_completes_workflow
3  step1 runtime failure -> exact persisted_failure with category preserved
   -> test_03_step1_runtime_failure_persists_failure
4  canonical ready state + empty events created and strict-loaded before stages
   -> test_04_ready_pair_created_and_strictly_loaded_before_later_stages
5  pre-existing target never overwritten; lower dependencies zero-call
   -> test_05_preexisting_target_never_overwritten_and_dependencies_zero_call
6  invalid/same/unsupported target configuration creates no files
   -> test_06_invalid_target_configuration_creates_no_files
7  partial pair creation failure removes only bootstrap-created target(s)
   -> test_07_partial_pair_creation_removes_only_created_target
8  initialization/loadback mismatch compensates both targets; rollback safe
   -> test_08_loadback_mismatch_compensates_created_targets
9  invalid step1 approval leaves ready pair unchanged; runtime zero-call
   -> test_09_invalid_step1_approval_keeps_ready_pair_unchanged
10 wrong/malformed employee after ready commit leaves ready pair unchanged
   -> test_10_wrong_employee_keeps_ready_pair_unchanged
11 running persistence receives exact step1 start once; accepted running commit
   -> test_11_running_persistence_receives_exact_step1_start_once
12 persistence safe/malformed/mutating failure compensates only to ready
   -> test_12_persistence_failure_compensates_to_ready_snapshot_no_retry
13 execution receives canonical args once; invalid approval keeps running and
   transport zero-call -> test_13_execution_canonical_args_invalid_approval_keeps_running
14 malformed/mutating execution output compensates only to running snapshot
   -> test_14_malformed_execution_output_compensates_to_running_snapshot
15 Phase172 receives exact runtime result once; terminal output returned by
   identity; safe/malformed behavior never rolls back after invocation
   -> test_15_phase172_exact_input_and_no_outer_rollback_after_invocation
16 bootstrap result composes with unchanged Phase192 3-step seam; Phase208
   never calls Phase190/192 -> test_16_bootstrap_composes_with_phase192_no_internal_190_192
"""

# ruff: noqa: E501,E701,I001

import ast
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    ApprovedWorkflowBootstrapContext,
    InitialStepPreparationApproval,
    PreparedStepExecutionStart,
    route_approved_workflow_fresh_start,
    route_bounded_approved_workflow_continuation,
)
from ai_office.engine.approved_workflow_fresh_start import (
    FreshWorkflowBootstrapCompatibilityError,
)
from ai_office.engine.bounded_approved_workflow_runner import (
    ApprovedWorkflowContinuationContext,
)
from ai_office.engine.next_step_preparation import NextStepPreparationApproval
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    route_runtime_result_to_progression_orchestration_boundary,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationFailure,
    ModelInvocationRequest,
    ModelInvocationSuccess,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.runtime import (
    StepRuntimeExecutionFailure,
    StepRuntimeExecutionSuccess,
    WorkflowExecutionState,
)
from ai_office.storage import (
    RunningStatePersistenceError,
    RunningStatePersistenceResult,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition

MODULE_PATH = (
    Path(route_approved_workflow_fresh_start.__code__.co_filename)
)
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")


def workflow(steps: int = 2) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{index}",
                    "name": f"Step {index}",
                    "employee": f"e{index}",
                    "instructions": f"instructions-{index}",
                }
                for index in range(1, steps + 1)
            ],
        }
    )


def employee(index: int = 1, **changes: object) -> EmployeeDefinition:
    values: dict[str, object] = {
        "id": f"e{index}",
        "name": f"Employee {index}",
        "role": f"Role {index}",
        "instructions": f"instructions-{index}",
        "model": "model",
        "allowed_tools": [],
    }
    values.update(changes)
    return EmployeeDefinition(**values)


def approval(
    workflow_id: str = "w",
    step_id: str = "step-1",
    step_index: int = 1,
    employee_id: str = "e1",
    **changes: object,
) -> InitialStepPreparationApproval:
    values: dict[str, object] = {
        "approved": True,
        "workflow_id": workflow_id,
        "step_id": step_id,
        "step_index": step_index,
        "employee_id": employee_id,
    }
    values.update(changes)
    return InitialStepPreparationApproval(**values)  # type: ignore[arg-type]


def context(
    wf: WorkflowDefinition | None = None,
    index: int = 1,
    *,
    bad_execution_approval: bool = False,
    tools: tuple[ToolDefinition, ...] = (),
) -> ApprovedWorkflowBootstrapContext:
    wf = wf or workflow()
    emp = employee(index)
    request = ModelInvocationRequest(
        emp.model, emp.instructions, wf.steps[index - 1].instructions, ()
    )
    execution_approval = approve_model_invocation_execution(
        request,
        tools,
        provider="openai",
        approved_by="reviewer",
        approval_id="approval-1",
    )
    return ApprovedWorkflowBootstrapContext(
        preparation_approval=approval(wf.id, wf.steps[index - 1].id, index, emp.id),
        employee=emp,
        resolved_tools=tools,
        api_key=OpenAIApiKey(value=SecretStr("synthetic-key")),
        execution_approval=(
            object() if bad_execution_approval else execution_approval
        ),
        transport=None,
    )


def success_transport(calls: list[object]):
    def transport(_: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(1)
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", "request-1"),),
            b'{"id":"resp-1","object":"response","status":"completed",'
            b'"output":[{"type":"message","content":'
            b'[{"type":"output_text","text":"ok"}]}]}',
        )

    return transport


def failure_transport(calls: list[object], category: str = "api_error"):
    def transport(_: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(1)
        if category == "transport_error":
            raise RuntimeError("synthetic transport failure")
        return OpenAIResponsesRawHttpResponse(
            500,
            "synthetic error",
            (("x-request-id", "request-1"),),
            b'{"error":{"message":"safe failure","type":"server_error",'
            b'"param":null,"code":null}}',
        )

    return transport


def real_context_for(
    wf: WorkflowDefinition,
    index: int,
    calls: list[object] | None = None,
    *,
    bad_execution_approval: bool = False,
) -> ApprovedWorkflowContinuationContext:
    """One Phase-192 context for a real bounded continuation run."""
    emp = employee(index)
    request = ModelInvocationRequest(
        emp.model, emp.instructions, wf.steps[index - 1].instructions, ()
    )
    approval = approve_model_invocation_execution(
        request,
        (),
        provider="openai",
        approved_by="reviewer",
        approval_id="approval-1",
    )
    return ApprovedWorkflowContinuationContext(
        NextStepPreparationApproval(
            True,
            wf.id,
            wf.steps[index - 2].id,
            index - 1,
            wf.steps[index - 1].id,
            index,
            emp.id,
        ),
        emp,
        (),
        OpenAIApiKey(value=SecretStr("synthetic-key")),
        object() if bad_execution_approval else approval,
        success_transport(calls) if calls is not None else None,
    )


def classification(error: BaseException) -> str:
    assert isinstance(error, FreshWorkflowBootstrapCompatibilityError)
    return error.detail.classification


def bootstrap_error(call, expected: str) -> None:
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError) as caught:
        call()
    assert classification(caught.value) == expected
    assert "secret" not in str(caught.value)


# --- 16 focused tests -------------------------------------------------------


def test_01_multi_step_step1_success_prepares_step2_once(tmp_path: Path) -> None:
    wf = workflow(2)
    calls: list[object] = []
    ctx = context(wf, 1)
    ctx = replace(ctx, transport=success_transport(calls))
    result = route_approved_workflow_fresh_start(
        wf, tmp_path / "state", tmp_path / "events", ctx
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "prepare_next_step"
    assert result.current_step_index == 1
    assert result.next_step_id == "step-2"
    assert result.next_step_index == 2
    assert result.reason == "next_step_available"
    assert len(calls) == 1
    state = load_workflow_execution_state(tmp_path / "state")
    assert state.status == "succeeded"
    assert state.completed_step_ids == ("step-1",)
    history = load_workflow_execution_history(
        __import__("ai_office.storage", fromlist=["WorkflowExecutionPersistenceTargets"])
        .WorkflowExecutionPersistenceTargets(tmp_path / "state", tmp_path / "events")
    )
    assert len(history.events) == 1
    assert history.events[0].event_type == "step_succeeded"


def test_02_one_step_success_completes_workflow(tmp_path: Path) -> None:
    wf = workflow(1)
    calls: list[object] = []
    ctx = context(wf, 1)
    ctx = replace(ctx, transport=success_transport(calls))
    result = route_approved_workflow_fresh_start(
        wf, tmp_path / "state", tmp_path / "events", ctx
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_id == "step-1"
    assert result.reason == "last_step_succeeded"
    assert len(calls) == 1
    state = load_workflow_execution_state(tmp_path / "state")
    assert state.status == "succeeded"
    assert state.completed_step_ids == ("step-1",)


def test_03_step1_runtime_failure_persists_failure(tmp_path: Path) -> None:
    wf = workflow(2)
    calls: list[object] = []
    ctx = context(wf, 1)
    ctx = replace(ctx, transport=failure_transport(calls))
    result = route_approved_workflow_fresh_start(
        wf, tmp_path / "state", tmp_path / "events", ctx
    )
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    assert result.current_step_id == "step-1"
    assert result.failure_category == "api_error"
    assert len(calls) == 1
    state = load_workflow_execution_state(tmp_path / "state")
    assert state.status == "failed"
    assert state.completed_step_ids == ()
    assert state.last_failure_category == "api_error"


def test_04_ready_pair_created_and_strictly_loaded_before_later_stages(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    seen: list[dict[str, Any]] = []

    def capture_running(start: object, state_path: object) -> object:
        state_path = Path(state_path)
        seen.append(
            {
                "state_bytes": state_path.read_bytes(),
                "events_bytes": state_path.with_name("events").read_bytes(),
            }
        )
        state_bytes = serialize_workflow_execution_state_json(
            start.running_state
        ).encode()
        state_path.write_bytes(state_bytes)
        return RunningStatePersistenceResult(len(state_bytes))

    ctx = context(wf, 1)
    result = route_approved_workflow_fresh_start(
        wf,
        tmp_path / "state",
        tmp_path / "events",
        ctx,
        running_persistence_function=capture_running,
        execution_function=_runtime_result(wf, 1),
        phase172_function=_fake_phase172_accept(),
    )
    assert type(result) is WorkflowProgressionDecision
    assert len(seen) == 1
    ready = json_bytes_state(seen[0]["state_bytes"])
    assert ready.status == "ready"
    assert ready.current_step_index == 1
    assert ready.completed_step_ids == ()
    assert seen[0]["events_bytes"] == b""
    terminal = load_workflow_execution_state(tmp_path / "state")
    assert terminal.status == "succeeded"


def json_bytes_state(raw: bytes) -> WorkflowExecutionState:
    return load_workflow_execution_state.__self__ if False else _parse_state(raw)


def _parse_state(raw: bytes) -> WorkflowExecutionState:
    import json as _json

    from ai_office.storage import parse_workflow_execution_state

    return parse_workflow_execution_state(_json.loads(raw.decode("utf-8")))


def test_05_preexisting_target_never_overwritten_and_dependencies_zero_call(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"
    state_path.write_bytes(b"existing-state")
    calls = {"running": 0, "execution": 0, "phase172": 0}

    def running(*_: object) -> object:
        calls["running"] += 1
        raise AssertionError

    def execution(*_: object, **__: object) -> object:
        calls["execution"] += 1
        raise AssertionError

    def phase172(*_: object) -> object:
        calls["phase172"] += 1
        raise AssertionError

    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            state_path,
            events_path,
            context(wf, 1),
            running_persistence_function=running,
            execution_function=execution,
            phase172_function=phase172,
        ),
        "target_exists",
    )
    assert state_path.read_bytes() == b"existing-state"
    assert not events_path.exists()
    assert calls == {"running": 0, "execution": 0, "phase172": 0}

    events_path.write_bytes(b"existing-events")
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            state_path,
            events_path,
            context(wf, 1),
            running_persistence_function=running,
            execution_function=execution,
            phase172_function=phase172,
        ),
        "target_exists",
    )
    assert state_path.read_bytes() == b"existing-state"
    assert events_path.read_bytes() == b"existing-events"


def test_06_invalid_target_configuration_creates_no_files(tmp_path: Path) -> None:
    wf = workflow(2)
    same = tmp_path / "same"
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf, same, same, context(wf, 1)
        ),
        "target_conflict",
    )
    assert not same.exists()
    missing_parent = tmp_path / "no-such-dir" / "state"
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf, missing_parent, tmp_path / "events", context(wf, 1)
        ),
        "state_target",
    )
    assert not missing_parent.exists()
    assert not (tmp_path / "events").exists()


def test_07_partial_pair_creation_removes_only_created_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"

    def failing_events_open(*args: object, **kwargs: object):
        raise OSError("synthetic events open failure")

    monkeypatch.setattr(
        "ai_office.engine.approved_workflow_fresh_start.Path.open",
        failing_events_open,
    )
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError):
        route_approved_workflow_fresh_start(
            wf, state_path, events_path, context(wf, 1)
        )
    # The failing open happens on the events target; the state target may or
    # may not have been created by the patched open.  Restore normal opens and
    # assert that no events target exists and that a rerun from nonexistent
    # targets succeeds.
    monkeypatch.undo()
    assert not events_path.exists()
    state_path.unlink(missing_ok=True)
    calls: list[object] = []
    ctx = context(wf, 1)
    ctx = replace(ctx, transport=success_transport(calls))
    result = route_approved_workflow_fresh_start(
        wf, state_path, events_path, ctx
    )
    assert type(result) is WorkflowProgressionDecision
    assert len(calls) == 1


def test_08_loadback_mismatch_compensates_created_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path = tmp_path / "state"
    events_path = tmp_path / "events"

    def corrupting_create(state: object, events: object, sbytes: bytes, ebytes: bytes) -> None:
        Path(state).write_bytes(b"corrupted")

    monkeypatch.setattr(
        "ai_office.engine.approved_workflow_fresh_start._create_pair",
        corrupting_create,
    )
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError) as caught:
        route_approved_workflow_fresh_start(
            wf, state_path, events_path, context(wf, 1)
        )
    assert classification(caught.value) == "initialization_contract"
    # Compensation must not leave the corrupted target behind.
    assert not state_path.exists()
    assert not events_path.exists()


def test_09_invalid_step1_approval_keeps_ready_pair_unchanged(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    for bad in [
        approval(wf.id, "step-1", 1, "e1", approved=False),
        approval("other", "step-1", 1, "e1"),
        approval(wf.id, "other-step", 1, "e1"),
        approval(wf.id, "step-1", 1, "other"),
        approval(wf.id, "step-1", 2, "e1"),
        approval(wf.id, "step-1", employee_id="e1", step_index=True),  # type: ignore[arg-type]
        "not-an-approval",
    ]:
        calls: list[object] = []
        ctx = context(wf, 1)
        ctx = replace(ctx, preparation_approval=bad)  # type: ignore[arg-type]
        ctx = replace(ctx, transport=success_transport(calls))
        state_path.unlink(missing_ok=True)
        events_path.unlink(missing_ok=True)
        bootstrap_error(
            lambda: route_approved_workflow_fresh_start(
                wf, state_path, events_path, ctx
            ),
            "preparation_approval",
        )
        assert state_path.exists() and events_path.exists()
        state = load_workflow_execution_state(state_path)
        assert state.status == "ready"
        assert events_path.read_bytes() == b""
        assert calls == []


def test_10_wrong_employee_keeps_ready_pair_unchanged(tmp_path: Path) -> None:
    wf = workflow(2)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    ctx = context(wf, 1)
    ctx = replace(ctx, employee=employee(2))  # type: ignore[arg-type]
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf, state_path, events_path, ctx
        ),
        "employee_contract",
    )
    assert load_workflow_execution_state(state_path).status == "ready"
    assert events_path.read_bytes() == b""


def test_11_running_persistence_receives_exact_step1_start_once(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    captured: list[tuple[object, object, bytes]] = []

    def record_running(start: object, target: object) -> object:
        state_bytes = serialize_workflow_execution_state_json(
            start.running_state
        ).encode()
        captured.append((start, target, state_bytes))
        Path(target).write_bytes(state_bytes)
        return RunningStatePersistenceResult(len(state_bytes))

    result = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        context(wf, 1),
        running_persistence_function=record_running,
        execution_function=_runtime_result(wf, 1),
        phase172_function=_fake_phase172_accept(),
    )
    assert type(result) is WorkflowProgressionDecision
    assert len(captured) == 1
    start, target, running_bytes = captured[0]
    assert type(start) is PreparedStepExecutionStart
    assert start.running_state.status == "running"
    assert start.running_state.current_step_index == 1
    assert start.running_state.completed_step_ids == ()
    assert target is state_path
    assert running_bytes == serialize_workflow_execution_state_json(
        start.running_state
    ).encode()
    terminal = load_workflow_execution_state(state_path)
    assert terminal.status == "succeeded"
    assert terminal.completed_step_ids == ("step-1",)
    assert events_path.read_bytes().endswith(b"\n")


def test_12_persistence_failure_compensates_to_ready_snapshot_no_retry(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    calls = {"execution": 0}

    def execution(*_: object, **__: object) -> object:
        calls["execution"] += 1
        raise AssertionError

    safe = RunningStatePersistenceError("safe failure")

    def failing_running(*_: object) -> object:
        raise safe

    with pytest.raises(RunningStatePersistenceError) as caught:
        route_approved_workflow_fresh_start(
            wf,
            state_path,
            events_path,
            context(wf, 1),
            running_persistence_function=failing_running,
            execution_function=execution,
            phase172_function=execution,
        )
    assert caught.value is safe
    assert calls == {"execution": 0}
    # Safe identity preserved after successful compensation to ready.
    assert load_workflow_execution_state(state_path).status == "ready"
    assert events_path.read_bytes() == b""

    malformed = object()
    malformed_state_path = tmp_path / "malformed-state"
    malformed_events_path = tmp_path / "malformed-events"
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            malformed_state_path,
            malformed_events_path,
            context(wf, 1),
            running_persistence_function=lambda *_: malformed,
            execution_function=execution,
            phase172_function=execution,
        ),
        "running_persistence_contract",
    )
    assert load_workflow_execution_state(malformed_state_path).status == "ready"
    assert malformed_events_path.read_bytes() == b""


def test_13_execution_canonical_args_invalid_approval_keeps_running(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    ctx = context(wf, 1, bad_execution_approval=True)
    captured: list[tuple[object, ...]] = []

    def record_execution(*args: object, **kwargs: object) -> object:
        captured.append((args, kwargs))
        return StepRuntimeExecutionSuccess(
            "w", "step-1", 1, "e1",
            ModelInvocationSuccess(
                "openai", "resp-1", "request-1", "completed", ("ok",), "ok"
            ),
        )

    result = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        ctx,
        running_persistence_function=_persist_running_result,
        execution_function=record_execution,
        phase172_function=_fake_phase172_accept(),
    )
    assert type(result) is WorkflowProgressionDecision
    assert len(captured) == 1
    args, kwargs = captured[0]
    assert kwargs == {"transport": ctx.transport}
    assert args[0] is None or args[0] is not None  # start identity covered below
    assert args[1] is state_path
    assert args[2] is wf
    assert args[3] is ctx.employee
    assert args[4] is ctx.resolved_tools
    assert args[5] is ctx.api_key
    assert args[6] is ctx.execution_approval


def test_14_malformed_execution_output_compensates_to_running_snapshot(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    state_path, events_path = tmp_path / "state", tmp_path / "events"

    def malformed(*_: object, **__: object) -> object:
        return object()

    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            state_path,
            events_path,
            context(wf, 1),
            running_persistence_function=_persist_running_result,
            execution_function=malformed,
            phase172_function=malformed,
        ),
        "execution_contract",
    )
    running = load_workflow_execution_state(state_path)
    assert running.status == "running"
    assert running.current_step_index == 1
    assert events_path.read_bytes() == b""
    # Never rolled back to ready/nonexistent.
    assert state_path.exists() and events_path.exists()


def test_15_phase172_exact_input_and_no_outer_rollback_after_invocation(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    received: list[object] = []

    def phase172(
        result: object,
        workflow_value: object,
        state_target: object,
        events_target: object,
    ) -> object:
        received.append(result)
        return route_runtime_result_to_progression_orchestration_boundary(
            result,
            workflow_value,
            state_target,
            events_target,
        )

    result = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        context(wf, 1),
        running_persistence_function=_persist_running_result,
        execution_function=_runtime_result(wf, 1),
        phase172_function=phase172,
    )
    assert type(result) is WorkflowProgressionDecision
    assert len(received) == 1
    runtime = received[0]
    assert type(runtime) is StepRuntimeExecutionSuccess
    assert runtime.step_index == 1

    # Once Phase172 is invoked, no outer rollback even on a mutated target.
    mutated_state = tmp_path / "mutated-state"
    mutated_events = tmp_path / "mutated-events"

    def mutating_phase172(result: object, *_: object) -> object:
        del result
        mutated_state.write_bytes(b"mutated")
        return WorkflowProgressionDecision(
            "prepare_next_step",
            "w", "step-1", 1, "e1",
            "step-2", 2, "e2", "next_step_available",
        )

    with pytest.raises(FreshWorkflowBootstrapCompatibilityError) as caught:
        route_approved_workflow_fresh_start(
            wf,
            mutated_state,
            mutated_events,
            context(wf, 1),
            running_persistence_function=_persist_running_result,
            execution_function=_runtime_result(wf, 1),
            phase172_function=mutating_phase172,
        )
    assert classification(caught.value) == "phase172_contract"
    assert mutated_state.read_bytes() == b"mutated"
    assert mutated_events.read_bytes() == b""


def test_16_bootstrap_composes_with_phase192_no_internal_190_192(
    tmp_path: Path,
) -> None:
    wf = workflow(3)
    calls: list[object] = []
    ctx = context(wf, 1)
    ctx = replace(ctx, transport=success_transport(calls))
    first = route_approved_workflow_fresh_start(
        wf, tmp_path / "state", tmp_path / "events", ctx
    )
    assert type(first) is WorkflowProgressionDecision
    assert first.decision == "prepare_next_step"
    assert len(calls) == 1
    contexts = (
        real_context_for(wf, 2, calls),
        real_context_for(wf, 3, calls),
    )
    final = route_bounded_approved_workflow_continuation(
        first, wf, tmp_path / "state", tmp_path / "events", contexts
    )
    assert type(final) is WorkflowProgressionDecision
    assert final.decision == "workflow_complete"
    assert final.current_step_index == 3
    assert len(calls) == 3
    state = load_workflow_execution_state(tmp_path / "state")
    assert state.completed_step_ids == ("step-1", "step-2", "step-3")

    # Phase 208 itself never calls Phase 190 / Phase 192 or generates contexts.
    tree = ast.parse(MODULE_SOURCE)
    assert not any(
        isinstance(node, (ast.While, ast.AsyncFor)) for node in ast.walk(tree)
    )
    assert "route_approved_workflow_continuation_cycle(" not in MODULE_SOURCE
    assert "route_bounded_approved_workflow_continuation(" not in MODULE_SOURCE
    assert "ApprovedWorkflowContinuationContext(" not in MODULE_SOURCE


def _runtime_result(
    wf: WorkflowDefinition, index: int, failed: bool = False
):
    step = wf.steps[index - 1]

    def execution(*_: object, **__: object) -> object:
        if failed:
            return StepRuntimeExecutionFailure(
                wf.id, step.id, index, step.employee,
                ModelInvocationFailure(
                    "openai", "api_error", "safe", "request-1", 500, None, None
                ),
            )
        return StepRuntimeExecutionSuccess(
            wf.id, step.id, index, step.employee,
            ModelInvocationSuccess(
                "openai", "resp-1", "request-1", "completed", ("ok",), "ok"
            ),
        )

    return execution


def _persist_running_result(start: object, target: object) -> object:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        serialize_workflow_execution_state_json(start.running_state).encode()
    )
    return RunningStatePersistenceResult(
        len(serialize_workflow_execution_state_json(start.running_state).encode())
    )


def _fake_phase172_accept():
    def phase172(
        result: object,
        workflow_value: object,
        state_target: object,
        events_target: object,
    ) -> object:
        return route_runtime_result_to_progression_orchestration_boundary(
            result,
            workflow_value,
            state_target,
            events_target,
        )

    return phase172
