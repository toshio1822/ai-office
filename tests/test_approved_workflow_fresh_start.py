"""Focused Phase 208 fresh-start boundary tests for Issue #659.

The suite exercises the narrowed four-input public contract and observable
stage/commit behavior. Canonical owners are monkeypatched only at their module
resolution points when a fault or call-count observation is needed; tests do
not prescribe fake-dependency argument topology or object identity.
Synthetic transports are used exclusively, so no provider/network/paid API
call is made.
"""

# ruff: noqa: E501,E701,I001

import inspect
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
import ai_office.engine.approved_workflow_fresh_start as fresh_start_module
from ai_office.engine import (
    ApprovedWorkflowBootstrapContext,
    InitialStepPreparationApproval,
    route_approved_workflow_fresh_start,
)
from ai_office.engine.approved_workflow_fresh_start import (
    FreshWorkflowBootstrapCompatibilityError,
)
from ai_office.engine.workflow_approval_evidence import (
    WorkflowApprovalEvidencePersistenceError,
    approve_business_step,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.runtime_result_to_progression_orchestration_boundary import (
    RuntimeResultToProgressionOrchestrationBoundaryError as Phase172Error,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.invocation import (
    ModelInvocationRequest,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.runtime import StepRuntimeExecutionSuccess
from ai_office.runtime.persisted_start_execution import (
    PersistedStartExecutionCompatibilityError,
)
from ai_office.storage import (
    RunningStatePersistenceError,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    load_workflow_execution_state,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition
from tests._run_test_support import create_test_run


def workflow(
    steps: int = 2, *, business_approval_required: bool = True
) -> WorkflowDefinition:
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
                    "business_approval_required": business_approval_required,
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
    state_path: Path,
    bad_execution_approval: bool = False,
    tools: tuple[ToolDefinition, ...] = (),
    with_business_approvals: bool = True,
) -> ApprovedWorkflowBootstrapContext:
    wf = wf or workflow()
    run_id = state_path.name.removesuffix(".state.json")
    run = create_test_run(
        state_path.parent,
        run_id,
        wf,
        tuple(employee(step_index) for step_index, _ in enumerate(wf.steps, 1)),
        with_business_approvals=with_business_approvals,
    )
    binding = run.binding
    emp = employee(index)
    request = ModelInvocationRequest(
        emp.model,
        emp.instructions,
        wf.steps[index - 1].instructions,
        tools,
        run_id=binding.run_id,
        manifest_digest=binding.manifest_digest,
        run_input=f"input-{run_id}",
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
        execution_approval=(object() if bad_execution_approval else execution_approval),
        transport=None,
        binding=binding,
        manifest_store=run.store,
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


def failure_transport(calls: list[object]):
    def transport(_: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(1)
        return OpenAIResponsesRawHttpResponse(
            500,
            "synthetic error",
            (("x-request-id", "request-1"),),
            b'{"error":{"message":"safe failure","type":"server_error",'
            b'"param":null,"code":null}}',
        )

    return transport


def classification(error: BaseException) -> str:
    assert isinstance(error, FreshWorkflowBootstrapCompatibilityError)
    return error.detail.classification


def bootstrap_error(call, expected: str) -> None:
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError) as caught:
        call()
    assert classification(caught.value) == expected
    assert "secret" not in str(caught.value)


def test_01_canonical_stages_run_once_and_preserve_non_final_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-1.state.json",
        tmp_path / "run-1.events.jsonl",
    )
    transport_calls: list[object] = []
    persistence_calls: list[object] = []
    execution_calls: list[object] = []
    phase_calls: list[object] = []
    phase_inputs: list[object] = []

    original_persistence = fresh_start_module.persist_prepared_running_state
    original_execution = fresh_start_module.execute_persisted_start_openai_step
    original_phase = (
        fresh_start_module.route_runtime_result_to_progression_orchestration_boundary
    )

    def observe_persistence(*args: object, **kwargs: object) -> object:
        persistence_calls.append(1)
        return original_persistence(*args, **kwargs)

    def observe_execution(*args: object, **kwargs: object) -> object:
        execution_calls.append(1)
        return original_execution(*args, **kwargs)

    def observe_phase(*args: object, **kwargs: object) -> object:
        phase_calls.append(1)
        phase_inputs.append(args[0] if args else None)
        return original_phase(*args, **kwargs)

    monkeypatch.setattr(
        fresh_start_module,
        "persist_prepared_running_state",
        observe_persistence,
    )
    monkeypatch.setattr(
        fresh_start_module,
        "execute_persisted_start_openai_step",
        observe_execution,
    )
    monkeypatch.setattr(
        fresh_start_module,
        "route_runtime_result_to_progression_orchestration_boundary",
        observe_phase,
    )

    result = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        replace(
            context(wf, 1, state_path=state_path),
            transport=success_transport(transport_calls),
        ),
    )

    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "prepare_next_step"
    assert result.next_step_id == "step-2"
    assert persistence_calls == [1]
    assert execution_calls == [1]
    assert phase_calls == [1]
    assert type(phase_inputs[0]) is StepRuntimeExecutionSuccess
    assert transport_calls == [1]
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(state_path, events_path)
    )
    assert history.state.status == "succeeded"
    assert history.state.completed_step_ids == ("step-1",)
    assert state_path.read_bytes() == serialize_workflow_execution_state_json(
        history.state
    ).encode("utf-8")
    assert len(history.events) == 1
    assert history.events[0].event_type == "step_succeeded"
    assert events_path.read_bytes() == serialize_runtime_step_event_jsonl(
        history.events[0]
    ).encode("utf-8")


def test_02_one_step_success_returns_workflow_complete(tmp_path: Path) -> None:
    wf = workflow(1)
    calls: list[object] = []
    state_path, events_path = (
        tmp_path / "run-2.state.json",
        tmp_path / "run-2.events.jsonl",
    )
    result = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        replace(
            context(wf, 1, state_path=state_path),
            transport=success_transport(calls),
        ),
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert result.current_step_id == "step-1"
    assert result.reason == "last_step_succeeded"
    assert calls == [1]
    state = load_workflow_execution_state(state_path)
    assert state.status == "succeeded"
    assert state.completed_step_ids == ("step-1",)


def test_03_runtime_failure_returns_persisted_failure_once(tmp_path: Path) -> None:
    wf = workflow(2)
    calls: list[object] = []
    state_path, events_path = (
        tmp_path / "run-3.state.json",
        tmp_path / "run-3.events.jsonl",
    )
    result = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        replace(
            context(wf, 1, state_path=state_path),
            transport=failure_transport(calls),
        ),
    )
    assert type(result) is PersistedExecutionOutcome
    assert result.outcome == "persisted_failure"
    assert result.current_step_id == "step-1"
    assert result.failure_category == "api_error"
    assert calls == [1]
    state = load_workflow_execution_state(state_path)
    assert state.status == "failed"
    assert state.completed_step_ids == ()
    assert state.last_failure_category == "api_error"


