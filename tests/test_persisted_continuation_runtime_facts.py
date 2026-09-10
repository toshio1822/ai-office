"""Focused Phase-260 persisted continuation runtime-facts tests."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from ai_office.engine import (
    PersistedContinuationRuntimeFactsError,
    build_persisted_continuation_runtime_facts,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    UpstreamStepOutput,
    build_model_invocation_execution_fingerprint,
    build_model_invocation_task_input,
    serialize_runtime_facts_snapshot_canonical,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    LoadedWorkflowExecutionHistory,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history_with_source_digests,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_STATE = WorkflowExecutionState(
    workflow_id="workflow",
    status="succeeded",
    current_step_id="step-1",
    current_step_index=1,
    current_employee_id="employee-1",
    completed_step_ids=("step-1",),
    last_failure_category=None,
)
_EVENT = RuntimeStepEvent(
    event_type="step_succeeded",
    workflow_id="workflow",
    step_id="step-1",
    step_index=1,
    employee_id="employee-1",
    previous_status="running",
    next_status="succeeded",
    provider="openai",
    failure_category=None,
    response_id="response-secret-like-id",
    request_id="request-secret-like-id",
    output_text="business output must remain Policy A",
    message=None,
)


def history(
    state: WorkflowExecutionState = _STATE,
    event: RuntimeStepEvent = _EVENT,
) -> LoadedWorkflowExecutionHistory:
    return LoadedWorkflowExecutionHistory(state=state, events=(event,))


def state_bytes(state: WorkflowExecutionState = _STATE) -> bytes:
    return serialize_workflow_execution_state_json(state).encode("utf-8")


def event_bytes(event: RuntimeStepEvent = _EVENT) -> bytes:
    return serialize_runtime_step_event_jsonl(event).encode("utf-8")


def state_digest(state: WorkflowExecutionState = _STATE) -> str:
    return sha256(state_bytes(state)).hexdigest()


def snapshot(
    value: LoadedWorkflowExecutionHistory | None = None,
    *,
    source_sha256: str | None = None,
):
    value = history() if value is None else value
    return build_persisted_continuation_runtime_facts(
        "workflow",
        2,
        value,
        state_source_sha256=state_digest(value.state)
        if source_sha256 is None
        else source_sha256,
    )


def request(value=None):
    value = snapshot() if value is None else value
    return ModelInvocationRequest(
        model="model",
        system_instructions="system instructions",
        task_instructions="task instructions",
        allowed_tools=(),
        upstream_inputs=(
            UpstreamStepOutput(
                "workflow",
                "step-1",
                1,
                "employee-1",
                _EVENT.output_text or "",
            ),
        ),
        runtime_facts=value,
    )


def test_exact_v1_fact_set_and_provenance_digests() -> None:
    value = snapshot()
    assert [fact.key for fact in value.facts] == [
        "predecessor.employee_id",
        "predecessor.provider",
        "predecessor.step_id",
        "predecessor.step_index",
        "workflow.completed_step_count",
        "workflow.status",
    ]
    assert {fact.key: (fact.value_kind, fact.value) for fact in value.facts} == {
        "workflow.status": ("enum", "succeeded"),
        "workflow.completed_step_count": ("integer", 1),
        "predecessor.step_id": ("identifier", "step-1"),
        "predecessor.step_index": ("integer", 1),
        "predecessor.employee_id": ("identifier", "employee-1"),
        "predecessor.provider": ("enum", "openai"),
    }
    expected_state_sha = state_digest()
    expected_event_sha = sha256(event_bytes()).hexdigest()
    for fact in value.facts:
        assert fact.provenance.workflow_id == "workflow"
        assert fact.provenance.observed_at is None
        if fact.key.startswith("workflow."):
            assert fact.provenance.origin == "persisted_state"
            assert fact.provenance.source_ref == "state"
            assert fact.provenance.source_sha256 == expected_state_sha
        else:
            assert fact.provenance.origin == "persisted_event"
            assert fact.provenance.source_ref == "event:1"
            assert fact.provenance.source_sha256 == expected_event_sha


def test_canonical_snapshot_and_digest_are_pinned() -> None:
    value = snapshot()
    canonical = serialize_runtime_facts_snapshot_canonical(value)
    assert canonical == (
        '{"facts":[{"key":"predecessor.employee_id",'
        '"provenance":{"observed_at":null,"origin":"persisted_event",'
        '"source_ref":"event:1","source_sha256":"'
        + sha256(event_bytes()).hexdigest()
        + '","workflow_id":"workflow"},"value":"employee-1",'
        '"value_kind":"identifier"},{"key":"predecessor.provider",'
        '"provenance":{"observed_at":null,"origin":"persisted_event",'
        '"source_ref":"event:1","source_sha256":"'
        + sha256(event_bytes()).hexdigest()
        + '","workflow_id":"workflow"},"value":"openai",'
        '"value_kind":"enum"},{"key":"predecessor.step_id",'
        '"provenance":{"observed_at":null,"origin":"persisted_event",'
        '"source_ref":"event:1","source_sha256":"'
        + sha256(event_bytes()).hexdigest()
        + '","workflow_id":"workflow"},"value":"step-1",'
        '"value_kind":"identifier"},{"key":"predecessor.step_index",'
        '"provenance":{"observed_at":null,"origin":"persisted_event",'
        '"source_ref":"event:1","source_sha256":"'
        + sha256(event_bytes()).hexdigest()
        + '","workflow_id":"workflow"},"value":1,"value_kind":"integer"},'
        '{"key":"workflow.completed_step_count",'
        '"provenance":{"observed_at":null,"origin":"persisted_state",'
        '"source_ref":"state","source_sha256":"'
        + state_digest()
        + '","workflow_id":"workflow"},"value":1,"value_kind":"integer"},'
        '{"key":"workflow.status","provenance":{"observed_at":null,'
        '"origin":"persisted_state","source_ref":"state",'
        '"source_sha256":"'
        + state_digest()
        + '","workflow_id":"workflow"},"value":"succeeded",'
        '"value_kind":"enum"}],"schema_version":"runtime-facts.v1"}'
    )
    assert value.digest == sha256(canonical.encode("utf-8")).hexdigest()
    assert (
        value.digest
        == "26237a4d50bdf31a119d52a3ab0ca811961e910aba9488e7ff9c4af6d846c4a4"
    )


def test_same_history_produces_same_task_input_and_fingerprint() -> None:
    first = request()
    second = request(snapshot())
    assert first.runtime_facts == second.runtime_facts
    assert build_model_invocation_task_input(
        first
    ) == build_model_invocation_task_input(second)
    assert build_model_invocation_execution_fingerprint(first, ()) == (
        build_model_invocation_execution_fingerprint(second, ())
    )


def test_source_digest_changes_invalidate_facts_and_fingerprint() -> None:
    first = request()
    changed_state_digest = request(snapshot(source_sha256="b" * 64))
    changed_event = replace(_EVENT, provider="omniroute")
    changed_event_request = request(snapshot(history(event=changed_event)))
    fingerprint = build_model_invocation_execution_fingerprint(first, ())
    assert first.runtime_facts != changed_state_digest.runtime_facts
    assert first.runtime_facts != changed_event_request.runtime_facts
    assert (
        build_model_invocation_execution_fingerprint(changed_state_digest, ())
        != fingerprint
    )
    assert (
        build_model_invocation_execution_fingerprint(changed_event_request, ())
        != fingerprint
    )


def test_policy_a_output_is_separate_and_never_a_runtime_fact() -> None:
    value = snapshot()
    task_input = json.loads(build_model_invocation_task_input(request(value)))
    assert task_input["upstream_inputs"][0]["output_text"] == _EVENT.output_text
    assert "output_text" not in {
        key for fact in value.facts for key in (fact.key, str(fact.value))
    }
    assert "response-secret-like-id" not in serialize_runtime_facts_snapshot_canonical(
        value
    )
    assert (
        "business output must remain Policy A"
        not in serialize_runtime_facts_snapshot_canonical(value)
    )


def test_only_immediate_predecessor_is_selected() -> None:
    earlier = replace(_EVENT, step_id="step-0", step_index=1, employee_id="employee-0")
    immediate = replace(
        _EVENT, step_id="step-1", step_index=2, employee_id="employee-1"
    )
    state = replace(
        _STATE,
        current_step_id="step-1",
        current_step_index=2,
        completed_step_ids=("step-0", "step-1"),
    )
    value = build_persisted_continuation_runtime_facts(
        "workflow",
        3,
        LoadedWorkflowExecutionHistory(state, (earlier, immediate)),
        state_source_sha256=state_digest(state),
    )
    assert {
        fact.key: fact.value
        for fact in value.facts
        if fact.key.startswith("predecessor.")
    }["predecessor.step_id"] == "step-1"


@pytest.mark.parametrize(
    "bad_history",
    [
        LoadedWorkflowExecutionHistory(replace(_STATE, status="running"), (_EVENT,)),
        LoadedWorkflowExecutionHistory(replace(_STATE, workflow_id="other"), (_EVENT,)),
        LoadedWorkflowExecutionHistory(
            replace(_STATE, current_step_index=2), (_EVENT,)
        ),
        LoadedWorkflowExecutionHistory(_STATE, ()),
        LoadedWorkflowExecutionHistory(_STATE, (_EVENT, _EVENT)),
        LoadedWorkflowExecutionHistory(
            _STATE, (replace(_EVENT, event_type="step_failed"),)
        ),
    ],
)
def test_invalid_or_ambiguous_history_is_rejected_without_details(
    bad_history: LoadedWorkflowExecutionHistory,
) -> None:
    with pytest.raises(PersistedContinuationRuntimeFactsError) as caught:
        build_persisted_continuation_runtime_facts(
            "workflow", 2, bad_history, state_source_sha256=state_digest()
        )
    assert str(caught.value) == "persisted continuation runtime facts are invalid"
    assert "business output" not in str(caught.value)
    assert "response-secret-like-id" not in str(caught.value)


def test_loader_returns_exact_source_digests_without_schema_change(
    tmp_path: Path,
) -> None:
    targets = WorkflowExecutionPersistenceTargets(
        tmp_path / "state.json", tmp_path / "events.jsonl"
    )
    targets.state_path.write_bytes(state_bytes())
    targets.events_path.write_bytes(event_bytes())
    loaded, actual_state_sha, actual_events_sha = (
        load_workflow_execution_history_with_source_digests(targets)
    )
    assert loaded == history()
    assert actual_state_sha == sha256(state_bytes()).hexdigest()
    assert actual_events_sha == sha256(event_bytes()).hexdigest()
    assert set(json.loads(targets.state_path.read_text()).keys()) == {
        "workflow_id",
        "status",
        "current_step_id",
        "current_step_index",
        "current_employee_id",
        "completed_step_ids",
        "last_failure_category",
    }
