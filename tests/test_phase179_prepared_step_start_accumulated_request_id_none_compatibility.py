"""Real Phase 179 -> prepared-step-start accumulated aged-None compatibility (Issue #390).

Merged Phase 179 composes public Phase 178 -> public Phase 145 and can produce an
exact ``PreparedWorkflowStep(step 8)`` whose straight durable step-7 snapshot
carries an **accumulated aged ``request_id=None``** predecessor (provider
``"openai"``, position 5) inherited from the already-approved Phase 155 / 178 /
179 path.  Phase 146 (prepared-step-start outer-chain) and Phase 138 (outer)
only allowed ``None`` for the **immediate** predecessor, so the same valid
``None`` became invalid once it aged behind a later successful step.

Issue #390 adds the same bounded accumulated-aged rule on the **prepared route
only** of Phase 146 and Phase 138:

``request_id is None AND position >= 5 AND provider == \"openai\"
AND persisted current_step_index >= 7``

These tests drive the REAL Phase 179 boundary (synthetic successful transport,
exactly once) to produce the canonical step-8 ``PreparedWorkflowStep``, then run
it through the real repaired Phase 146 -> real Phase 138 -> unchanged lower
chain (Phase 131 -> ... -> Phase 33) to an exact ``PreparedStepExecutionStart``
(step 8), leaving the post-Phase 179 committed bytes unchanged and never
persisting / executing step 8.  Strictness (positions 1-4, non-openai, empty,
wrong-type, lower persisted index, malformed order / completed-prefix /
linkage, stop routes, ready-only error identity, mutation compensation,
both-target protection, no retry) stays enforced.

Requirement-to-test mapping (Issue #390):
- 1 -> test_01_source_default_dependency_audit
- 2 -> test_02_canonical_provenance_real_phase179
- 3 -> test_03_full_repaired_real_chain_success
- 4 -> test_04_phase146_bounded_accumulated_rule
- 5 -> test_05_phase138_bounded_accumulated_rule
- 6 -> test_06_non_contiguous_accumulated_provenance
- 7 -> test_07_multiple_valid_accumulated_immediate_none_bounded
- 8 -> test_08_unchanged_stop_readonly_error_behavior
"""

# ruff: noqa: E501,E701,E702,F401,I001

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    WorkflowProgressionDecision,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as real_phase146,
)
from ai_office.engine import (
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as real_phase138,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase146Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as Phase138Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeReentryContinuationCompatibilityError as Phase131Error,
)
from ai_office.invocation import ModelInvocationRequest
from ai_office.providers.openai import OpenAIApiKey
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)

_HARNESS179 = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary.py"
)


def _harness179():
    spec = importlib.util.spec_from_file_location("_phase179_harness", _HARNESS179)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# --------------------------------------------------------------------------
# Real Phase 179 canonical provenance (synthetic transport, exactly once)
# --------------------------------------------------------------------------
def _canonical(tmp_path: Path, *, non_contiguous: bool = False) -> dict:
    """Drive the real Phase 179 public boundary to produce the exact
    ``PreparedWorkflowStep(step 8)`` plus the post-Phase 179 committed snapshot
    that carries the accumulated aged step-5 ``request_id=None`` / openai."""
    h = _harness179()
    root = tmp_path
    values = h.running_setup(root, steps=8, current=6)
    wf = values["workflow"]  # type: ignore[assignment]
    sp: Path = values["state_path"]  # type: ignore[assignment]
    ep: Path = values["events_path"]  # type: ignore[assignment]
    result = h.runtime_success(wf, 6, request_id_none=(not non_contiguous))
    decision = h.prepare_decision(wf, 6)
    approval = h.approval_for(decision)
    employee = h.employee_for(decision)
    next_approval = h.approval_for(h.prepare_decision(wf, 7))
    next_employee = h.employee_for(h.prepare_decision(wf, 7))
    api_key = OpenAIApiKey(value=SecretStr("synthetic"))
    execution_approval = h.execution_approval_for(values)
    calls: list = []
    out = h.phase179(
        result,
        wf,
        approval,
        employee,
        sp,
        ep,
        h.TOOLS,
        api_key,
        execution_approval,
        h.success_transport(calls),
        next_approval,
        next_employee,
    )
    assert type(out) is PreparedWorkflowStep
    assert out.step_id == "step-8" and out.step_index == 8
    assert len(calls) == 1
    emp8 = h.employee_for(h.prepare_decision(wf, 7))
    return {
        "workflow": wf,
        "prepared": out,
        "emp8": emp8,
        "employee": employee,
        "state_path": sp,
        "events_path": ep,
        "committed": (sp.read_bytes(), ep.read_bytes()),
        "calls": calls,
        "harness": h,
    }