def test_04_public_contract_has_four_inputs_and_rejects_removed_keywords(
    tmp_path: Path,
) -> None:
    signature = inspect.signature(route_approved_workflow_fresh_start)
    assert list(signature.parameters) == [
        "workflow",
        "state_path",
        "events_path",
        "context",
    ]
    assert all(
        parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in signature.parameters.values()
    )
    assert all(
        parameter.kind is not inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )

    wf = workflow()
    for index, removed_keyword in enumerate(
        (
            "running_persistence_function",
            "execution_function",
            "phase172_function",
        )
    ):
        state_path = tmp_path / f"run-4-{index}.state.json"
        events_path = tmp_path / f"run-4-{index}.events.jsonl"
        with pytest.raises(TypeError):
            route_approved_workflow_fresh_start(
                wf,
                state_path,
                events_path,
                context(wf, 1, state_path=state_path),
                **{removed_keyword: object()},
            )


def test_runless_or_arbitrary_targets_fail_before_provider_or_durable_mutation(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    source_state = tmp_path / "source.state.json"
    source_context = context(wf, 1, state_path=source_state)

    runless_calls: list[object] = []
    arbitrary_state = tmp_path / "arbitrary.state.json"
    arbitrary_events = tmp_path / "arbitrary.events.jsonl"
    runless_context = replace(
        source_context,
        binding=None,
        manifest_store=None,
        transport=success_transport(runless_calls),
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf, arbitrary_state, arbitrary_events, runless_context
        ),
        "run_binding",
    )
    assert runless_calls == []
    assert not arbitrary_state.exists()
    assert not arbitrary_events.exists()

    bound_calls: list[object] = []
    bound_context = replace(
        source_context,
        transport=success_transport(bound_calls),
    )
    bound_arbitrary_state = tmp_path / "bound-arbitrary.state.json"
    bound_arbitrary_events = tmp_path / "bound-arbitrary.events.jsonl"
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            bound_arbitrary_state,
            bound_arbitrary_events,
            bound_context,
        ),
        "run_binding",
    )
    assert bound_calls == []
    assert not bound_arbitrary_state.exists()
    assert not bound_arbitrary_events.exists()


