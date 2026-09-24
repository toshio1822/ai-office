"""Real Phase 143 classify -> real Phase 144 observable progression with Phase-155
provenance, plus inline negative provenance proofs.

The Phase 144 facade no longer delegates into the Phase 136 and lower historical
progression chain, so this file asserts observable Phase 144 behavior only: the
exact returned progression decision, the identity-preserving stop, the
classification rejections and the read-only state/events guarantee.
"""

# ruff: noqa: E501,E701,E702,F401,I001

from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import PersistedExecutionOutcome, WorkflowProgressionDecision
from ai_office.engine.classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ClassifiedPersistedOutcomeProgressionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as Phase144CompatError,
    route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.engine.persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceResult,
    WorkflowExecutionPersistenceTargets,
    load_workflow_execution_history,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_STEP_IDS = ("one", "two", "three", "four", "five", "six")
_SENTINEL = object()


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": step_id,
                    "name": step_id.capitalize(),
                    "employee": step_id[0],
                    "instructions": step_id,
                }
                for step_id in _STEP_IDS
            ],
        }
    )


def predecessor_event(
    step_id: str,
    position: int,
    provider: object = "other",
    request_id: object = _SENTINEL,
    output_text: object = "output",
) -> RuntimeStepEvent:
    resolved_request_id = f"request-{step_id}" if request_id is _SENTINEL else request_id
    return RuntimeStepEvent(
        "step_succeeded",
        "w",
        step_id,
        position,
        step_id[0],
        "running",
        "succeeded",
        provider,  # type: ignore[arg-type]
        None,
        f"response-{step_id}",
        resolved_request_id,  # type: ignore[arg-type]
        output_text,  # type: ignore[arg-type]
        None,
    )


def terminal_event(status: str) -> RuntimeStepEvent:
    if status == "succeeded":
        return RuntimeStepEvent(
            "step_succeeded",
            "w",
            "six",
            6,
            "s",
            "running",
            "succeeded",
            "openai",
            None,
            "response-six",
            "request-six",
            "output-six",
            None,
        )
    return RuntimeStepEvent(
        "step_failed",
        "w",
        "six",
        6,
        "s",
        "running",
        "failed",
        "openai",
        "api_error",
        None,
        "request-six",
        None,
        "safe failure",
    )


def setup(
    tmp_path: Path, status: str, *, earlier_empty: tuple[int, ...] = (2,)
) -> dict[str, object]:
    supplied_workflow = workflow()
    state = WorkflowExecutionState(
        "w",
        status,
        "six",
        6,
        "s",
        tuple(_STEP_IDS) if status == "succeeded" else tuple(_STEP_IDS[:5]),
        None if status == "succeeded" else "api_error",
    )
    events = [
        predecessor_event(
            step_id,
            position,
            output_text="" if position in earlier_empty else "output",
        )
        for position, step_id in enumerate(_STEP_IDS[:5], 1)
    ]
    events[4] = predecessor_event(
        "five", 5, provider="openai", request_id=None, output_text=""
    )
    events.append(terminal_event(status))
    state_bytes = serialize_workflow_execution_state_json(state).encode("utf-8")
    event_bytes = "".join(
        serialize_runtime_step_event_jsonl(event) for event in events
    ).encode("utf-8")
    terminal_bytes = serialize_runtime_step_event_jsonl(events[-1]).encode("utf-8")
    state_path, events_path = tmp_path / "state", tmp_path / "events"
    state_path.write_bytes(state_bytes)
    events_path.write_bytes(event_bytes)
    result = WorkflowExecutionPersistenceResult(
        state_path,
        events_path,
        len(state_bytes),
        len(terminal_bytes),
    )
    return {
        "result": result,
        "workflow": supplied_workflow,
        "state_path": state_path,
        "events_path": events_path,
    }


def reload_and_assert_provenance(
    values: dict[str, object], status: str, *, earlier_empty: tuple[int, ...] = (2,)
) -> None:
    """Reload persisted state/history via the public storage loader and assert
    the Issue #330 Phase-155 provenance facts before invocation."""
    loaded = load_workflow_execution_history(
        WorkflowExecutionPersistenceTargets(
            values["state_path"],  # type: ignore[arg-type]
            values["events_path"],  # type: ignore[arg-type]
        )
    )
    state, events = loaded.state, loaded.events
    for position in earlier_empty:
        assert events[position - 1].step_id == _STEP_IDS[position - 1]
        assert events[position - 1].output_text == ""
    # Issue #341: earlier predecessor request IDs stay exact non-empty built-in
    # strings at Phase 144.
    for position in (1, 2, 3, 4):
        assert isinstance(events[position - 1].request_id, str)
        assert events[position - 1].request_id
    assert events[4].step_id == "five"
    assert events[4].output_text == ""
    assert events[4].request_id is None
    # Issue #341: the immediate predecessor provider is exactly "openai" where
    # the existing boundary requires it.
    assert events[4].provider == "openai"
    assert state.status == status
    assert state.current_step_id == "six"
    assert state.current_step_index == 6
    assert state.current_employee_id == "s"
    assert state.completed_step_ids == (
        tuple(_STEP_IDS) if status == "succeeded" else tuple(_STEP_IDS[:5])
    )
    assert state.last_failure_category == (
        None if status == "succeeded" else "api_error"
    )
    terminal = events[-1]
    assert terminal.step_id == "six"
    assert terminal.step_index == 6
    assert terminal.employee_id == "s"
    assert terminal.provider == "openai"
    if status == "succeeded":
        assert terminal.event_type == "step_succeeded"
        assert terminal.next_status == "succeeded"
        assert terminal.failure_category is None
        assert terminal.response_id == "response-six"
        assert terminal.request_id == "request-six"
        assert terminal.output_text == "output-six"
        assert terminal.message is None
    else:
        assert terminal.event_type == "step_failed"
        assert terminal.next_status == "failed"
        assert terminal.failure_category == "api_error"
        assert terminal.response_id is None
        assert terminal.request_id == "request-six"
        assert terminal.output_text is None
        assert terminal.message == "safe failure"


