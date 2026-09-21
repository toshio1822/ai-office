"""Behavioral tests for the Phase 145 approved-preparation facade."""

# ruff: noqa: E501,E701,E702,F401,I001

import inspect
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition, WorkflowStepDefinition
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    NextStepPreparationError,
    PreparedWorkflowStep,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    PersistedExecutionOutcome,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145CompatibilityError,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as public_phase145,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {"id": "one", "name": "One", "employee": "a", "instructions": "one"},
                {"id": "two", "name": "Two", "employee": "b", "instructions": "two"},
                {
                    "id": "three",
                    "name": "Three",
                    "employee": "c",
                    "instructions": "three",
                },
                {"id": "four", "name": "Four", "employee": "d", "instructions": "four"},
                {"id": "five", "name": "Five", "employee": "e", "instructions": "five"},
                {"id": "six", "name": "Six", "employee": "f", "instructions": "six"},
            ],
        }
    )


def progression(
    supplied_workflow: WorkflowDefinition, index: int = 5
) -> WorkflowProgressionDecision:
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


def predecessor_event(
    step: WorkflowStepDefinition,
    index: int,
    *,
    provider: object = "other",
    request_id: object = "request",
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
        "response",
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
        "api_error" if failed else None,
        None if failed else response_id,
        request_id,
        None if failed else output_text,
        "safe failure" if failed else None,
    )


def data(
    tmp_path: Path,
    *,
    index: int = 5,
    status: str = "succeeded",
    terminal_provider: object = "openai",
    terminal_output: object = "output",
    terminal_request_id: object = "request",
    predecessor_providers: dict[int, object] | None = None,
    predecessor_outputs: dict[int, object] | None = None,
    predecessor_request_ids: dict[int, object] | None = None,
) -> dict[str, object]:
    supplied_workflow = workflow()
    step = supplied_workflow.steps[index - 1]
    predecessor_providers = predecessor_providers or {}
    predecessor_outputs = predecessor_outputs or {}
    predecessor_request_ids = predecessor_request_ids or {}
    predecessors = tuple(
        predecessor_event(
            prior,
            position,
            provider=predecessor_providers.get(
                position, "openai" if position == index - 1 else "other"
            ),
            output_text=predecessor_outputs.get(position, "output"),
            request_id=predecessor_request_ids.get(position, "request"),
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
    )
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = b"".join(
        serialize_runtime_step_event_jsonl(event).encode("utf-8")
        for event in (*predecessors, terminal)
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
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
        "approval": approval(result)
        if type(result) is WorkflowProgressionDecision
        else None,
        "employee": employee(result)
        if type(result) is WorkflowProgressionDecision
        else None,
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }


def completion_data(
    tmp_path: Path,
    *,
    provider: object = "other",
    output: object = "output",
    predecessor_outputs: dict[int, object] | None = None,
) -> dict[str, object]:
    value = data(
        tmp_path,
        index=6,
        terminal_provider=provider,
        terminal_output=output,
        predecessor_outputs=predecessor_outputs,
    )
    value["result"] = WorkflowProgressionDecision(
        "workflow_complete", "w", "six", 6, "f", None, None, None, "last_step_succeeded"
    )
    value["approval"] = None
    value["employee"] = None
    return value


def failure_data(tmp_path: Path, *, provider: object = "other") -> dict[str, object]:
    return data(tmp_path, index=5, status="failed", terminal_provider=provider)


def invoke(value: dict[str, object]) -> object:
    return public_phase145(
        value["result"],
        value["workflow"],
        value["approval"],
        value["employee"],
        value["state_path"],
        value["events_path"],
    )


def before(value: dict[str, object]) -> tuple[bytes, bytes]:
    return (
        Path(value["state_path"]).read_bytes(),
        Path(value["events_path"]).read_bytes(),
    )


def unchanged(value: dict[str, object]) -> None:
    assert before(value) == (value["before_state"], value["before_events"])


def assert_rejected(value: dict[str, object], expected: str) -> None:
    snapshot = before(value)
    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value)
    assert caught.value.detail.classification == expected
    assert before(value) == snapshot


def test_facade_public_signature_omits_phase137_seam() -> None:
    parameters = list(inspect.signature(public_phase145).parameters.values())
    assert [parameter.name for parameter in parameters] == [
        "result",
        "workflow",
        "approval",
        "employee",
        "state_path",
        "events_path",
    ]
    assert all(parameter.annotation is object for parameter in parameters)


@pytest.mark.parametrize("index", [1, 2, 5])
def test_valid_prepare_returns_pure_prepared_step_without_writing(
    tmp_path: Path, index: int
) -> None:
    value = data(tmp_path / str(index), index=index)
    out = invoke(value)
    assert type(out) is PreparedWorkflowStep
    assert out.workflow_id == "w"
    assert out.step_id == value["workflow"].steps[index].id  # type: ignore[union-attr]
    assert out.step_index == index + 1
    assert out.employee_id == value["employee"].id  # type: ignore[union-attr]
    unchanged(value)


