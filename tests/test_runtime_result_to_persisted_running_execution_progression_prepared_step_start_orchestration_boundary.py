"""Focused tests for Issue #392 Phase 180."""

# ruff: noqa: E501,I001

import importlib.util
import inspect
from pathlib import Path

import pytest

from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase180Error,
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary,
    route_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary,
    route_runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase146CompatibilityError,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryCompatibilityError as Phase179CompatibilityError,
)

phase180 = route_runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary
phase179 = route_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary

_HARNESS_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary.py"
)


def _harness():
    spec = importlib.util.spec_from_file_location("phase179_test_harness", _HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case(tmp_path: Path, *, steps: int = 8, current: int = 6, request_none: bool = True):
    h = _harness()
    values = h.running_setup(tmp_path, steps=steps, current=current)
    wf = values["workflow"]
    first_decision = h.prepare_decision(wf, current)
    first_approval = h.approval_for(first_decision)
    first_employee = h.employee_for(first_decision)
    if current + 1 < len(wf.steps):
        next_decision = h.prepare_decision(wf, current + 1)
        next_approval = h.approval_for(next_decision)
        next_employee = h.employee_for(next_decision)
    else:
        next_approval = None
        next_employee = None
    return {
        "h": h,
        "values": values,
        "workflow": wf,
        "result": h.runtime_success(wf, current, request_id_none=request_none),
        "approval": first_approval,
        "employee": first_employee,
        "next_approval": next_approval,
        "next_employee": next_employee,
        "state_path": values["state_path"],
        "events_path": values["events_path"],
        "tools": h.TOOLS,
        "api_key": __import__("ai_office.providers.openai", fromlist=["OpenAIApiKey"]).OpenAIApiKey(value="synthetic"),
        "execution_approval": h.execution_approval_for(values),
    }


def _call(s: dict, *, phase179_function=phase179, phase146_function=route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary, **overrides):
    args = [
        overrides.get("result", s["result"]),
        overrides.get("workflow", s["workflow"]),
        overrides.get("preparation_approval", s["approval"]),
        overrides.get("employee", s["employee"]),
        overrides.get("state_path", s["state_path"]),
        overrides.get("events_path", s["events_path"]),
        overrides.get("resolved_tools", s["tools"]),
        overrides.get("api_key", s["api_key"]),
        overrides.get("execution_approval", s["execution_approval"]),
        overrides.get("transport", None),
        overrides.get("next_preparation_approval", s["next_approval"]),
        overrides.get("next_employee", s["next_employee"]),
    ]
    return phase180(*args, phase179_function=phase179_function, phase146_function=phase146_function)


def _real_phase179(s: dict, *, transport=None):
    h = s["h"]
    calls = []
    out = phase179(
        s["result"], s["workflow"], s["approval"], s["employee"],
        s["state_path"], s["events_path"], s["tools"], s["api_key"],
        s["execution_approval"], transport or h.success_transport(calls),
        s["next_approval"], s["next_employee"],
    )
    return out, calls


def _start(prepared: PreparedWorkflowStep, workflow) -> PreparedStepExecutionStart:
    from ai_office.invocation import ModelInvocationRequest, UpstreamStepOutput
    from ai_office.runtime import WorkflowExecutionState

    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            prepared.model,
            prepared.employee_instructions,
            prepared.step_instructions,
            prepared.allowed_tool_names,
            (
                UpstreamStepOutput(
                    workflow.id,
                    workflow.steps[prepared.step_index - 2].id,
                    prepared.step_index - 1,
                    workflow.steps[prepared.step_index - 2].employee,
                    "ok",
                ),
            ),
        ),
        WorkflowExecutionState(
            prepared.workflow_id,
            "running",
            prepared.step_id,
            prepared.step_index,
            prepared.employee_id,
            tuple(step.id for step in workflow.steps[: prepared.step_index - 1]),
            None,
        ),
    )


