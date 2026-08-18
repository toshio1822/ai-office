"""Real accumulated-None approved-preparation-entry regression (Issue #386).

A real Phase 177/172 continuation can legitimately produce durable
predecessor history where step 5 and step 6 both succeeded with
``request_id=None`` (provider ``"openai"``) and step 7 then succeeded with a
non-empty request id, yielding a ``prepare_next_step`` decision for step 8.

Before Issue #386 the approved-preparation entry layers (Phase 145 outer-chain
and Phase 137 outer) independently rejected any non-immediate predecessor with
``request_id=None`` via ``terminal_contract``, so a real accumulated-None
provenance could not cross the approved-preparation entry seam.  Issue #386
adds a bounded ``allow_accumulated_openai_none`` relaxation: on the prepare
route only, an aged predecessor at position >= 5 whose provider is exactly
openai and whose request_id is None is accepted when ``current_step_index >= 7``.

These tests drive the real public Phase 145 route (real default Phase 137 -> 
real Phase 130/lower chain) against the exact scenario produced by the real
Phase 177 -> real Phase 172 harness, proving the whole real chain prepares
step 8 without reconstruction or rewriting, while unrelated strictness
(non-openai accumulated None, below-threshold positions) remains enforced.

Requirement-to-test mapping (Issue #386):
- A: real accumulated-None provenance -> real Phase 145 -> real Phase 137 ->
  real Phase 130/lower -> exact PreparedWorkflowStep(step 8), bytes unchanged
  -> test_real_a_accumulated_none_prepares_step8
- B: stop route (workflow_complete) remains strict: no accumulated-None
  relaxation on the stop route
  -> test_real_b_stop_route_remains_strict
- C: accumulated None at a below-threshold position (position < 5) remains
  rejected terminal_contract
  -> test_real_c_below_threshold_position_rejected
- D: non-openai accumulated None at an aged position remains rejected
  terminal_contract
  -> test_real_d_non_openai_accumulated_none_rejected
"""

# ruff: noqa: E501,E701,E702,F401,I001

import importlib.util
from pathlib import Path

import pytest

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
from ai_office.engine.progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    ProgressionToApprovedPreparationCycleHandoffChainBridgeOuterReentryContinuationError as Phase137Error,
)

_HARNESS = Path(__file__).with_name(
    "test_phase177_phase172_accumulated_request_id_none_persistence_classification_progression_compatibility.py"
)