def test_05_ready_pair_is_strictly_committed_before_running_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-5.state.json",
        tmp_path / "run-5.events.jsonl",
    )
    observed: list[tuple[str, bytes]] = []
    original_persistence = fresh_start_module.persist_prepared_running_state

    def observe_ready(*args: object, **kwargs: object) -> object:
        observed.append(
            (
                load_workflow_execution_state(state_path).status,
                events_path.read_bytes(),
            )
        )
        return original_persistence(*args, **kwargs)

    monkeypatch.setattr(
        fresh_start_module,
        "persist_prepared_running_state",
        observe_ready,
    )
    result = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        replace(
            context(wf, 1, state_path=state_path),
            transport=success_transport([]),
        ),
    )
    assert type(result) is WorkflowProgressionDecision
    assert observed == [("ready", b"")]
    assert load_workflow_execution_state(state_path).status == "succeeded"


def test_06_existing_targets_are_not_overwritten(tmp_path: Path) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-6.state.json",
        tmp_path / "run-6.events.jsonl",
    )
    state_path.write_bytes(b"existing-state")
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf, state_path, events_path, context(wf, 1, state_path=state_path)
        ),
        "target_exists",
    )
    assert state_path.read_bytes() == b"existing-state"
    assert not events_path.exists()

    events_path.write_bytes(b"existing-events")
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf, state_path, events_path, context(wf, 1, state_path=state_path)
        ),
        "target_exists",
    )
    assert state_path.read_bytes() == b"existing-state"
    assert events_path.read_bytes() == b"existing-events"

    same_path = tmp_path / "run-6-same.state.json"
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf, same_path, same_path, context(wf, 1, state_path=same_path)
        ),
        "target_conflict",
    )
    assert not same_path.exists()

    missing_parent = tmp_path / "missing" / "run-6.state.json"
    valid_state = tmp_path / "run-6-valid.state.json"
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            missing_parent,
            tmp_path / "missing" / "run-6.events.jsonl",
            context(wf, 1, state_path=valid_state),
        ),
        "run_binding",
    )
    assert not missing_parent.exists()


def test_07_initialization_failure_compensates_only_created_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-7.state.json",
        tmp_path / "run-7.events.jsonl",
    )
    original_open = Path.open

    def failing_events_open(
        path: Path, mode: str = "r", *args: object, **kwargs: object
    ):
        if path == events_path and mode == "xb":
            assert state_path.is_file()
            raise OSError("synthetic events open failure")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(
        "ai_office.engine.approved_workflow_fresh_start.Path.open",
        failing_events_open,
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf, state_path, events_path, context(wf, 1, state_path=state_path)
        ),
        "dependency_error",
    )
    assert not state_path.exists()
    assert not events_path.exists()