# --------------------------------------------------------------------------
# Handcrafted prepared-route snapshots for strictness controls
# --------------------------------------------------------------------------
def _workflow(steps: int) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "w",
            "name": "W",
            "description": "D",
            "steps": [
                {
                    "id": f"step-{i}",
                    "name": f"Step {i}",
                    "employee": f"e{i}",
                    "instructions": f"step-{i}",
                }
                for i in range(1, steps + 1)
            ],
        }
    )


def _employee(index: int) -> EmployeeDefinition:
    step = _workflow(20).steps[index - 1]
    return EmployeeDefinition.model_validate(
        {
            "id": step.employee,
            "name": "Next Employee",
            "role": "role",
            "instructions": "employee instructions",
            "model": "model-name",
            "allowed_tools": ["tool-one", "tool-two"],
        }
    )


def _prepared(index: int) -> PreparedWorkflowStep:
    wf = _workflow(20)
    step = wf.steps[index - 1]
    person = _employee(index)
    return PreparedWorkflowStep(
        wf.id,
        step.id,
        index,
        person.id,
        person.instructions,
        step.instructions,
        person.model,
        tuple(person.allowed_tools),
    )


def _start_for(value: PreparedWorkflowStep) -> PreparedStepExecutionStart:
    wf = _workflow(20)
    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            value.model,
            value.employee_instructions,
            value.step_instructions,
            value.allowed_tool_names,
        ),
        WorkflowExecutionState(
            value.workflow_id,
            "running",
            value.step_id,
            value.step_index,
            value.employee_id,
            tuple(step.id for step in wf.steps[: value.step_index - 1]),
            None,
        ),
    )


def _predecessor_event(step_id: str, index: int, **changes: object) -> RuntimeStepEvent:
    return replace(
        RuntimeStepEvent(
            "step_succeeded",
            "w",
            step_id,
            index,
            f"e{index}",
            "running",
            "succeeded",
            "openai",
            None,
            f"response-{step_id}",
            f"request-{step_id}",
            f"output-{step_id}",
            None,
        ),
        **changes,  # type: ignore[arg-type]
    )