def _classification(fn, expected: str):
    with pytest.raises(Phase180Error) as exc:
        fn()
    assert exc.value.detail.classification == expected
    assert "secret" not in str(exc.value)


def test_01_public_api_error_hierarchy_defaults_signature_and_source_audit(tmp_path: Path, monkeypatch):
    sig = inspect.signature(phase180)
    params = list(sig.parameters.values())
    assert [p.name for p in params if p.kind is p.POSITIONAL_OR_KEYWORD and p.default is inspect.Parameter.empty] == [
        "result", "workflow", "preparation_approval", "employee", "state_path", "events_path",
        "resolved_tools", "api_key", "execution_approval", "transport", "next_preparation_approval", "next_employee",
    ]
    assert [p.name for p in params if p.kind is p.KEYWORD_ONLY] == ["phase179_function", "phase146_function"]
    assert params[-2].default is phase179
    assert params[-1].default is route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary
    assert issubclass(Phase180Error, RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError)
    assert RuntimeResultToPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryFailureDetail
    source = Path(phase180.__code__.co_filename).read_text()
    assert source.count("phase179_function(") == 1
    assert source.count("phase146_function(") == 1
    assert "while " not in source and "route_prepared_start_persistence" not in source
    # ---- input-domain inline matrix (kept within this single collected test) ----
    s = _case(tmp_path / "inputs")
    p146 = []
    def no_p146(*a, **k):
        p146.append(1)
        return None
    # StepRuntimeExecutionSuccess subclass reject (exact-type check)
    class Sub(type(s["result"])):
        pass
    sub = Sub(s["result"].workflow_id, s["result"].step_id, s["result"].step_index,
              s["result"].employee_id, s["result"].invocation_result)
    _classification(lambda: _call(s, result=sub, phase146_function=no_p146), "result_type")
    # substitute / unrelated result reject
    _classification(lambda: _call(s, result=object(), phase146_function=no_p146), "result_type")
    _classification(lambda: _call(s, result=WorkflowProgressionDecision("prepare_next_step", "w", "step-6", 6, "e6", "step-7", 7, "e7", "next_step_available"), phase146_function=no_p146), "result_type")
    # invalid workflow
    _classification(lambda: _call(s, workflow=object(), phase146_function=no_p146), "workflow_definition")
    # invalid state/event target types
    _classification(lambda: _call(s, state_path="not-a-path", phase146_function=no_p146), "state_target")
    _classification(lambda: _call(s, events_path=12345, phase146_function=no_p146), "event_target")
    # same state and events target
    _classification(lambda: _call(s, events_path=s["state_path"], phase146_function=no_p146), "target_conflict")
    # non-callable dependencies -> configuration
    _classification(lambda: _call(s, phase179_function=None, phase146_function=no_p146), "configuration")
    _classification(lambda: _call(s, phase146_function=object()), "configuration")
    # state is_file OSError -> state_target (post-type-check, real Path)
    real_is_file = Path.is_file
    def bad_state_is_file(self):
        if self == s["state_path"]:
            raise OSError("boom state")
        return real_is_file(self)
    monkeypatch.setattr(Path, "is_file", bad_state_is_file)
    _classification(lambda: _call(s, phase146_function=no_p146), "state_target")
    monkeypatch.setattr(Path, "is_file", real_is_file)
    # events is_file OSError -> event_target
    def bad_events_is_file(self):
        if self == s["events_path"]:
            raise OSError("boom events")
        return real_is_file(self)
    monkeypatch.setattr(Path, "is_file", bad_events_is_file)
    _classification(lambda: _call(s, phase146_function=no_p146), "event_target")
    monkeypatch.setattr(Path, "is_file", real_is_file)
    # state read_bytes OSError -> dependency_error (during pre-Phase179 capture)
    real_read_bytes = Path.read_bytes
    def bad_state_read(self):
        if self == s["state_path"]:
            raise OSError("secret state read")
        return real_read_bytes(self)
    monkeypatch.setattr(Path, "read_bytes", bad_state_read)
    _classification(lambda: _call(s, phase146_function=no_p146), "dependency_error")
    monkeypatch.setattr(Path, "read_bytes", real_read_bytes)
    # events read_bytes OSError -> dependency_error
    def bad_events_read(self):
        if self == s["events_path"]:
            raise OSError("secret events read")
        return real_read_bytes(self)
    monkeypatch.setattr(Path, "read_bytes", bad_events_read)
    _classification(lambda: _call(s, phase146_function=no_p146), "dependency_error")
    monkeypatch.setattr(Path, "read_bytes", real_read_bytes)
    # Phase 146 zero-call on every input-domain reject above
    assert p146 == []