def test_08_loadback_mismatch_compensates_created_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-8.state.json",
        tmp_path / "run-8.events.jsonl",
    )

    def mismatched_loadback(*_: object) -> object:
        assert state_path.is_file()
        assert events_path.is_file()
        return object()

    monkeypatch.setattr(
        "ai_office.engine.approved_workflow_fresh_start.load_workflow_execution_history",
        mismatched_loadback,
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf, state_path, events_path, context(wf, 1, state_path=state_path)
        ),
        "initialization_contract",
    )
    assert not state_path.exists()
    assert not events_path.exists()

    rollback_state, rollback_events = (
        tmp_path / "run-8-rollback.state.json",
        tmp_path / "run-8-rollback.events.jsonl",
    )
    original_unlink = Path.unlink

    def fail_state_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path == rollback_state:
            raise OSError("synthetic compensation failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        "ai_office.engine.approved_workflow_fresh_start.Path.unlink",
        fail_state_unlink,
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            rollback_state,
            rollback_events,
            context(wf, 1, state_path=rollback_state),
        ),
        "rollback_failure",
    )
    assert rollback_state.exists()
    assert not rollback_events.exists()


def test_09_invalid_approval_keeps_ready_pair_and_skips_provider(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    invalid = (
        approval("other", "step-1", 1, "e1"),
        approval(wf.id, "other-step", 1, "e1"),
        approval(wf.id, "step-1", 2, "e1"),
        "not-an-approval",
    )
    for index, bad in enumerate(invalid):
        state_path, events_path = (
            tmp_path / f"run-9-{index}.state.json",
            tmp_path / f"run-9-{index}.events.jsonl",
        )
        calls: list[object] = []
        ctx = replace(
            context(wf, 1, state_path=state_path),
            preparation_approval=bad,  # type: ignore[arg-type]
            transport=success_transport(calls),
        )
        bootstrap_error(
            lambda ctx=ctx, state_path=state_path, events_path=events_path: (
                route_approved_workflow_fresh_start(wf, state_path, events_path, ctx)
            ),
            "preparation_approval",
        )
        assert load_workflow_execution_state(state_path).status == "ready"
        assert events_path.read_bytes() == b""
        assert calls == []


def test_10_legacy_preparation_flag_is_not_business_authority(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-10-legacy.state.json",
        tmp_path / "run-10-legacy.events.jsonl",
    )
    calls: list[object] = []
    result = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        replace(
            context(wf, 1, state_path=state_path),
            preparation_approval=approval(wf.id, "step-1", 1, "e1", approved=False),
            transport=success_transport(calls),
        ),
    )
    assert type(result) is WorkflowProgressionDecision
    assert calls == [1]
    assert load_workflow_execution_state(state_path).status == "succeeded"


def test_11_wrong_employee_keeps_ready_pair(tmp_path: Path) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-10.state.json",
        tmp_path / "run-10.events.jsonl",
    )
    ctx = replace(
        context(wf, 1, state_path=state_path),
        employee=employee(2),  # type: ignore[arg-type]
        transport=success_transport([]),
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(wf, state_path, events_path, ctx),
        "employee_contract",
    )
    assert load_workflow_execution_state(state_path).status == "ready"
    assert events_path.read_bytes() == b""