def _write_prepared_scenario(
    tmp_path: Path,
    *,
    prepared_index: int = 8,
    current: int = 7,
    none_positions: tuple = (),
    non_openai_positions: tuple = (),
    empty_positions: tuple = (),
    wrong_type_positions: tuple = (),
    swap: bool = False,
) -> dict:
    """Prepared-route durable snapshot for strictness controls.  ``current`` is
    the persisted current step index; the prepared value is at ``prepared_index``."""
    wf = _workflow(20)
    current_step = wf.steps[current - 1]
    predecessors = []
    for position in range(1, current):
        changes: dict = {}
        if position in none_positions:
            changes["request_id"] = None
            changes["provider"] = "openai"
        if position in non_openai_positions:
            changes["provider"] = "anthropic"
        if position in empty_positions:
            changes["request_id"] = ""
        if position in wrong_type_positions:
            changes["request_id"] = 12345  # type: ignore[dict-item]
        predecessors.append(
            _predecessor_event(wf.steps[position - 1].id, position, **changes)
        )
    terminal = _predecessor_event(
        current_step.id, current, request_id="request", output_text="output"
    )
    if swap and len(predecessors) >= 2:
        predecessors[-1], predecessors[-2] = predecessors[-2], predecessors[-1]
    state = WorkflowExecutionState(
        "w",
        "succeeded",
        current_step.id,
        current,
        current_step.employee,
        tuple(step.id for step in wf.steps[:current]),
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
    return {
        "workflow": wf,
        "prepared": _prepared(prepared_index),
        "employee": _employee(prepared_index),
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_bytes,
        "before_events": event_bytes,
    }


def _recording_stub(return_value: object):
    calls: list = []

    def stub(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return return_value

    return stub, calls


def _assert_prepare_accepts(route, scenario: dict, dep_kwarg: str = "phase138_function") -> None:
    expected = _start_for(scenario["prepared"])
    stub, calls = _recording_stub(expected)
    out = route(
        scenario["prepared"],
        scenario["workflow"],
        scenario["employee"],
        scenario["state_path"],
        scenario["events_path"],
        **{dep_kwarg: stub},
    )
    assert out is expected
    assert len(calls) == 1
    assert scenario["state_path"].read_bytes() == scenario["before_state"]
    assert scenario["events_path"].read_bytes() == scenario["before_events"]


# --- 1. source / default dependency audit ---------------------------------
def test_01_source_default_dependency_audit() -> None:
    import inspect

    # Phase 146 default dependency is the real Phase 138 public boundary.
    sig146 = inspect.signature(real_phase146)
    kw146 = {k: p.default for k, p in sig146.parameters.items() if p.kind is p.KEYWORD_ONLY}
    assert set(kw146) == {"phase138_function"}
    assert kw146["phase138_function"] is real_phase138

    # Phase 138 default dependency is the real Phase 131 public boundary.
    sig138 = inspect.signature(real_phase138)
    kw138 = {k: p.default for k, p in sig138.parameters.items() if p.kind is p.KEYWORD_ONLY}
    assert set(kw138) == {"phase131_function"}
    import ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary as fr

    assert kw138["phase131_function"] is (
        fr.route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary
    )
    assert Phase146Error is not None
    assert Phase138Error is not None

    # Layering / no-bypass: Phase 146 delegates to public Phase 138 exactly once
    # with five positional args and no kwargs; phase131 is not named in Phase146.
    src146 = Path(
        "src/ai_office/engine/"
        "prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "phase138_function(result, workflow, employee, state_path, events_path)" in src146
    assert "phase131" not in src146.lower()
    assert "_check_predecessor" in src146

    # Phase 138 delegates to public Phase 131 exactly once; it never reaches
    # Phase 146 or re-validates 138-ward states.
    src138 = Path(
        "src/ai_office/engine/"
        "prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "phase131_function(result, workflow, employee, state_path, events_path)" in src138
    assert "phase146" not in src138.lower()

    # Phase 131 / lower production is untouched (no accumulated rule backport).
    src131 = Path(
        "src/ai_office/engine/"
        "prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary.py"
    ).read_text(encoding="utf-8")
    assert "allow_accumulated_openai_none" not in src131


# --- 2. canonical provenance: real Phase 179 ------------------------------
def test_02_canonical_provenance_real_phase179(tmp_path: Path) -> None:
    c = _canonical(tmp_path, non_contiguous=False)
    h = c["harness"]
    # Real Phase 179 returned the exact prepared step 8 with the durable
    # step-7 success snapshot that ages the step-5 openai None.
    events = h.loaded_events(tmp_path)
    assert [e.step_id for e in events] == [f"step-{i}" for i in range(1, 8)]
    assert events[4].request_id is None and events[4].provider == "openai"
    assert len(events) == 7
    # synthetic transport executed step 7 exactly once
    assert len(c["calls"]) == 1
    # committed snapshot captured for post-Phase179 byte identity
    assert c["state_path"].read_bytes() == c["committed"][0]
    assert c["events_path"].read_bytes() == c["committed"][1]


# --- 3. full repaired real chain success -----------------------------------
def test_03_full_repaired_real_chain_success(tmp_path: Path) -> None:
    c = _canonical(tmp_path, non_contiguous=False)
    h = c["harness"]
    # Real repaired Phase 146 (default -> real Phase 138 -> unchanged lower
    # chain Phase 131 -> ... -> Phase 33) returns exact PreparedStepExecutionStart.
    out = real_phase146(
        c["prepared"],
        c["workflow"],
        c["emp8"],
        c["state_path"],
        c["events_path"],
    )
    assert type(out) is PreparedStepExecutionStart
    assert out.running_state.workflow_id == "w"
    assert out.running_state.current_step_id == "step-8"
    assert out.running_state.current_step_index == 8
    # bytes remain post-Phase179 committed bytes
    assert c["state_path"].read_bytes() == c["committed"][0]
    assert c["events_path"].read_bytes() == c["committed"][1]
    # step 8 is not persisted and not executed
    events = h.loaded_events(tmp_path)
    assert len(events) == 7
    assert len(c["calls"]) == 1  # step-7 synthetic execution happened exactly once


# --- 4. Phase 146 bounded accumulated rule ---------------------------------
def test_04_phase146_bounded_accumulated_rule(tmp_path: Path) -> None:
    # (a) position-5+ accumulated openai None accepted at current index 7.
    accept = _write_prepared_scenario(
        tmp_path / "accept", current=7, none_positions=(5,)
    )
    _assert_prepare_accepts(real_phase146, accept, dep_kwarg="phase138_function")

    # (b) positions 1-4 None rejected (terminal_contract).
    below = _write_prepared_scenario(
        tmp_path / "below", current=7, none_positions=(4,)
    )
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            below["prepared"], below["workflow"], below["employee"],
            below["state_path"], below["events_path"], phase138_function=real_phase138,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (c) non-openai accumulated None rejected.
    non_openai = _write_prepared_scenario(
        tmp_path / "nonopenai", current=7, none_positions=(5,),
        non_openai_positions=(5,),
    )
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            non_openai["prepared"], non_openai["workflow"], non_openai["employee"],
            non_openai["state_path"], non_openai["events_path"],
            phase138_function=real_phase138,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (d) empty-string request ID rejected (not None, not non-empty).
    empty = _write_prepared_scenario(
        tmp_path / "empty", current=7, empty_positions=(5,)
    )
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            empty["prepared"], empty["workflow"], empty["employee"],
            empty["state_path"], empty["events_path"], phase138_function=real_phase138,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (e) wrong-type request ID rejected.
    wrong = _write_prepared_scenario(
        tmp_path / "wrong", current=7, wrong_type_positions=(5,)
    )
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            wrong["prepared"], wrong["workflow"], wrong["employee"],
            wrong["state_path"], wrong["events_path"], phase138_function=real_phase138,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (f) lower persisted current index (< 7) does not gain the accumulated rule:
    # an aged position-4 None at current index 6 stays rejected.
    early = _write_prepared_scenario(
        tmp_path / "early", prepared_index=7, current=6, none_positions=(4,)
    )
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            early["prepared"], early["workflow"], early["employee"],
            early["state_path"], early["events_path"], phase138_function=real_phase138,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (g) existing immediate predecessor None compatibility preserved: an
    # immediate request_id=None at position 5 (current index 6, index >= 6)
    # is accepted as before.
    immediate = _write_prepared_scenario(
        tmp_path / "immediate", prepared_index=7, current=6, none_positions=(5,)
    )
    _assert_prepare_accepts(real_phase146, immediate, dep_kwarg="phase138_function")


# --- 5. Phase 138 bounded accumulated rule ---------------------------------
def test_05_phase138_bounded_accumulated_rule(tmp_path: Path) -> None:
    # (a) accumulated openai None accepted on the Phase 138 prepare route.
    accept = _write_prepared_scenario(
        tmp_path / "accept", current=7, none_positions=(5,)
    )
    _assert_prepare_accepts(real_phase138, accept, dep_kwarg="phase131_function")

    # (b) positions 1-4 None rejected.
    below = _write_prepared_scenario(tmp_path / "below", current=7, none_positions=(4,))
    with pytest.raises(Phase138Error) as caught:
        real_phase138(
            below["prepared"], below["workflow"], below["employee"],
            below["state_path"], below["events_path"],
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (c) non-openai accumulated None rejected.
    non_openai = _write_prepared_scenario(
        tmp_path / "nonopenai", current=7, none_positions=(5,),
        non_openai_positions=(5,),
    )
    with pytest.raises(Phase138Error) as caught:
        real_phase138(
            non_openai["prepared"], non_openai["workflow"], non_openai["employee"],
            non_openai["state_path"], non_openai["events_path"],
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (d) empty-string request ID rejected.
    empty = _write_prepared_scenario(tmp_path / "empty", current=7, empty_positions=(5,))
    with pytest.raises(Phase138Error) as caught:
        real_phase138(
            empty["prepared"], empty["workflow"], empty["employee"],
            empty["state_path"], empty["events_path"],
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (e) wrong-type request ID rejected.
    wrong = _write_prepared_scenario(tmp_path / "wrong", current=7, wrong_type_positions=(5,))
    with pytest.raises(Phase138Error) as caught:
        real_phase138(
            wrong["prepared"], wrong["workflow"], wrong["employee"],
            wrong["state_path"], wrong["events_path"],
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (f) lower persisted current index (< 7) does not gain the accumulated rule.
    early = _write_prepared_scenario(
        tmp_path / "early", prepared_index=7, current=6, none_positions=(4,)
    )
    with pytest.raises(Phase138Error) as caught:
        real_phase138(
            early["prepared"], early["workflow"], early["employee"],
            early["state_path"], early["events_path"],
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (g) existing immediate predecessor None compatibility preserved.
    immediate = _write_prepared_scenario(
        tmp_path / "immediate", prepared_index=7, current=6, none_positions=(5,)
    )
    _assert_prepare_accepts(real_phase138, immediate, dep_kwarg="phase131_function")


# --- 6. non-contiguous accumulated provenance through full chain -----------
def test_06_non_contiguous_accumulated_provenance(tmp_path: Path) -> None:
    c = _canonical(tmp_path, non_contiguous=True)
    h = c["harness"]
    # step5 request_id=None/openai; step6 non-empty request id.
    events = h.loaded_events(tmp_path)
    assert events[4].request_id is None and events[4].provider == "openai"
    assert events[5].request_id is not None
    out = real_phase146(
        c["prepared"], c["workflow"], c["emp8"],
        c["state_path"], c["events_path"],
    )
    assert type(out) is PreparedStepExecutionStart
    assert out.running_state.current_step_index == 8
    assert c["state_path"].read_bytes() == c["committed"][0]
    assert c["events_path"].read_bytes() == c["committed"][1]
    assert len(c["calls"]) == 1


# --- 7. multiple valid accumulated + immediate None stays bounded ----------
def test_07_multiple_valid_accumulated_immediate_none_bounded(tmp_path: Path) -> None:
    # (a) canonical step-5 aged None + step-6 immediate None both succeed
    # through the full repaired default chain.
    c = _canonical(tmp_path, non_contiguous=False)
    out = real_phase146(
        c["prepared"], c["workflow"], c["emp8"],
        c["state_path"], c["events_path"],
    )
    assert type(out) is PreparedStepExecutionStart
    assert out.running_state.current_step_index == 8
    assert c["state_path"].read_bytes() == c["committed"][0]
    assert c["events_path"].read_bytes() == c["committed"][1]

    # (b) earlier position-4 None remains rejected below the boundary.
    below = _write_prepared_scenario(tmp_path / "below", current=7, none_positions=(4,))
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            below["prepared"], below["workflow"], below["employee"],
            below["state_path"], below["events_path"], phase138_function=real_phase138,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (c) non-openai accumulated None remains rejected.
    non_openai = _write_prepared_scenario(
        tmp_path / "nonopenai", current=7, none_positions=(5,),
        non_openai_positions=(5,),
    )
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            non_openai["prepared"], non_openai["workflow"], non_openai["employee"],
            non_openai["state_path"], non_openai["events_path"],
            phase138_function=real_phase138,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (d) malformed event order stays strict (terminal/linkage mismatch).
    swap = _write_prepared_scenario(tmp_path / "swap", current=7, swap=True)
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            swap["prepared"], swap["workflow"], swap["employee"],
            swap["state_path"], swap["events_path"], phase138_function=real_phase138,
        )
    assert caught.value.detail.classification == "terminal_contract"

    # (e) malformed completed-prefix stays strict: corrupt ONLY completed_step_ids
    # in the state target (aged accumulated None stays valid) -> terminal_contract.
    prefix = _write_prepared_scenario(
        tmp_path / "prefix", current=7, none_positions=(5,)
    )
    state_payload = json.loads(prefix["state_path"].read_text())
    state_payload["completed_step_ids"] = [
        sid for i, sid in enumerate(state_payload["completed_step_ids"]) if i != 1
    ]
    prefix["state_path"].write_text(
        json.dumps(state_payload, separators=(",", ":"))
    )
    before_state = prefix["state_path"].read_bytes()
    before_events = prefix["events_path"].read_bytes()
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            prefix["prepared"], prefix["workflow"], prefix["employee"],
            prefix["state_path"], prefix["events_path"],
            phase138_function=real_phase138,
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert prefix["state_path"].read_bytes() == before_state
    assert prefix["events_path"].read_bytes() == before_events

    # (f) malformed linkage stays strict: from the canonical real snapshot, corrupt
    # ONLY one linkage item (a predecessor event's step_id) -> terminal_contract.
    link = _canonical(tmp_path / "link", non_contiguous=False)
    link_lines = link["events_path"].read_text().splitlines()
    linkage_payload = json.loads(link_lines[0])  # step-1 predecessor
    linkage_payload["step_id"] = "step-999"  # break event<->workflow-step linkage
    link_lines[0] = json.dumps(linkage_payload, separators=(",", ":"))
    link["events_path"].write_text("\n".join(link_lines) + "\n")
    before_state = link["state_path"].read_bytes()
    before_events = link["events_path"].read_bytes()
    with pytest.raises(Phase146Error) as caught:
        real_phase146(
            link["prepared"], link["workflow"], link["emp8"],
            link["state_path"], link["events_path"],
        )
    assert caught.value.detail.classification == "terminal_contract"
    assert link["state_path"].read_bytes() == before_state
    assert link["events_path"].read_bytes() == before_events


# --- 8. unchanged stop / read-only / error behavior ------------------------
def test_08_unchanged_stop_readonly_error_behavior(tmp_path: Path) -> None:
    # =================================================================
    # Phase146 / Phase138 stop semantics are NOT broadened by Issue #390
    # =================================================================
    # (a) Phase146 canonical workflow_complete -> exact identity, targets unchanged.
    ok = tmp_path / "stopok"
    values = _write_terminal_stop(ok, steps=8, fail=False)
    sp, ep = values["state_path"], values["events_path"]
    before_state, before_events = values["before_state"], values["before_events"]
    stop = _completion(values["workflow"], 8)
    out = real_phase146(stop, values["workflow"], None, sp, ep,
                        phase138_function=lambda *a, **k: None)
    assert out is stop
    assert sp.read_bytes() == before_state
    assert ep.read_bytes() == before_events

    # (b) Phase146 canonical persisted_failure -> exact identity, targets unchanged.
    pf = tmp_path / "persisted"
    values = _write_terminal_stop(pf, steps=8, fail=True)
    sp, ep = values["state_path"], values["events_path"]
    before_state, before_events = values["before_state"], values["before_events"]
    failure = _failure(values["workflow"], 8)
    out = real_phase146(failure, values["workflow"], None, sp, ep,
                        phase138_function=lambda *a, **k: None)
    assert out is failure
    assert sp.read_bytes() == before_state
    assert ep.read_bytes() == before_events

    # (c) Phase146 aged-None workflow_complete still rejected (stop not broadened).
    d = tmp_path / "agedstop"
    values2 = _write_terminal_stop(d, steps=8, fail=False)
    sp2, ep2 = values2["state_path"], values2["events_path"]
    original_state2 = sp2.read_bytes()
    injected = _inject_aged_none(ep2)
    stop2 = _completion(values2["workflow"], 8)
    with pytest.raises(Phase146Error) as caught:
        real_phase146(stop2, values2["workflow"], None, sp2, ep2,
                      phase138_function=lambda *a, **k: None)
    assert caught.value.detail.classification == "terminal_contract"
    assert sp2.read_bytes() == original_state2
    assert ep2.read_bytes() == injected

    # (d) Phase146 aged-None persisted_failure still rejected (stop not broadened).
    d2 = tmp_path / "agedpf"
    values3 = _write_terminal_stop(d2, steps=8, fail=True)
    sp3, ep3 = values3["state_path"], values3["events_path"]
    original_state3 = sp3.read_bytes()
    injected3 = _inject_aged_none(ep3)
    failure3 = _failure(values3["workflow"], 8)
    with pytest.raises(Phase146Error) as caught:
        real_phase146(failure3, values3["workflow"], None, sp3, ep3,
                      phase138_function=lambda *a, **k: None)
    assert caught.value.detail.classification == "terminal_contract"
    assert sp3.read_bytes() == original_state3
    assert ep3.read_bytes() == injected3

    # (e) Phase138 aged-None workflow_complete still rejected (not broadened).
    p138 = tmp_path / "p138wc"
    values4 = _write_terminal_stop(p138, steps=8, fail=False)
    sp4, ep4 = values4["state_path"], values4["events_path"]
    original_state4 = sp4.read_bytes()
    injected4 = _inject_aged_none(ep4)
    stop4 = _completion(values4["workflow"], 8)
    with pytest.raises(Phase138Error) as caught:
        real_phase138(stop4, values4["workflow"], None, sp4, ep4,
                      phase131_function=lambda *a, **k: None)
    assert caught.value.detail.classification == "terminal_contract"
    assert sp4.read_bytes() == original_state4
    assert ep4.read_bytes() == injected4

    # (f) Phase138 aged-None persisted_failure still rejected (not broadened).
    p138b = tmp_path / "p138pf"
    values5 = _write_terminal_stop(p138b, steps=8, fail=True)
    sp5, ep5 = values5["state_path"], values5["events_path"]
    original_state5 = sp5.read_bytes()
    injected5 = _inject_aged_none(ep5)
    failure5 = _failure(values5["workflow"], 8)
    with pytest.raises(Phase138Error) as caught:
        real_phase138(failure5, values5["workflow"], None, sp5, ep5,
                      phase131_function=lambda *a, **k: None)
    assert caught.value.detail.classification == "terminal_contract"
    assert sp5.read_bytes() == original_state5
    assert ep5.read_bytes() == injected5

    # =================================================================
    # prepared-route safe dependency error: exact identity
    # =================================================================
    # (g) Phase146 re-raises the exact Phase138 safe error with identity and
    # leaves both targets unchanged.
    c2 = _canonical(tmp_path / "c2", non_contiguous=True)
    safe = Phase138Error("terminal_contract")

    def raising_dep(*args: object, **kwargs: object) -> object:
        raise safe

    with pytest.raises(Phase138Error) as caught:
        real_phase146(
            c2["prepared"], c2["workflow"], c2["emp8"],
            c2["state_path"], c2["events_path"],
            phase138_function=raising_dep,
        )
    assert caught.value is safe
    assert c2["state_path"].read_bytes() == c2["committed"][0]
    assert c2["events_path"].read_bytes() == c2["committed"][1]

    # (h) Phase138 re-raises the exact Phase131 safe error; dependency is called
    # exactly once (no retry) and both targets are unchanged.
    c3 = _canonical(tmp_path / "c3", non_contiguous=True)
    calls: list = []
    safe131 = Phase131Error("terminal_contract")

    def raising131(*args: object, **kwargs: object) -> object:
        calls.append(1)
        raise safe131

    with pytest.raises(Phase131Error) as caught:
        real_phase138(
            c3["prepared"], c3["workflow"], c3["emp8"],
            c3["state_path"], c3["events_path"],
            phase131_function=raising131,
        )
    assert caught.value is safe131
    assert len(calls) == 1  # exactly once, no retry
    assert c3["state_path"].read_bytes() == c3["committed"][0]
    assert c3["events_path"].read_bytes() == c3["committed"][1]

    # =================================================================
    # read-only / mutation compensation (prepared route, Phase146)
    # =================================================================
    # (i) state-target mutation is compensated back to original bytes (both
    # targets restored), and the call fails.
    c5 = _canonical(tmp_path / "c5", non_contiguous=True)
    orig_state5 = c5["state_path"].read_bytes()
    orig_events5 = c5["events_path"].read_bytes()

    def mut_state_dep(*args: object, **kwargs: object) -> object:
        c5["state_path"].write_bytes(b"corrupted-state")
        return "bogus"

    with pytest.raises(Phase146Error):
        real_phase146(
            c5["prepared"], c5["workflow"], c5["emp8"],
            c5["state_path"], c5["events_path"],
            phase138_function=mut_state_dep,
        )
    assert c5["state_path"].read_bytes() == orig_state5
    assert c5["events_path"].read_bytes() == orig_events5

    # (j) events-target mutation is compensated back to original bytes (both
    # targets restored), and the call fails.
    c4 = _canonical(tmp_path / "c4", non_contiguous=True)
    orig_state4 = c4["state_path"].read_bytes()
    orig_events4 = c4["events_path"].read_bytes()

    def mut_events_dep(*args: object, **kwargs: object) -> object:
        c4["events_path"].write_bytes(b"corrupted-events")
        return "bogus"

    with pytest.raises(Phase146Error):
        real_phase146(
            c4["prepared"], c4["workflow"], c4["emp8"],
            c4["state_path"], c4["events_path"],
            phase138_function=mut_events_dep,
        )
    assert c4["state_path"].read_bytes() == orig_state4
    assert c4["events_path"].read_bytes() == orig_events4

    # (k) both-target mutation is compensated back to original bytes (both
    # restored), and the call fails.
    c6 = _canonical(tmp_path / "c6", non_contiguous=True)
    orig_state6 = c6["state_path"].read_bytes()
    orig_events6 = c6["events_path"].read_bytes()

    def mut_both_dep(*args: object, **kwargs: object) -> object:
        c6["state_path"].write_bytes(b"corrupted")
        c6["events_path"].write_bytes(b"corrupted")
        return "bogus"

    with pytest.raises(Phase146Error):
        real_phase146(
            c6["prepared"], c6["workflow"], c6["emp8"],
            c6["state_path"], c6["events_path"],
            phase138_function=mut_both_dep,
        )
    assert c6["state_path"].read_bytes() == orig_state6
    assert c6["events_path"].read_bytes() == orig_events6


# --------------------------------------------------------------------------
def real_phase131_or_dummy():
    import ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary as fr

    return fr.route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary


def _write_terminal_stop(tmp_path: Path, *, steps: int, fail: bool) -> dict:
    wf = _workflow(steps)
    last = steps
    if fail:
        state = WorkflowExecutionState(
            "w", "failed", wf.steps[last - 1].id, last, wf.steps[last - 1].employee,
            tuple(step.id for step in wf.steps[: last - 1]), "api_error",
        )
    else:
        state = WorkflowExecutionState(
            "w", "succeeded", wf.steps[last - 1].id, last, wf.steps[last - 1].employee,
            tuple(step.id for step in wf.steps[:last]), None,
        )
    events = [_predecessor_event(wf.steps[i].id, i + 1) for i in range(last - 1)]
    final = wf.steps[last - 1]
    if fail:
        events.append(
            RuntimeStepEvent(
                "step_failed", "w", final.id, last, final.employee, "running",
                "failed", "openai", "api_error", None, None, None, "safe failure",
            )
        )
    else:
        events.append(
            RuntimeStepEvent(
                "step_succeeded", "w", final.id, last, final.employee, "running",
                "succeeded", "openai", None, f"response-{final.id}",
                f"request-{final.id}", f"output-{final.id}", None,
            )
        )
    tmp_path.mkdir(parents=True, exist_ok=True)
    state_path, events_path = tmp_path / "state.json", tmp_path / "events.jsonl"
    state_path.write_bytes(serialize_workflow_execution_state_json(state).encode("utf-8"))
    events_path.write_bytes(
        b"".join(serialize_runtime_step_event_jsonl(e).encode("utf-8") for e in events)
    )
    return {
        "workflow": wf,
        "state_path": state_path,
        "events_path": events_path,
        "before_state": state_path.read_bytes(),
        "before_events": events_path.read_bytes(),
    }


def _inject_aged_none(events_path: Path) -> bytes:
    import json

    lines = events_path.read_text(encoding="utf-8").splitlines()
    for idx in (4, 5):
        payload = json.loads(lines[idx])
        payload["request_id"] = None
        payload["provider"] = "openai"
        lines[idx] = json.dumps(payload, separators=(",", ":"))
    injected = ("\n".join(lines) + "\n").encode("utf-8")
    events_path.write_bytes(injected)
    return injected


def _completion(wf: WorkflowDefinition, index: int) -> WorkflowProgressionDecision:
    step = wf.steps[index - 1]
    return WorkflowProgressionDecision(
        "workflow_complete", "w", step.id, index, step.employee, None, None, None,
        "last_step_succeeded",
    )


def _failure(wf: WorkflowDefinition, index: int) -> PersistedExecutionOutcome:
    step = wf.steps[index - 1]
    return PersistedExecutionOutcome(
        "persisted_failure", "w", step.id, index, step.employee, "api_error"
    )
