"""Real Phase 178 -> Phase 145 accumulated-None approved-preparation compatibility (Issue #386).

The real Phase 178 public boundary, driven with a synthetic successful
transport, legitimately produces a durable predecessor history where step 5
and step 6 both succeeded with ``request_id=None`` (provider ``"openai"``)
and step 7 then succeeded with a non-empty request id, yielding an exact
``prepare_next_step`` decision for step 8.  Before Issue #386 the
approved-preparation entry layers (Phase 145 outer-chain and Phase 137 outer)
independently rejected any non-immediate predecessor with ``request_id=None``
via ``terminal_contract``, so a real accumulated-None provenance could not
cross the approved-preparation entry seam.

Issue #386 adds a bounded ``allow_accumulated_openai_none`` relaxation: on the
prepare route only, an aged predecessor at position >= 5 whose provider is
exactly openai and whose request_id is None is accepted when
``current_step_index >= 7``.  These tests prove the real Phase 145 default
chain (real Phase 137 -> real Phase 130/lower) prepares step 8 from the exact
real Phase 178 prepare decision, while unrelated strictness (non-openai
accumulated None, below-threshold positions, aged-None on the stop route)
remains enforced.

Requirement-to-test mapping (Issue #386):
- A -> test_a_real_phase178_accumulated_none_prepares_step8
- B -> test_b_non_contiguous_accumulated_openai_none_prepares_step8
- C -> test_c_inline_strict_prepare_negatives
- D -> test_d_canonical_stop_routes_exact_identity_aged_none_strict
"""

# ruff: noqa: E501,E701,E702,F401,I001

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.engine import (
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as real_phase145,
)
from ai_office.engine import (
    route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as real_phase137,
)
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase145Error,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_HARNESS178 = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_orchestration_boundary.py"
)


def _harness178():
    spec = importlib.util.spec_from_file_location("_phase178_harness", _HARNESS178)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _full_execution_approval(harness, workflow):
    """Approve the exact Phase-178 step-7 request, including predecessor data."""
    employee = harness.employee_for(harness.prepare_decision(workflow, 6))
    step = workflow.steps[6]
    request = ModelInvocationRequest(
        employee.model,
        employee.instructions,
        step.instructions,
        tuple(employee.allowed_tools),
        (
            UpstreamStepOutput(
                workflow.id,
                workflow.steps[5].id,
                6,
                workflow.steps[5].employee,
                "output",
            ),
        ),
    )
    return approve_model_invocation_execution(
        request,
        harness.TOOLS,
        provider="openai",
        approved_by="test",
        approval_id="approval-id",
    )


def _phase178_prepare_step8(tmp_path: Path) -> tuple[dict, WorkflowProgressionDecision]:
    """Drive the real Phase 178 public boundary with a synthetic successful
    transport to obtain the exact prepare_next_step(step8) decision and the
    durable step5/step6-None provenance it leaves behind."""
    harness = _harness178()
    root = tmp_path / "scenario"
    values = harness.canonical_running_setup(root, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[assignment]
    state_path: Path = values["state_path"]  # type: ignore[assignment]
    events_path: Path = values["events_path"]  # type: ignore[assignment]
    result = harness.runtime_success(wf, 6, request_id_none=True)
    decision = harness.prepare_decision(wf, 6)
    approval = harness.approval_for(decision)
    employee = harness.employee_for(decision)
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = _full_execution_approval(harness, wf)
    calls: list = []
    out = harness.phase178(
        result,
        wf,
        approval,
        employee,
        state_path,
        events_path,
        harness.TOOLS,
        api_key,
        execution_approval,
        harness.success_transport(calls),
    )
    assert type(out) is WorkflowProgressionDecision
    assert out.decision == "prepare_next_step"
    assert out.current_step_id == "step-7"
    assert out.current_step_index == 7
    assert out.next_step_id == "step-8"
    assert out.next_step_index == 8
    events = harness.loaded_events(root)
    assert [e.step_id for e in events] == [f"step-{i}" for i in range(1, 8)]
    assert events[4].request_id is None and events[4].provider == "openai"
    assert events[5].request_id is None and events[5].provider == "openai"
    assert len(events) == 7
    assert len(calls) == 1
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }, out