def test_11_running_persistence_failure_recovers_ready_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)

    safe_state, safe_events = (
        tmp_path / "run-11-safe.state.json",
        tmp_path / "run-11-safe.events.jsonl",
    )
    safe_calls: list[object] = []
    safe_snapshot: list[tuple[bytes, bytes]] = []
    safe_error = RunningStatePersistenceError("safe persistence failure")

    def safe_failure(*_: object, **__: object) -> object:
        safe_calls.append(1)
        safe_snapshot.append((safe_state.read_bytes(), safe_events.read_bytes()))
        safe_state.write_bytes(b"mutated-state")
        safe_events.write_bytes(b"mutated-events")
        raise safe_error

    monkeypatch.setattr(
        fresh_start_module, "persist_prepared_running_state", safe_failure
    )
    with pytest.raises(RunningStatePersistenceError) as caught:
        route_approved_workflow_fresh_start(
            wf,
            safe_state,
            safe_events,
            replace(
                context(wf, 1, state_path=safe_state), transport=success_transport([])
            ),
        )
    assert caught.value is safe_error
    assert safe_calls == [1]
    assert safe_snapshot
    assert (safe_state.read_bytes(), safe_events.read_bytes()) == safe_snapshot[0]
    assert load_workflow_execution_state(safe_state).status == "ready"
    assert safe_events.read_bytes() == b""

    malformed_state = tmp_path / "run-11-malformed.state.json"
    malformed_events = tmp_path / "run-11-malformed.events.jsonl"
    malformed_calls: list[object] = []

    def malformed(*_: object, **__: object) -> object:
        malformed_calls.append(1)
        return object()

    monkeypatch.setattr(fresh_start_module, "persist_prepared_running_state", malformed)
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            malformed_state,
            malformed_events,
            replace(
                context(wf, 1, state_path=malformed_state),
                transport=success_transport([]),
            ),
        ),
        "running_persistence_contract",
    )
    assert malformed_calls == [1]
    assert load_workflow_execution_state(malformed_state).status == "ready"
    assert malformed_events.read_bytes() == b""

    unexpected_state = tmp_path / "run-11-unexpected.state.json"
    unexpected_events = tmp_path / "run-11-unexpected.events.jsonl"
    unexpected_calls: list[object] = []

    def unexpected(*_: object, **__: object) -> object:
        unexpected_calls.append(1)
        unexpected_state.write_bytes(b"unexpected-state")
        unexpected_events.write_bytes(b"unexpected-events")
        raise RuntimeError("secret persistence failure")

    monkeypatch.setattr(
        fresh_start_module, "persist_prepared_running_state", unexpected
    )
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError) as caught_unexpected:
        route_approved_workflow_fresh_start(
            wf,
            unexpected_state,
            unexpected_events,
            replace(
                context(wf, 1, state_path=unexpected_state),
                transport=success_transport([]),
            ),
        )
    assert classification(caught_unexpected.value) == "dependency_error"
    assert "secret" not in str(caught_unexpected.value)
    assert unexpected_calls == [1]
    assert load_workflow_execution_state(unexpected_state).status == "ready"
    assert unexpected_events.read_bytes() == b""


def test_12_invalid_execution_approval_keeps_running_and_skips_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-12.state.json",
        tmp_path / "run-12.events.jsonl",
    )
    transport_calls: list[object] = []
    phase_calls: list[object] = []

    def unexpected_phase(*_: object, **__: object) -> object:
        phase_calls.append(1)
        raise AssertionError("Phase 172 must not run after execution rejection")

    monkeypatch.setattr(
        fresh_start_module,
        "route_runtime_result_to_progression_orchestration_boundary",
        unexpected_phase,
    )
    ctx = replace(
        context(wf, 1, state_path=state_path, bad_execution_approval=True),
        transport=success_transport(transport_calls),
    )
    with pytest.raises(PersistedStartExecutionCompatibilityError) as caught:
        route_approved_workflow_fresh_start(wf, state_path, events_path, ctx)
    assert caught.value.detail.classification == "request_data"
    assert load_workflow_execution_state(state_path).status == "running"
    assert events_path.read_bytes() == b""
    assert transport_calls == []
    assert phase_calls == []


def test_13_required_business_approval_missing_skips_provider(
    tmp_path: Path,
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-13-business.state.json",
        tmp_path / "run-13-business.events.jsonl",
    )
    transport_calls: list[object] = []

    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            state_path,
            events_path,
            replace(
                context(
                    wf,
                    1,
                    state_path=state_path,
                    with_business_approvals=False,
                ),
                transport=success_transport(transport_calls),
            ),
        ),
        "business_approval",
    )
    assert transport_calls == []
    assert load_workflow_execution_state(state_path).status == "ready"
    assert events_path.read_bytes() == b""


def test_14_business_not_required_does_not_authorize_execution(
    tmp_path: Path,
) -> None:
    wf = workflow(2, business_approval_required=False)
    state_path, events_path = (
        tmp_path / "run-14-not-required.state.json",
        tmp_path / "run-14-not-required.events.jsonl",
    )
    transport_calls: list[object] = []

    with pytest.raises(PersistedStartExecutionCompatibilityError) as caught:
        route_approved_workflow_fresh_start(
            wf,
            state_path,
            events_path,
            replace(
                context(
                    wf,
                    1,
                    state_path=state_path,
                    bad_execution_approval=True,
                    with_business_approvals=False,
                ),
                transport=success_transport(transport_calls),
            ),
        )
    assert caught.value.detail.classification == "request_data"
    assert transport_calls == []
    assert load_workflow_execution_state(state_path).status == "running"
    assert events_path.read_bytes() == b""