def test_02_phase179_exactly_once_twelve_positional_no_kwargs(tmp_path: Path):
    s = _case(tmp_path / "calls")
    prepared, _ = _real_phase179(s)
    assert type(prepared) is PreparedWorkflowStep
    calls = []
    def p179(*args, **kwargs):
        assert not kwargs
        calls.append(args)
        return prepared
    _call(s, phase179_function=p179, phase146_function=lambda *a: _start(prepared, s["workflow"]))
    assert len(calls) == 1 and len(calls[0]) == 12
    assert calls[0][0] is s["result"] and calls[0][1] is s["workflow"]
    assert calls[0][2] is s["approval"] and calls[0][3] is s["employee"]
    assert calls[0][10] is s["next_approval"] and calls[0][11] is s["next_employee"]


def test_03_phase146_exactly_once_five_positional_and_no_kwargs(tmp_path: Path):
    s = _case(tmp_path / "calls")
    prepared, _ = _real_phase179(s)
    calls = []
    def p146(*args, **kwargs):
        assert not kwargs
        calls.append(args)
        return _start(prepared, s["workflow"])
    out = _call(s, phase179_function=lambda *a: prepared, phase146_function=p146)
    assert type(out) is PreparedStepExecutionStart
    assert len(calls) == 1 and len(calls[0]) == 5
    assert calls[0] == (prepared, s["workflow"], s["next_employee"], s["state_path"], s["events_path"])


def test_04_next_employee_ownership_and_no_first_employee_reuse(tmp_path: Path):
    s = _case(tmp_path / "employee")
    prepared, _ = _real_phase179(s)
    seen = []
    def p146(*args):
        seen.append(args[2])
        return _start(prepared, s["workflow"])
    _call(s, phase179_function=lambda *a: prepared, phase146_function=p146)
    assert seen == [s["next_employee"]] and seen[0] is not s["employee"]
    _classification(lambda: _call(s, phase179_function=lambda *a: prepared, phase146_function=p146, next_employee=s["employee"]), "phase179_contract")


def test_05_later_operational_approval_employee_inputs_not_prevalidated(tmp_path: Path):
    h = _harness()
    values = h.terminal_setup(tmp_path / "no-prevalidation", steps=6)
    wf = values["workflow"]
    stop = h.complete_decision(wf, 6)
    calls = []
    def p179(*args):
        calls.append(args)
        return stop
    out = _call({"result": stop, "workflow": wf, "approval": None, "employee": None, "state_path": values["state_path"], "events_path": values["events_path"], "tools": None, "api_key": None, "execution_approval": None, "next_approval": None, "next_employee": None}, phase179_function=p179, phase146_function=lambda *a: pytest.fail("Phase146 called"), result=stop)
    assert out is stop and len(calls) == 1 and len(calls[0]) == 12