def expected_decision() -> WorkflowProgressionDecision:
    return WorkflowProgressionDecision(
        "workflow_complete",
        "w",
        "six",
        6,
        "s",
        None,
        None,
        None,
        "last_step_succeeded",
    )


def classify(values: dict[str, object], status: str) -> PersistedExecutionOutcome:
    """Run the real Phase 143 public classification boundary on the persisted
    result and require an exact PersistedExecutionOutcome with the exact Issue
    #341 Phase-155 fields."""
    outcome = route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        values["result"],  # type: ignore[arg-type]
        values["workflow"],  # type: ignore[arg-type]
        values["state_path"],  # type: ignore[arg-type]
        values["events_path"],  # type: ignore[arg-type]
    )
    assert type(outcome) is PersistedExecutionOutcome
    assert outcome.outcome == (
        "persisted_success" if status == "succeeded" else "persisted_failure"
    )
    assert outcome.workflow_id == "w"
    assert outcome.current_step_id == "six"
    assert outcome.current_step_index == 6
    assert outcome.current_employee_id == "s"
    assert outcome.failure_category == (
        None if status == "succeeded" else "api_error"
    )
    return outcome


def progress(
    values: dict[str, object], outcome: PersistedExecutionOutcome
) -> object:
    """Call the real Phase 144 default route (no synthetic seam)."""
    return route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
        outcome,
        values["workflow"],  # type: ignore[arg-type]
        values["state_path"],  # type: ignore[arg-type]
        values["events_path"],  # type: ignore[arg-type]
    )


def snapshot(values: dict[str, object]) -> tuple[bytes, bytes]:
    return (
        values["state_path"].read_bytes(),  # type: ignore[union-attr]
        values["events_path"].read_bytes(),  # type: ignore[union-attr]
    )


def test_real_classification_success_progresses_to_exact_workflow_complete(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    reload_and_assert_provenance(values, "succeeded")
    before = snapshot(values)
    outcome = classify(values, "succeeded")
    out = progress(values, outcome)
    assert type(out) is WorkflowProgressionDecision
    assert out == expected_decision()
    assert out.decision == "workflow_complete"
    assert out.current_step_id == "six"
    assert out.current_step_index == 6
    assert out.next_step_id is None
    assert out.next_step_index is None
    assert out.next_employee_id is None
    assert out.reason == "last_step_succeeded"
    assert snapshot(values) == before


def test_real_classification_failure_stops_identity_preserving_read_only(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "failed")
    reload_and_assert_provenance(values, "failed")
    before = snapshot(values)
    outcome = classify(values, "failed")
    assert outcome.outcome == "persisted_failure"
    out = progress(values, outcome)
    assert out is outcome
    assert snapshot(values) == before


def test_real_classification_multiple_earlier_empty_success_progresses(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded", earlier_empty=(2, 3))
    reload_and_assert_provenance(values, "succeeded", earlier_empty=(2, 3))
    before = snapshot(values)
    outcome = classify(values, "succeeded")
    out = progress(values, outcome)
    assert type(out) is WorkflowProgressionDecision
    assert out == expected_decision()
    assert snapshot(values) == before


def test_real_classification_multiple_earlier_empty_failure_stops(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "failed", earlier_empty=(2, 3))
    reload_and_assert_provenance(values, "failed", earlier_empty=(2, 3))
    before = snapshot(values)
    outcome = classify(values, "failed")
    assert outcome.outcome == "persisted_failure"
    out = progress(values, outcome)
    assert out is outcome
    assert snapshot(values) == before


def test_step2_output_none_mutation_is_rejected_at_phase144(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    outcome = classify(values, "succeeded")
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("two", 2, "other", output_text=None)
    )
    events.write_text(lines[0] + replacement + "".join(lines[2:]), encoding="utf-8")  # type: ignore[union-attr]
    before = values["state_path"].read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    with pytest.raises(Phase144CompatError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            outcome,
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            events,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert (values["state_path"].read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]


def test_step5_output_non_string_mutation_is_rejected_at_phase144(
    tmp_path: Path,
) -> None:
    values = setup(tmp_path, "succeeded")
    outcome = classify(values, "succeeded")
    events = values["events_path"]
    lines = events.read_text(encoding="utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    replacement = serialize_runtime_step_event_jsonl(
        predecessor_event("five", 5, provider="openai", request_id=None, output_text=1)
    )
    events.write_text("".join(lines[:4]) + replacement + "".join(lines[5:]), encoding="utf-8")  # type: ignore[union-attr]
    before = values["state_path"].read_bytes(), events.read_bytes()  # type: ignore[union-attr]
    with pytest.raises(Phase144CompatError) as caught:
        route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(
            outcome,
            values["workflow"],  # type: ignore[arg-type]
            values["state_path"],  # type: ignore[arg-type]
            events,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert (values["state_path"].read_bytes(), events.read_bytes()) == before  # type: ignore[union-attr]