def test_15_business_evidence_persistence_failure_skips_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-15-business-persist.state.json",
        tmp_path / "run-15-business-persist.events.jsonl",
    )
    transport_calls: list[object] = []

    def fail_persistence(*_: object, **__: object) -> object:
        raise WorkflowApprovalEvidencePersistenceError("ambiguous")

    monkeypatch.setattr(
        fresh_start_module,
        "persist_business_approval_evidence",
        fail_persistence,
    )
    context_value = context(
        wf,
        1,
        state_path=state_path,
        with_business_approvals=False,
    )
    assert context_value.binding is not None
    explicit_business_approval = approve_business_step(
        binding=context_value.binding,
        workflow_id=wf.id,
        step_id=wf.steps[0].id,
        step_index=1,
        employee_id=wf.steps[0].employee,
        approved_by="reviewer",
        approval_id="business-persistence-failure",
    )
    prepared = replace(
        context_value.preparation_approval,
        business_approval_evidence=explicit_business_approval,
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            state_path,
            events_path,
            replace(
                context_value,
                preparation_approval=prepared,
                transport=success_transport(transport_calls),
            ),
        ),
        "approval_evidence",
    )
    assert transport_calls == []
    assert load_workflow_execution_state(state_path).status == "ready"
    assert events_path.read_bytes() == b""


def test_16_execution_evidence_persistence_failure_skips_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-16-execution-persist.state.json",
        tmp_path / "run-16-execution-persist.events.jsonl",
    )
    transport_calls: list[object] = []

    def fail_persistence(*_: object, **__: object) -> object:
        raise WorkflowApprovalEvidencePersistenceError("ambiguous")

    monkeypatch.setattr(
        fresh_start_module,
        "persist_execution_approval_evidence",
        fail_persistence,
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            state_path,
            events_path,
            replace(
                context(wf, 1, state_path=state_path),
                transport=success_transport(transport_calls),
            ),
        ),
        "approval_evidence",
    )
    assert transport_calls == []


def test_17_durable_business_approval_is_authoritative_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)
    state_path, events_path = (
        tmp_path / "run-17-reused-business.state.json",
        tmp_path / "run-17-reused-business.events.jsonl",
    )
    context_value = context(wf, 1, state_path=state_path)
    assert context_value.binding is not None
    assert context_value.manifest_store is not None
    replacement_store = type(context_value.manifest_store)(state_path.parent)
    replacement_business_approval = approve_business_step(
        binding=context_value.binding,
        workflow_id=wf.id,
        step_id=wf.steps[0].id,
        step_index=1,
        employee_id=wf.steps[0].employee,
        approved_by="replacement-operator",
        approval_id="replacement-business-approval",
    )
    preparation = replace(
        context_value.preparation_approval,
        business_approval_evidence=replacement_business_approval,
    )

    def fail_if_repersisted(*_: object, **__: object) -> object:
        raise AssertionError("durable Business Approval must not be re-persisted")

    monkeypatch.setattr(
        fresh_start_module,
        "persist_business_approval_evidence",
        fail_if_repersisted,
    )
    transport_calls: list[object] = []
    result = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        replace(
            context_value,
            preparation_approval=preparation,
            manifest_store=replacement_store,
            transport=success_transport(transport_calls),
        ),
    )

    assert type(result) is WorkflowProgressionDecision
    assert result.next_step_index == 2
    assert len(transport_calls) == 1
    assert load_workflow_execution_state(state_path).status == "succeeded"