def test_06_original_workflow_complete_stop_identity_unchanged_phase146_zero_call(tmp_path: Path):
    h = _harness()
    v = h.terminal_setup(tmp_path / "complete", steps=6)
    stop = h.complete_decision(v["workflow"], 6)
    before = (v["state_path"].read_bytes(), v["events_path"].read_bytes())
    out = _call({"result": stop, "workflow": v["workflow"], "approval": None, "employee": None, "state_path": v["state_path"], "events_path": v["events_path"], "tools": None, "api_key": None, "execution_approval": None, "next_approval": None, "next_employee": None}, phase179_function=lambda *a: stop, phase146_function=lambda *a: pytest.fail("called"), result=stop)
    assert out is stop and (v["state_path"].read_bytes(), v["events_path"].read_bytes()) == before


def test_07_original_persisted_failure_stop_identity_unchanged_phase146_zero_call(tmp_path: Path):
    h = _harness()
    v = h.terminal_setup(tmp_path / "failure", steps=6, fail=True)
    stop = h.failure_outcome(v["workflow"], 6)
    before = (v["state_path"].read_bytes(), v["events_path"].read_bytes())
    out = _call({"result": stop, "workflow": v["workflow"], "approval": None, "employee": None, "state_path": v["state_path"], "events_path": v["events_path"], "tools": None, "api_key": None, "execution_approval": None, "next_approval": None, "next_employee": None}, phase179_function=lambda *a: stop, phase146_function=lambda *a: pytest.fail("called"), result=stop)
    assert out is stop and (v["state_path"].read_bytes(), v["events_path"].read_bytes()) == before


def test_08_runtime_to_workflow_complete_stop_retains_phase179_bytes_and_zero_phase146(tmp_path: Path):
    s = _case(tmp_path / "runtime-complete", steps=7, current=6)
    out179, calls = _real_phase179(s)
    assert type(out179) is WorkflowProgressionDecision and out179.decision == "workflow_complete"
    committed = (s["state_path"].read_bytes(), s["events_path"].read_bytes())
    phase146_calls = []
    out = _call(s, phase179_function=lambda *a: out179, phase146_function=lambda *a: phase146_calls.append(1), result=s["result"])
    assert out is out179 and phase146_calls == [] and (s["state_path"].read_bytes(), s["events_path"].read_bytes()) == committed


def test_09_runtime_to_persisted_failure_stop_retains_phase179_bytes_and_zero_phase146(tmp_path: Path):
    s = _case(tmp_path / "runtime-failure")
    out179, _ = _real_phase179(s, transport=s["h"].failure_transport([]))
    assert type(out179) is PersistedExecutionOutcome and out179.outcome == "persisted_failure"
    committed = (s["state_path"].read_bytes(), s["events_path"].read_bytes())
    calls = []
    out = _call(s, phase179_function=lambda *a: out179, phase146_function=lambda *a: calls.append(1), result=s["result"])
    assert out is out179 and calls == [] and (s["state_path"].read_bytes(), s["events_path"].read_bytes()) == committed
    # inline subcase: exact same stop but with a mismatched failure_category on the
    # surfaced persisted_failure -> phase179_contract, Phase146 zero-call, and the
    # Phase-179-owned durable bytes are NOT rolled back.
    mismatched = PersistedExecutionOutcome(
        "persisted_failure",
        out179.workflow_id,
        out179.current_step_id,
        out179.current_step_index,
        out179.current_employee_id,
        "mismatched-category",  # differs from durable terminal last_failure_category
    )
    calls2 = []
    _classification(
        lambda: _call(s, phase179_function=lambda *a: mismatched, phase146_function=lambda *a: calls2.append(1), result=s["result"]),
        "phase179_contract",
    )
    assert calls2 == []
    assert (s["state_path"].read_bytes(), s["events_path"].read_bytes()) == committed


def test_10_real_default_accumulated_none_phase179_to_phase146_step8_start(tmp_path: Path):
    s = _case(tmp_path / "real")
    calls = []
    out = _call(s, transport=s["h"].success_transport(calls))
    assert type(out) is PreparedStepExecutionStart and out.running_state.current_step_id == "step-8"
    assert out.running_state.status == "running"
    assert len(calls) == 1  # step-7 synthetic transport exactly once
    assert s["state_path"].read_text().find('"current_step_index":7') >= 0
    assert '"current_step_index":8' not in s["state_path"].read_text()