def test_direct_facade_calls_pure_preparation_once_with_loaded_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = data(tmp_path)
    expected = PreparedWorkflowStep(
        "w",
        "six",
        6,
        "f",
        "employee instructions",
        "six",
        "model-name",
        ("tool-one", "tool-two"),
    )
    calls: list[tuple[object, ...]] = []

    import ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase145_module

    def prepare(*args: object) -> PreparedWorkflowStep:
        calls.append(args)
        return expected

    monkeypatch.setattr(phase145_module, "prepare_approved_next_workflow_step", prepare)
    out = invoke(value)
    assert out is expected
    assert len(calls) == 1
    workflow_value, history, decision, approval_value, employee_value = calls[0]
    assert workflow_value is value["workflow"]
    assert type(history) is LoadedWorkflowExecutionHistory
    assert decision is value["result"]
    assert approval_value is value["approval"]
    assert employee_value is value["employee"]
    unchanged(value)


def test_removed_phase137_keyword_is_not_accepted(tmp_path: Path) -> None:
    value = data(tmp_path)
    with pytest.raises(TypeError):
        public_phase145(
            value["result"],
            value["workflow"],
            value["approval"],
            value["employee"],
            value["state_path"],
            value["events_path"],
            phase137_function=lambda *_: None,
        )


@pytest.mark.parametrize("field", ["current_step_id", "next_step_id", "reason"])
def test_stale_or_forged_decision_is_rejected_before_history_load(
    tmp_path: Path, field: str
) -> None:
    value = data(tmp_path)
    decision = value["result"]
    replacement = replace(decision, **{field: "forged"})
    value["result"] = replacement
    assert_rejected(value, "decision_contract")


def test_approval_mismatch_is_rejected_before_preparation(tmp_path: Path) -> None:
    value = data(tmp_path)
    value["approval"] = replace(value["approval"], next_employee_id="forged")
    assert_rejected(value, "approval_contract")


def test_employee_mismatch_is_rejected_before_preparation(tmp_path: Path) -> None:
    value = data(tmp_path)
    value["employee"] = value["employee"].model_copy(update={"id": "forged"})
    assert_rejected(value, "employee_contract")


@pytest.mark.parametrize(
    "mutation", [{"workflow_id": "forged"}, {"current_step_id": "forged"}]
)
def test_persisted_history_inconsistency_fails_closed_without_new_write(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    value = data(tmp_path)
    state_path = Path(value["state_path"])
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload.update(mutation)
    state_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    injected = before(value)
    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value)
    assert caught.value.detail.classification == "terminal_contract"
    assert before(value) == injected


def test_preparation_error_is_fail_closed_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = data(tmp_path)
    calls = 0
    import ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase145_module

    def fail_once(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise NextStepPreparationError()

    monkeypatch.setattr(
        phase145_module, "prepare_approved_next_workflow_step", fail_once
    )
    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value)
    assert caught.value.detail.classification == "prepared_contract"
    assert calls == 1
    unchanged(value)


def test_unexpected_preparation_error_is_sanitized_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = data(tmp_path)
    calls = 0
    import ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase145_module

    def fail_once(*_: object) -> object:
        nonlocal calls
        calls += 1
        raise RuntimeError("private detail")

    monkeypatch.setattr(
        phase145_module, "prepare_approved_next_workflow_step", fail_once
    )
    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value)
    assert caught.value.detail.classification == "dependency_error"
    assert calls == 1
    unchanged(value)


@pytest.mark.parametrize("route", ["completion", "failure"])
def test_stop_routes_preserve_identity_and_history_compatibility(
    tmp_path: Path, route: str
) -> None:
    value = (
        completion_data(tmp_path) if route == "completion" else failure_data(tmp_path)
    )
    result = invoke(value)
    assert result is value["result"]
    unchanged(value)


def test_stop_route_accepts_non_openai_provider_and_empty_predecessor_output(
    tmp_path: Path,
) -> None:
    value = completion_data(
        tmp_path,
        provider="local-provider",
        predecessor_outputs={1: ""},
    )
    result = invoke(value)
    assert result is value["result"]
    unchanged(value)


def test_stop_route_rejects_empty_final_success_output(tmp_path: Path) -> None:
    value = completion_data(tmp_path, output="")
    assert_rejected(value, "terminal_contract")


def test_prepare_route_rejects_below_threshold_legacy_none_request_id(
    tmp_path: Path,
) -> None:
    value = data(
        tmp_path,
        index=5,
        predecessor_request_ids={4: None},
    )
    assert_rejected(value, "terminal_contract")


def test_public_error_contains_only_safe_classification(tmp_path: Path) -> None:
    value = data(tmp_path)
    value["result"] = replace(value["result"], workflow_id="forged")
    with pytest.raises(Phase145CompatibilityError) as caught:
        invoke(value)
    assert set(vars(caught.value)) == {"detail"}
    assert caught.value.detail.classification == "decision_contract"
    assert "forged" not in str(caught.value)


def test_no_provider_or_external_execution_is_needed_for_prepare(
    tmp_path: Path,
) -> None:
    value = data(tmp_path)
    out = invoke(value)
    assert type(out) is PreparedWorkflowStep
    assert before(value) == (value["before_state"], value["before_events"])


# Keep the loader import in this focused module as an explicit check that the
# facade validates the same persisted snapshot it passes to pure preparation.
def test_history_loader_reads_both_authoritative_targets(tmp_path: Path) -> None:
    value = data(tmp_path)
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(value["state_path"], value["events_path"])
    )
    assert loaded.state.status == "succeeded"
    assert len(loaded.events) == 5