def test_13_execution_failure_and_malformed_output_recover_running_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)

    malformed_state, malformed_events = (
        tmp_path / "run-13-malformed.state.json",
        tmp_path / "run-13-malformed.events.jsonl",
    )
    malformed_calls: list[object] = []

    def malformed(*_: object, **__: object) -> object:
        malformed_calls.append(1)
        return object()

    monkeypatch.setattr(
        fresh_start_module, "execute_persisted_start_openai_step", malformed
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            malformed_state,
            malformed_events,
            replace(
                context(wf, 1, state_path=malformed_state),
                transport=success_transport([]),
            ),
        ),
        "execution_contract",
    )
    assert malformed_calls == [1]
    assert load_workflow_execution_state(malformed_state).status == "running"
    assert malformed_events.read_bytes() == b""

    mutation_state, mutation_events = (
        tmp_path / "run-13-mutation.state.json",
        tmp_path / "run-13-mutation.events.jsonl",
    )
    mutation_calls: list[object] = []
    mutation_snapshot: list[tuple[bytes, bytes]] = []

    def mutated_output(*_: object, **__: object) -> object:
        mutation_calls.append(1)
        mutation_snapshot.append(
            (mutation_state.read_bytes(), mutation_events.read_bytes())
        )
        mutation_state.write_bytes(b"mutated-state")
        mutation_events.write_bytes(b"mutated-events")
        return object()

    monkeypatch.setattr(
        fresh_start_module, "execute_persisted_start_openai_step", mutated_output
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            mutation_state,
            mutation_events,
            replace(
                context(wf, 1, state_path=mutation_state),
                transport=success_transport([]),
            ),
        ),
        "execution_contract",
    )
    assert mutation_calls == [1]
    assert mutation_snapshot
    assert (
        mutation_state.read_bytes(),
        mutation_events.read_bytes(),
    ) == mutation_snapshot[0]
    assert load_workflow_execution_state(mutation_state).status == "running"
    assert mutation_events.read_bytes() == b""

    safe_state, safe_events = (
        tmp_path / "run-13-safe.state.json",
        tmp_path / "run-13-safe.events.jsonl",
    )
    safe_calls: list[object] = []
    safe_error = PersistedStartExecutionCompatibilityError("state_status")

    def safe_failure(*_: object, **__: object) -> object:
        safe_calls.append(1)
        safe_state.write_bytes(b"safe-mutated-state")
        safe_events.write_bytes(b"safe-mutated-events")
        raise safe_error

    monkeypatch.setattr(
        fresh_start_module, "execute_persisted_start_openai_step", safe_failure
    )
    with pytest.raises(PersistedStartExecutionCompatibilityError) as caught_safe:
        route_approved_workflow_fresh_start(
            wf,
            safe_state,
            safe_events,
            replace(
                context(wf, 1, state_path=safe_state), transport=success_transport([])
            ),
        )
    assert caught_safe.value is safe_error
    assert safe_calls == [1]
    assert load_workflow_execution_state(safe_state).status == "running"
    assert safe_events.read_bytes() == b""

    unexpected_state = tmp_path / "run-13-unexpected.state.json"
    unexpected_events = tmp_path / "run-13-unexpected.events.jsonl"
    unexpected_calls: list[object] = []

    def unexpected(*_: object, **__: object) -> object:
        unexpected_calls.append(1)
        unexpected_state.write_bytes(b"unexpected-state")
        unexpected_events.write_bytes(b"unexpected-events")
        raise RuntimeError("secret execution failure")

    monkeypatch.setattr(
        fresh_start_module, "execute_persisted_start_openai_step", unexpected
    )
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError) as caught_unexpected:
        route_approved_workflow_fresh_start(
            wf,
            unexpected_state,
            unexpected_events,
            replace(
                context(wf, 1, state_path=unexpected_state),
                transport=success_transport([]),
            ),
        )
    assert classification(caught_unexpected.value) == "dependency_error"
    assert "secret" not in str(caught_unexpected.value)
    assert unexpected_calls == [1]
    assert load_workflow_execution_state(unexpected_state).status == "running"
    assert unexpected_events.read_bytes() == b""