def test_11_real_default_non_contiguous_accumulated_provenance_step8_start(tmp_path: Path):
    s = _case(tmp_path / "non-contiguous", request_none=False)
    calls = []
    out = _call(s, transport=s["h"].success_transport(calls))
    assert type(out) is PreparedStepExecutionStart and out.running_state.current_step_id == "step-8"
    assert out.running_state.completed_step_ids == tuple(f"step-{i}" for i in range(1, 8))
    assert len(calls) == 1


def test_12_recognized_phase179_safe_error_identity_no_rollback(tmp_path: Path):
    s = _case(tmp_path / "safe179")
    sentinel = Phase179CompatibilityError("dependency_error")
    before = s["state_path"].read_bytes()
    def p179(*args):
        s["state_path"].write_bytes(before + b"mutated")
        raise sentinel
    with pytest.raises(Phase179CompatibilityError) as exc:
        _call(s, phase179_function=p179, phase146_function=lambda *a: pytest.fail("called"))
    assert exc.value is sentinel and s["state_path"].read_bytes() == before + b"mutated"


def test_13_unexpected_phase179_error_sanitized_no_rollback(tmp_path: Path):
    s = _case(tmp_path / "unexpected179")
    before = s["events_path"].read_bytes()
    def p179(*args):
        s["events_path"].write_bytes(before + b"secret")
        raise RuntimeError("secret dependency detail")
    _classification(lambda: _call(s, phase179_function=p179, phase146_function=lambda *a: pytest.fail("called")), "dependency_error")
    assert s["events_path"].read_bytes() == before + b"secret"


def test_14_malformed_phase179_output_contract_no_phase146_no_rollback(tmp_path: Path):
    s = _case(tmp_path / "malformed179")
    before = s["state_path"].read_bytes()
    def p179(*args):
        s["state_path"].write_bytes(before + b"changed")
        return object()
    _classification(lambda: _call(s, phase179_function=p179, phase146_function=lambda *a: pytest.fail("called")), "phase179_contract")
    assert s["state_path"].read_bytes() == before + b"changed"


def test_15_phase179_prepared_mismatch_workflow_employee_snapshot_matrix(tmp_path: Path):
    s = _case(tmp_path / "mismatch")
    prepared, _ = _real_phase179(s)
    wrong_step = PreparedWorkflowStep(prepared.workflow_id, "wrong", prepared.step_index, prepared.employee_id, prepared.employee_instructions, prepared.step_instructions, prepared.model, prepared.allowed_tool_names)
    for value, employee in ((wrong_step, s["next_employee"]), (prepared, s["employee"])):
        calls = []
        _classification(lambda value=value, employee=employee: _call(s, phase179_function=lambda *a: value, phase146_function=lambda *a: calls.append(1), next_employee=employee), "phase179_contract")
        assert calls == []
    s["state_path"].write_bytes(s["state_path"].read_bytes() + b"mismatch")
    _classification(lambda: _call(s, phase179_function=lambda *a: prepared, phase146_function=lambda *a: pytest.fail("called")), "phase179_contract")


def test_16_malformed_phase146_return_restores_post_phase179_snapshot(tmp_path: Path):
    s = _case(tmp_path / "malformed146")
    prepared, _ = _real_phase179(s)
    committed = (s["state_path"].read_bytes(), s["events_path"].read_bytes())
    def p146(*args):
        s["state_path"].write_bytes(b"bad-state")
        s["events_path"].write_bytes(b"bad-events")
        return object()
    _classification(lambda: _call(s, phase179_function=lambda *a: prepared, phase146_function=p146), "phase146_contract")
    assert (s["state_path"].read_bytes(), s["events_path"].read_bytes()) == committed