def _write_prepare_scenario(
    tmp_path: Path,
    *,
    index: int,
    none_positions: tuple[int, ...] = (),
    non_openai_positions: tuple[int, ...] = (),
) -> dict:
    """Write a prepare-route durable snapshot: prior steps 1..index-1 plus a
    succeeded terminal at `index`, with the requested None/openai overrides."""
    harness = _harness178()
    wf = harness.workflow(9)
    step = wf.steps[index - 1]
    predecessors = []
    for position, prior in enumerate(wf.steps[: index - 1], 1):
        changes: dict = {}
        if position in none_positions:
            changes["request_id"] = None
            changes["provider"] = "openai"
        if position in non_openai_positions:
            changes["provider"] = "anthropic"
        predecessors.append(
            harness.predecessor_event(prior.id, position, **changes)
        )
    terminal = harness.predecessor_event(
        step.id, index, provider="openai", request_id="request", output_text="output"
    )
    state = WorkflowExecutionState(
        "w",
        "succeeded",
        step.id,
        index,
        step.employee,
        tuple(item.id for item in wf.steps[:index]),
        None,
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
    decision = harness.prepare_decision(wf, index)
    return {
        "result": decision,
        "workflow": wf,
        "approval": harness.approval_for(decision),
        "employee": harness.employee_for(decision),
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }


def test_a_real_phase178_accumulated_none_prepares_step8(tmp_path: Path) -> None:
    # Issue #386: the exact prepare_next_step(step8) produced by the real
    # Phase 178 boundary (with accumulated step5/step6 openai None) crosses
    # the real Phase 145 default chain (real Phase 137 -> Phase 130/lower) and
    # yields an exact PreparedWorkflowStep(step8).
    scenario, decision = _phase178_prepare_step8(tmp_path)
    wf = scenario["workflow"]
    approval = _harness178().approval_for(decision)
    employee = _harness178().employee_for(decision)
    out = real_phase145(
        decision,
        wf,
        approval,
        employee,
        scenario["state_path"],
        scenario["events_path"],
        phase137_function=real_phase137,
    )
    assert type(out) is PreparedWorkflowStep
    assert out.workflow_id == "w"
    assert out.step_id == "step-8"
    assert out.step_index == 8
    # durable inputs unchanged through the real Phase 145 chain
    assert scenario["state_path"].read_bytes() == scenario["state_before"]
    assert scenario["events_path"].read_bytes() == scenario["events_before"]


def test_b_non_contiguous_accumulated_openai_none_prepares_step8(
    tmp_path: Path,
) -> None:
    # Non-contiguous accumulated control: only position 5 is None/openai,
    # position 6 keeps a non-empty request id, terminal step 7 succeeded.
    # The real Phase 145 default chain still prepares step 8.
    value = _write_prepare_scenario(tmp_path, index=7, none_positions=(5,))
    out = real_phase145(
        value["result"],
        value["workflow"],
        value["approval"],
        value["employee"],
        value["state_path"],
        value["events_path"],
        phase137_function=real_phase137,
    )
    assert type(out) is PreparedWorkflowStep
    assert out.step_id == "step-8" and out.step_index == 8
    assert value["state_path"].read_bytes() == value["before_state"]
    assert value["events_path"].read_bytes() == value["before_events"]


def test_c_inline_strict_prepare_negatives(tmp_path: Path) -> None:
    # (a) accumulated None at position 4 (below threshold >= 5) is rejected
    # and the target bytes are unchanged.
    below = _write_prepare_scenario(
        tmp_path / "below", index=7, none_positions=(4,)
    )
    with pytest.raises(Phase145Error) as caught:
        real_phase145(
            below["result"],
            below["workflow"],
            below["approval"],
            below["employee"],
            below["state_path"],
            below["events_path"],
            phase137_function=real_phase137,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert below["state_path"].read_bytes() == below["before_state"]
    assert below["events_path"].read_bytes() == below["before_events"]

    # (b) accumulated None at position 5 with a non-openai provider is
    # rejected and the target bytes are unchanged.
    non_openai = _write_prepare_scenario(
        tmp_path / "nonopenai", index=7, none_positions=(5,), non_openai_positions=(5,)
    )
    with pytest.raises(Phase145Error) as caught:
        real_phase145(
            non_openai["result"],
            non_openai["workflow"],
            non_openai["approval"],
            non_openai["employee"],
            non_openai["state_path"],
            non_openai["events_path"],
            phase137_function=real_phase137,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert non_openai["state_path"].read_bytes() == non_openai["before_state"]
    assert non_openai["events_path"].read_bytes() == non_openai["before_events"]


def test_d_canonical_stop_routes_exact_identity_aged_none_strict(tmp_path: Path) -> None:
    harness = _harness178()

    # (a) canonical workflow_complete stop: exact identity + bytes unchanged.
    d1 = tmp_path / "d1"
    values = harness.terminal_setup(d1, steps=6)
    wf = values["workflow"]  # type: ignore[assignment]
    state_path: Path = values["state_path"]  # type: ignore[assignment]
    events_path: Path = values["events_path"]  # type: ignore[assignment]
    committed_state = state_path.read_bytes()
    committed_events = events_path.read_bytes()
    stop = harness.complete_decision(wf, 6)
    out = real_phase145(
        stop,
        wf,
        None,
        None,
        state_path,
        events_path,
        phase137_function=real_phase137,
    )
    assert out is stop
    assert state_path.read_bytes() == committed_state
    assert events_path.read_bytes() == committed_events

    # (b) canonical persisted_failure stop: exact identity + bytes unchanged.
    d2 = tmp_path / "d2"
    values2 = harness.terminal_setup(d2, steps=6, fail=True)
    wf2 = values2["workflow"]  # type: ignore[assignment]
    state_path2: Path = values2["state_path"]  # type: ignore[assignment]
    events_path2: Path = values2["events_path"]  # type: ignore[assignment]
    committed_state2 = state_path2.read_bytes()
    committed_events2 = events_path2.read_bytes()
    stop2 = harness.failure_outcome(wf2, 6)
    out2 = real_phase145(
        stop2,
        wf2,
        None,
        None,
        state_path2,
        events_path2,
        phase137_function=real_phase137,
    )
    assert out2 is stop2
    assert state_path2.read_bytes() == committed_state2
    assert events_path2.read_bytes() == committed_events2

    # (c) aged-None workflow_complete stop must remain strict: the accumulated
    # relaxation is gated to the prepare route only (``not stop``), so an
    # aged openai None at positions 5 and 6 is still rejected on the stop
    # route, proving stop semantics are not widened.
    d3 = tmp_path / "d3"
    values3 = harness.terminal_setup(d3, steps=8)
    wf3 = values3["workflow"]  # type: ignore[assignment]
    state_path3: Path = values3["state_path"]  # type: ignore[assignment]
    events_path3: Path = values3["events_path"]  # type: ignore[assignment]
    lines = events_path3.read_text(encoding="utf-8").splitlines()
    for idx in (4, 5):
        payload = json.loads(lines[idx])
        payload["request_id"] = None
        payload["provider"] = "openai"
        lines[idx] = json.dumps(payload, separators=(",", ":"))
    events_path3.write_text("\n".join(lines) + "\n", encoding="utf-8")
    injected_events = events_path3.read_bytes()
    stop3 = harness.complete_decision(wf3, 8)
    with pytest.raises(Phase145Error) as caught:
        real_phase145(
            stop3,
            wf3,
            None,
            None,
            state_path3,
            events_path3,
            phase137_function=real_phase137,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert state_path3.read_bytes() == values3["state_before"]
    assert events_path3.read_bytes() == injected_events

    # (d) aged-None persisted_failure stop must remain strict as well.
    d4 = tmp_path / "d4"
    values4 = harness.terminal_setup(d4, steps=8, fail=True)
    wf4 = values4["workflow"]  # type: ignore[assignment]
    state_path4: Path = values4["state_path"]  # type: ignore[assignment]
    events_path4: Path = values4["events_path"]  # type: ignore[assignment]
    lines4 = events_path4.read_text(encoding="utf-8").splitlines()
    for idx in (4, 5):
        payload = json.loads(lines4[idx])
        payload["request_id"] = None
        payload["provider"] = "openai"
        lines4[idx] = json.dumps(payload, separators=(",", ":"))
    events_path4.write_text("\n".join(lines4) + "\n", encoding="utf-8")
    injected_events4 = events_path4.read_bytes()
    stop4 = harness.failure_outcome(wf4, 8)
    with pytest.raises(Phase145Error) as caught:
        real_phase145(
            stop4,
            wf4,
            None,
            None,
            state_path4,
            events_path4,
            phase137_function=real_phase137,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert state_path4.read_bytes() == values4["state_before"]
    assert events_path4.read_bytes() == injected_events4