def test_14_phase172_errors_keep_post_invocation_bytes_without_outer_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wf = workflow(2)

    safe_state, safe_events = (
        tmp_path / "run-14-safe.state.json",
        tmp_path / "run-14-safe.events.jsonl",
    )
    safe_calls: list[object] = []
    safe_error = Phase172Error("synthetic Phase 172 safe error")

    def safe_phase(*_: object, **__: object) -> object:
        safe_calls.append(1)
        safe_state.write_bytes(b"phase172-safe-state")
        safe_events.write_bytes(b"phase172-safe-events")
        raise safe_error

    monkeypatch.setattr(
        fresh_start_module,
        "route_runtime_result_to_progression_orchestration_boundary",
        safe_phase,
    )
    with pytest.raises(Phase172Error) as caught_safe:
        route_approved_workflow_fresh_start(
            wf,
            safe_state,
            safe_events,
            replace(
                context(wf, 1, state_path=safe_state), transport=success_transport([])
            ),
        )
    assert caught_safe.value is safe_error
    assert safe_calls == [1]
    assert safe_state.read_bytes() == b"phase172-safe-state"
    assert safe_events.read_bytes() == b"phase172-safe-events"

    unexpected_state = tmp_path / "run-14-unexpected.state.json"
    unexpected_events = tmp_path / "run-14-unexpected.events.jsonl"
    unexpected_calls: list[object] = []

    def unexpected_phase(*_: object, **__: object) -> object:
        unexpected_calls.append(1)
        unexpected_state.write_bytes(b"phase172-unexpected-state")
        unexpected_events.write_bytes(b"phase172-unexpected-events")
        raise RuntimeError("secret Phase 172 failure")

    monkeypatch.setattr(
        fresh_start_module,
        "route_runtime_result_to_progression_orchestration_boundary",
        unexpected_phase,
    )
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError) as caught_unexpected:
        route_approved_workflow_fresh_start(
            wf,
            unexpected_state,
            unexpected_events,
            replace(
                context(wf, 1, state_path=unexpected_state),
                transport=success_transport([]),
            ),
        )
    assert classification(caught_unexpected.value) == "dependency_error"
    assert "secret" not in str(caught_unexpected.value)
    assert unexpected_calls == [1]
    assert unexpected_state.read_bytes() == b"phase172-unexpected-state"
    assert unexpected_events.read_bytes() == b"phase172-unexpected-events"

    malformed_state = tmp_path / "run-14-malformed.state.json"
    malformed_events = tmp_path / "run-14-malformed.events.jsonl"
    malformed_calls: list[object] = []

    def malformed_phase(*_: object, **__: object) -> object:
        malformed_calls.append(1)
        malformed_state.write_bytes(b"phase172-malformed-state")
        malformed_events.write_bytes(b"phase172-malformed-events")
        return object()

    monkeypatch.setattr(
        fresh_start_module,
        "route_runtime_result_to_progression_orchestration_boundary",
        malformed_phase,
    )
    bootstrap_error(
        lambda: route_approved_workflow_fresh_start(
            wf,
            malformed_state,
            malformed_events,
            replace(
                context(wf, 1, state_path=malformed_state),
                transport=success_transport([]),
            ),
        ),
        "phase172_contract",
    )
    assert malformed_calls == [1]
    assert malformed_state.read_bytes() == b"phase172-malformed-state"
    assert malformed_events.read_bytes() == b"phase172-malformed-events"


def test_15_fresh_start_stops_after_first_step(tmp_path: Path) -> None:
    wf = workflow(3)
    calls: list[object] = []
    state_path, events_path = (
        tmp_path / "run-15.state.json",
        tmp_path / "run-15.events.jsonl",
    )
    bootstrap = context(wf, 1, state_path=state_path)
    first = route_approved_workflow_fresh_start(
        wf,
        state_path,
        events_path,
        replace(bootstrap, transport=success_transport(calls)),
    )
    assert type(first) is WorkflowProgressionDecision
    assert first.decision == "prepare_next_step"
    assert first.current_step_id == "step-1"
    assert first.current_step_index == 1
    assert first.next_step_id == "step-2"
    assert first.next_step_index == 2
    assert calls == [1]
    assert load_workflow_execution_state(state_path).completed_step_ids == ("step-1",)
    history = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(state_path, events_path)
    )
    assert len(history.events) == 1