def test_17_phase146_safe_error_identity_compensates_state_event_both(tmp_path: Path):
    for mode in ("state", "events", "both"):
        s = _case(tmp_path / mode)
        prepared, _ = _real_phase179(s)
        committed = (s["state_path"].read_bytes(), s["events_path"].read_bytes())
        sentinel = Phase146CompatibilityError("start_contract")
        def p146(*args, mode=mode):
            if mode in ("state", "both"):
                s["state_path"].write_bytes(b"state-mutated")
            if mode in ("events", "both"):
                s["events_path"].write_bytes(b"events-mutated")
            raise sentinel
        with pytest.raises(Phase146CompatibilityError) as exc:
            _call(s, phase179_function=lambda *a: prepared, phase146_function=p146)
        assert exc.value is sentinel
        assert (s["state_path"].read_bytes(), s["events_path"].read_bytes()) == committed


def test_18_phase146_valid_return_target_mutation_compensates_and_classifies(tmp_path: Path):
    for mode in ("state", "events", "both"):
        s = _case(tmp_path / mode)
        prepared, _ = _real_phase179(s)
        committed = (s["state_path"].read_bytes(), s["events_path"].read_bytes())
        def p146(*args, mode=mode):
            if mode in ("state", "both"):
                s["state_path"].write_bytes(b"state-mutated")
            if mode in ("events", "both"):
                s["events_path"].write_bytes(b"events-mutated")
            return _start(prepared, s["workflow"])
        _classification(lambda: _call(s, phase179_function=lambda *a: prepared, phase146_function=p146), "committed_mutation")
        assert (s["state_path"].read_bytes(), s["events_path"].read_bytes()) == committed


def test_19_unexpected_phase146_error_sanitization_and_rollback_failure_matrix(tmp_path: Path, monkeypatch):
    s = _case(tmp_path / "normal")
    prepared, _ = _real_phase179(s)
    committed = (s["state_path"].read_bytes(), s["events_path"].read_bytes())
    def unexpected(*args):
        s["state_path"].write_text("bad", encoding="utf-8")
        s["events_path"].write_text("bad", encoding="utf-8")
        raise RuntimeError("secret detail")
    _classification(lambda: _call(s, phase179_function=lambda *a: prepared, phase146_function=unexpected), "dependency_error")
    assert (s["state_path"].read_bytes(), s["events_path"].read_bytes()) == committed
    s2 = _case(tmp_path / "rollback-failure")
    prepared2, _ = _real_phase179(s2)
    attempted = []
    def fail_write(path, contents):
        attempted.append(path)
        raise OSError("cannot restore")
    monkeypatch.setattr(Path, "write_bytes", fail_write)
    def unexpected2(*args):
        s2["state_path"].write_text("bad", encoding="utf-8")
        s2["events_path"].write_text("bad", encoding="utf-8")
        raise RuntimeError("secret detail")
    _classification(lambda: _call(s2, phase179_function=lambda *a: prepared2, phase146_function=unexpected2), "rollback_failure")
    assert len(attempted) == 2


def test_20_no_readvance_persistence_execution_retry_or_loop_after_exact_start(tmp_path: Path):
    s = _case(tmp_path / "no-readvance")
    p179_calls = []
    p146_calls = []
    transport_calls = []
    def p179(*args):
        p179_calls.append(args)
        return phase179(*args)
    def p146(*args):
        p146_calls.append(args)
        return route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary(*args)
    out = _call(s, phase179_function=p179, phase146_function=p146, transport=s["h"].success_transport(transport_calls))
    assert type(out) is PreparedStepExecutionStart and len(p179_calls) == len(p146_calls) == 1
    assert len(transport_calls) == 1
    assert out.running_state.current_step_id == "step-8"
    assert '"current_step_index":7' in s["state_path"].read_text()
    assert '"current_step_index":8' not in s["state_path"].read_text()