def _harness():
    spec = importlib.util.spec_from_file_location("_phase177_172_harness", _HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _prepare_step8_scenario(tmp_path: Path) -> tuple[dict, WorkflowProgressionDecision]:
    """Real Phase 177 -> real Phase 172 give an exact step-8 prepare decision."""
    harness = _harness()
    root = tmp_path / "scenario"
    values = harness.canonical_running_setup(root, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[assignment]
    state_path: Path = values["state_path"]  # type: ignore[assignment]
    events_path: Path = values["events_path"]  # type: ignore[assignment]
    calls: list = []
    step7 = harness.run_phase177_success(values, calls)
    assert step7.step_id == "step-7" and len(calls) == 1
    decision = harness.phase172(step7, wf, state_path, events_path)
    assert type(decision) is WorkflowProgressionDecision
    assert decision.decision == "prepare_next_step"
    assert decision.next_step_index == 8
    events = harness.loaded_events(root)
    assert [e.step_id for e in events] == [f"step-{i}" for i in range(1, 8)]
    assert events[4].request_id is None and events[4].provider == "openai"
    assert events[5].request_id is None and events[5].provider == "openai"
    approval = harness.approval_for(decision)
    employee = harness.employee_for(decision)
    return {
        "decision": decision,
        "workflow": wf,
        "approval": approval,
        "employee": employee,
        "state_path": state_path,
        "events_path": events_path,
        "state_before": state_path.read_bytes(),
        "events_before": events_path.read_bytes(),
    }, decision


def _invoke_real_chain(scenario: dict) -> object:
    return real_phase145(
        scenario["decision"],
        scenario["workflow"],
        scenario["approval"],
        scenario["employee"],
        scenario["state_path"],
        scenario["events_path"],
        phase137_function=real_phase137,
    )


def test_real_a_accumulated_none_prepares_step8(tmp_path: Path) -> None:
    scenario, _decision = _prepare_step8_scenario(tmp_path)
    out = _invoke_real_chain(scenario)
    assert type(out) is PreparedWorkflowStep
    assert out.step_index == 8 and out.step_id == "step-8"
    assert out.workflow_id == "w"
    # durable inputs unchanged through the real chain
    assert scenario["state_path"].read_bytes() == scenario["state_before"]
    assert scenario["events_path"].read_bytes() == scenario["events_before"]


def test_real_b_stop_route_remains_strict(tmp_path: Path) -> None:
    # Same accumulated openai None provenance at positions 5 and 6, but the
    # decision is a stop route (workflow_complete at step 8).  The accumulated
    # relaxation is gated on the prepare route only (``not stop``); on the
    # stop route the aged openai None predecessor must still be rejected as
    # terminal_contract.
    harness = _harness()
    root = tmp_path / "stop"
    values = harness.terminal_setup(root, steps=8)
    wf = values["workflow"]  # type: ignore[assignment]
    state_path: Path = values["state_path"]  # type: ignore[assignment]
    events_path: Path = values["events_path"]  # type: ignore[assignment]
    # Inject aged None request_id + openai provider at positions 5 and 6
    # (event indices 4 and 5), leaving position 7 (immediate predecessor) and
    # the step-8 terminal event with non-empty request ids as the prepare
    # route requires.
    lines = events_path.read_text(encoding="utf-8").splitlines()
    import json as _json

    for idx in (4, 5):
        payload = _json.loads(lines[idx])
        payload["request_id"] = None
        payload["provider"] = "openai"
        lines[idx] = _json.dumps(payload, separators=(",", ":"))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    decision = WorkflowProgressionDecision(
        "workflow_complete", "w", "step-8", 8, "e8", None, None, None, "last_step_succeeded"
    )
    with pytest.raises(Phase145Error) as exc:
        real_phase145(
            decision,
            wf,
            None,
            None,
            state_path,
            events_path,
            phase137_function=real_phase137,
        )
    assert exc.value.detail.classification == "terminal_contract"


def test_real_c_below_threshold_position_rejected(tmp_path: Path) -> None:
    harness = _harness()
    root = tmp_path / "below"
    values = harness.canonical_running_setup(root, steps=8, current=7)
    wf = values["workflow"]  # type: ignore[assignment]
    state_path: Path = values["state_path"]  # type: ignore[assignment]
    events_path: Path = values["events_path"]  # type: ignore[assignment]
    # canonical running setup at current=7 gives immediate predecessor
    # (position 6) with None; force an additional aged None at position 4
    # (below threshold >= 5) to prove the below-threshold case is rejected.
    lines = events_path.read_text(encoding="utf-8").splitlines()
    payload = __import__("json").loads(lines[3])  # index 3 -> position 4
    payload["request_id"] = None
    payload["provider"] = "openai"
    lines[3] = __import__("json").dumps(payload, separators=(",", ":"))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    decision = harness.prepare_decision(wf, 7)
    approval = harness.approval_for(decision)
    employee = harness.employee_for(decision)
    with pytest.raises(Phase145Error) as exc:
        real_phase145(
            decision,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            phase137_function=real_phase137,
        )
    assert exc.value.detail.classification == "terminal_contract"


def test_real_d_non_openai_accumulated_none_rejected(tmp_path: Path) -> None:
    harness = _harness()
    root = tmp_path / "nonopenai"
    values = harness.canonical_running_setup(root, steps=8, current=7)
    wf = values["workflow"]  # type: ignore[assignment]
    state_path: Path = values["state_path"]  # type: ignore[assignment]
    events_path: Path = values["events_path"]  # type: ignore[assignment]
    # Force an aged None predecessor with a non-openai provider at position 5.
    lines = events_path.read_text(encoding="utf-8").splitlines()
    payload = __import__("json").loads(lines[4])  # index 4 -> position 5
    payload["request_id"] = None
    payload["provider"] = "anthropic"
    lines[4] = __import__("json").dumps(payload, separators=(",", ":"))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    decision = harness.prepare_decision(wf, 7)
    approval = harness.approval_for(decision)
    employee = harness.employee_for(decision)
    with pytest.raises(Phase145Error) as exc:
        real_phase145(
            decision,
            wf,
            approval,
            employee,
            state_path,
            events_path,
            phase137_function=real_phase137,
        )
    assert exc.value.detail.classification == "terminal_contract"
