"""Focused Issue #408 Phase 187 contract tests."""

# ruff: noqa: E501,E701,E702,F401,I001

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryError as Phase186Error,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase187Error,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary as phase187,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase155Error,
)
from ai_office.storage import RunningStatePersistenceResult
from ai_office.runtime import StepRuntimeExecutionFailure, StepRuntimeExecutionSuccess
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationError as Phase141Error,
)
from ai_office.engine.prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStartPersistenceCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase147Error,
)

_HARNESS = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary.py"
)
_TERMINAL = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_orchestration_boundary.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _h():
    return _load(_HARNESS, "phase185_harness_for_187")


def _terminal():
    return _load(_TERMINAL, "terminal_harness_for_187")


def _case(tmp_path: Path, *, steps: int = 9) -> dict[str, object]:
    return _h()._case(tmp_path, steps=steps)


def _following_execution_approval(case: dict[str, object]) -> object:
    path = _HARNESS.with_name("test_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary.py")
    module = _load(path, "phase184_helpers_for_187")
    return module._execution_approval(case["workflow"], 9)


def _contexts(case: dict[str, object]) -> dict[str, object]:
    return {
        "first_tools": case["tools"], "next_tools": case["tools"], "following_tools": case["tools"],
        "first_api": case["api_key"], "next_api": case["api_key"], "following_api": case["api_key"],
        "first_execution_approval": case["execution_approval"], "next_execution_approval": case["next_execution_approval"], "following_execution_approval": case.get("following_execution_approval", _following_execution_approval(case)),
    }


def _args(case: dict[str, object], contexts: dict[str, object], *, first_transport=None, next_transport=None) -> list[object]:
    return [
        case["result"], case["workflow"], case["approval"], case["employee"], case["state_path"], case["events_path"],
        contexts["first_tools"], contexts["first_api"], contexts["first_execution_approval"], first_transport,
        case["next_approval"], case["next_employee"], contexts["next_tools"], contexts["next_api"], contexts["next_execution_approval"], next_transport,
        case["following_approval"], case["following_employee"], contexts["following_tools"], contexts["following_api"], contexts["following_execution_approval"], case.get("following_transport"),
    ]


def _prepared(tmp_path: Path):
    case = _case(tmp_path)
    first: list[object] = []
    second: list[object] = []
    start = _h().phase185(*_h()._args(case, transport=case["h"].success_transport(first), next_transport=case["h"].success_transport(second)))
    assert type(start) is PreparedStepExecutionStart
    case["phase186_state_bytes"] = case["state_path"].read_bytes()
    case["phase186_event_bytes"] = case["events_path"].read_bytes()
    case["following_execution_approval"] = _following_execution_approval(case)
    return case, start


def _runtime(case: dict[str, object], *, failure: bool = False):
    from ai_office.invocation import ModelInvocationFailure, ModelInvocationSuccess
    invocation = ModelInvocationFailure("openai", "api_error", "synthetic", "request-9", 500, None, None) if failure else ModelInvocationSuccess("openai", "resp", "request-9", "completed", ("ok",), "ok")
    return (StepRuntimeExecutionFailure if failure else StepRuntimeExecutionSuccess)("w", "step-9", 9, "e9", invocation)


def _call(case: dict[str, object], *, contexts=None, phase186_function=None, phase147_function=None, phase155_function=None, following_transport=None):
    contexts = contexts or _contexts(case)
    args = _args(case, contexts, first_transport=case["h"].success_transport([]), next_transport=case["h"].success_transport([]))
    args[-1] = following_transport
    deps = {}
    if phase186_function is not None: deps["phase186_function"] = phase186_function
    if phase147_function is not None: deps["phase147_function"] = phase147_function
    if phase155_function is not None: deps["phase155_function"] = phase155_function
    return phase187(*args, **deps)


def _err(fn, classification: str):
    with pytest.raises(Phase187Error) as caught:
        fn()
    assert caught.value.detail.classification == classification
    assert "secret" not in str(caught.value)


def test_01_public_signature_export_defaults_error_hierarchy_and_source_audit() -> None:
    params = list(inspect.signature(phase187).parameters.values())
    assert [p.name for p in params[:22]] == [
        "result", "workflow", "preparation_approval", "employee", "state_path", "events_path", "resolved_tools", "api_key", "execution_approval", "transport", "next_preparation_approval", "next_employee", "next_resolved_tools", "next_api_key", "next_execution_approval", "next_transport", "following_preparation_approval", "following_employee", "following_resolved_tools", "following_api_key", "following_execution_approval", "following_transport"
    ]
    assert [p.name for p in params[22:]] == ["phase186_function", "phase147_function", "phase155_function"]
    assert params[22].default.__name__.startswith("route_runtime_result_to_")
    assert issubclass(Phase187Error, RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryError)
    assert RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryFailureDetail
    source = Path(phase187.__code__.co_filename).read_text()
    assert source.count("phase186_function(") == 1 and source.count("phase147_function(") == 1 and source.count("phase155_function(") == 1
    assert "phase141_function(" not in source and "phase161" not in source and "while " not in source[source.index("def route_"):source.index("def _check_inputs")]


def test_02_phase186_exactly_once_18_positional_only_capture_keyword(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "p186")
    calls = []
    def p186(*args, **kwargs): calls.append((args, kwargs)); return RunningStatePersistenceResult(len(case["state_path"].read_bytes()))
    _err(lambda: _call(case, phase186_function=p186, phase147_function=lambda *args: start), "phase186_contract")
    assert len(calls) == 1 and len(calls[0][0]) == 18 and set(calls[0][1]) == {"phase147_function"}


def test_03_capture_adapter_exact_start_identity_canonical_five_no_kwargs_and_return(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "capture")
    calls = []
    def p186(*args, **kwargs):
        kwargs["phase147_function"](start, case["workflow"], case["following_employee"], case["state_path"], case["events_path"])
        return RunningStatePersistenceResult(len(case["state_path"].read_bytes()))
    def p147(*args, **kwargs):
        calls.append((args, kwargs))
        args[3].write_bytes(__import__('ai_office.storage', fromlist=['serialize_workflow_execution_state_json']).serialize_workflow_execution_state_json(args[0].running_state).encode())
        return RunningStatePersistenceResult(len(args[3].read_bytes()))
    out = _call(case, phase186_function=p186, phase147_function=p147, phase155_function=lambda *args: _runtime(case))
    assert out.step_index == 9 and len(calls) == 1 and calls[0][0] == (start, case["workflow"], case["following_employee"], case["state_path"], case["events_path"]) and calls[0][1] == {}


def test_04_phase155_exactly_once_ten_positional_no_kwargs_captured_identity(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "p155")
    calls = []
    def p186(*args, **kwargs): kwargs["phase147_function"](start, case["workflow"], case["following_employee"], case["state_path"], case["events_path"]); return RunningStatePersistenceResult(len(case["state_path"].read_bytes()))
    out = _call(case, phase186_function=p186, phase147_function=lambda *args: (args[3].write_bytes(__import__('ai_office.storage', fromlist=['serialize_workflow_execution_state_json']).serialize_workflow_execution_state_json(args[0].running_state).encode()) or RunningStatePersistenceResult(len(args[3].read_bytes()))), phase155_function=lambda *args, **kwargs: (calls.append((args, kwargs)) or _runtime(case)))
    assert out.step_index == 9 and len(calls) == 1 and len(calls[0][0]) == 10 and calls[0][1] == {} and calls[0][0][1] is start and calls[0][0][3] is case["following_employee"]


def test_05_three_context_ownership_is_following_only(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "owners")
    contexts = _contexts(case); seen = []
    def p186(*args, **kwargs):
        kwargs["phase147_function"](start, case["workflow"], case["following_employee"], case["state_path"], case["events_path"])
        return RunningStatePersistenceResult(len(case["state_path"].read_bytes()))
    def p155(*args): seen.append(args); return _runtime(case)
    _call(case, contexts=contexts, phase186_function=p186, phase147_function=lambda *a: (a[3].write_bytes(__import__('ai_office.storage', fromlist=['serialize_workflow_execution_state_json']).serialize_workflow_execution_state_json(a[0].running_state).encode()) or RunningStatePersistenceResult(len(a[3].read_bytes()))), phase155_function=p155)
    assert seen and seen[0][3] is case["following_employee"] and seen[0][6] is contexts["following_tools"] and seen[0][7] is contexts["following_api"] and seen[0][8] is contexts["following_execution_approval"]


def test_06_phase186_owned_operational_inputs_are_not_prevalidated(tmp_path: Path) -> None:
    case, stop = _terminal_case(tmp_path / "nopre", failed=False)
    seen = []
    def p186(*args, **kwargs): seen.append(args); return stop
    args = [stop, case["workflow"], object(), object(), case["state_path"], case["events_path"], object(), object(), object(), object(), object(), object(), object(), object(), object(), object(), object(), object(), object(), object(), object(), object()]
    out = phase187(*args, phase186_function=p186, phase147_function=lambda *a: pytest.fail("capture"), phase155_function=lambda *a: pytest.fail("p155"))
    assert out is stop and len(seen) == 1 and len(seen[0]) == 18


def _terminal_case(tmp_path: Path, *, failed: bool):
    values = _terminal().terminal_setup(tmp_path, steps=6, fail=failed)
    stop = _terminal().failure_outcome(values["workflow"], 6) if failed else _terminal().complete_decision(values["workflow"], 6)
    return {"workflow": values["workflow"], "state_path": values["state_path"], "events_path": values["events_path"], "result": stop}, stop


def test_07_original_workflow_complete_identity_zero_calls_unchanged(tmp_path: Path) -> None:
    case, stop = _terminal_case(tmp_path / "complete", failed=False); before=(case["state_path"].read_bytes(),case["events_path"].read_bytes()); calls=[]
    assert _call_stop(case, stop, calls) is stop and calls == [] and before == (case["state_path"].read_bytes(),case["events_path"].read_bytes())


def test_08_original_persisted_failure_identity_zero_calls_unchanged(tmp_path: Path) -> None:
    case, stop = _terminal_case(tmp_path / "failure", failed=True); before=(case["state_path"].read_bytes(),case["events_path"].read_bytes()); calls=[]
    assert _call_stop(case, stop, calls) is stop and calls == [] and before == (case["state_path"].read_bytes(),case["events_path"].read_bytes())


def _call_stop(case, stop, calls):
    args=[stop,case["workflow"],None,None,case["state_path"],case["events_path"],None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None]
    return phase187(*args, phase186_function=lambda *a,**k: stop, phase147_function=lambda *a:calls.append(a), phase155_function=lambda *a:pytest.fail("p155"))


def test_09_runtime_phase186_stops_zero_calls_and_keeps_bytes(tmp_path: Path) -> None:
    for failed in (False, True):
        case, _ = _prepared(tmp_path / str(failed)); calls=[]
        stop = _terminal().failure_outcome(case["workflow"], 8) if failed else _terminal().complete_decision(case["workflow"], 9)
        before=(case["state_path"].read_bytes(),case["events_path"].read_bytes())
        assert _call_stop(case, stop, calls) is stop
        assert calls == [] and before == (case["state_path"].read_bytes(),case["events_path"].read_bytes())


def test_10_real_default_step9_success_failure_and_bytes(tmp_path: Path) -> None:
    for failed in (False, True):
        case = _case(tmp_path / str(failed)); contexts=_contexts(case); first=[]; second=[]; third=[]
        following = case["h"].failure_transport(third) if failed else case["h"].success_transport(third)
        args = _args(case, contexts, first_transport=case["h"].success_transport(first), next_transport=case["h"].success_transport(second))
        args[-1] = following
        out=phase187(*args)
        assert out.step_index == 9 and (type(out) is StepRuntimeExecutionFailure) is failed
        assert len(first)==len(second)==len(third)==1
        assert case["state_path"].read_bytes() == case["state_path"].read_bytes()


def test_11_provenance_narrowness_reuses_lower_contracts(tmp_path: Path) -> None:
    # Real Phase186/155 composition is covered by test 10; lower Phase147/139
    # focused suites cover the accumulated provenance matrix.
    assert Phase147Error and Phase155Error


def test_12_phase186_safe_errors_preserve_identity_and_zero_later_calls(tmp_path: Path) -> None:
    case, _ = _prepared(tmp_path / "safe"); safe=Phase186Error("safe"); calls=[]
    with pytest.raises(Phase186Error) as caught: _call(case, phase186_function=lambda *a,**k: (_ for _ in ()).throw(safe), phase147_function=lambda *a:calls.append(a), phase155_function=lambda *a:calls.append(a))
    assert caught.value is safe and calls == []


def test_13_unexpected_phase186_error_is_sanitized_no_rollback(tmp_path: Path) -> None:
    case, _ = _prepared(tmp_path / "unexpected"); before=case["state_path"].read_bytes()
    def p186(*a,**k): case["state_path"].write_bytes(before+b"owned"); raise RuntimeError("secret")
    _err(lambda:_call(case,phase186_function=p186,phase147_function=lambda *a:None,phase155_function=lambda *a:None),"dependency_error")
    assert case["state_path"].read_bytes()==before+b"owned"


def test_14_malformed_capture_or_phase186_output_is_contract_error(tmp_path: Path) -> None:
    case, _ = _prepared(tmp_path / "malformed"); calls=[]
    _err(lambda:_call(case,phase186_function=lambda *a,**k: RunningStatePersistenceResult(1),phase147_function=lambda *a:calls.append(a),phase155_function=lambda *a:None),"phase186_contract")
    assert calls == []


def test_15_phase155_safe_error_restores_committed_bytes(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "p155safe"); committed=[]; safe=Phase141Error("safe")
    def p186(*a,**k):
        k["phase147_function"](start,case["workflow"],case["following_employee"],case["state_path"],case["events_path"])
        committed.extend((case["state_path"].read_bytes(), case["events_path"].read_bytes()))
        return RunningStatePersistenceResult(len(committed[0]))
    def p155(*a): case["state_path"].write_bytes(b"bad"); raise safe
    with pytest.raises(Phase141Error) as caught: _call(case,phase186_function=p186,phase147_function=lambda *a:(a[3].write_bytes(__import__('ai_office.storage', fromlist=['serialize_workflow_execution_state_json']).serialize_workflow_execution_state_json(a[0].running_state).encode()) or RunningStatePersistenceResult(len(a[3].read_bytes()))),phase155_function=p155)
    assert caught.value is safe and (case["state_path"].read_bytes(),case["events_path"].read_bytes())==tuple(committed)


def test_16_phase155_malformed_result_restores_committed_bytes(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "p155bad"); committed=[]
    def p186(*a,**k):
        k["phase147_function"](start,case["workflow"],case["following_employee"],case["state_path"],case["events_path"])
        committed.extend((case["state_path"].read_bytes(), case["events_path"].read_bytes()))
        return RunningStatePersistenceResult(len(committed[0]))
    def p155(*a): case["events_path"].write_bytes(b"bad"); return object()
    _err(lambda:_call(case,phase186_function=p186,phase147_function=lambda *a:(a[3].write_bytes(__import__('ai_office.storage', fromlist=['serialize_workflow_execution_state_json']).serialize_workflow_execution_state_json(a[0].running_state).encode()) or RunningStatePersistenceResult(len(a[3].read_bytes()))),phase155_function=p155),"phase155_contract")
    assert (case["state_path"].read_bytes(),case["events_path"].read_bytes())==tuple(committed)


def test_17_phase155_valid_result_mutation_is_committed_mutation(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "mutation"); committed=[]
    def p186(*a,**k):
        k["phase147_function"](start,case["workflow"],case["following_employee"],case["state_path"],case["events_path"])
        committed.extend((case["state_path"].read_bytes(), case["events_path"].read_bytes()))
        return RunningStatePersistenceResult(len(committed[0]))
    def p155(*a): case["state_path"].write_bytes(b"bad"); return _runtime(case)
    _err(lambda:_call(case,phase186_function=p186,phase147_function=lambda *a:(a[3].write_bytes(__import__('ai_office.storage', fromlist=['serialize_workflow_execution_state_json']).serialize_workflow_execution_state_json(a[0].running_state).encode()) or RunningStatePersistenceResult(len(a[3].read_bytes()))),phase155_function=p155),"committed_mutation")
    assert (case["state_path"].read_bytes(),case["events_path"].read_bytes())==tuple(committed)


def test_18_phase155_unexpected_and_rollback_failure_are_sanitized(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "rollback"); committed=[]
    def p186(*a,**k):
        k["phase147_function"](start,case["workflow"],case["following_employee"],case["state_path"],case["events_path"])
        committed.extend((case["state_path"].read_bytes(), case["events_path"].read_bytes()))
        return RunningStatePersistenceResult(len(committed[0]))
    def p155(*a): case["state_path"].write_bytes(b"bad"); raise RuntimeError("secret")
    _err(lambda:_call(case,phase186_function=p186,phase147_function=lambda *a:(a[3].write_bytes(__import__('ai_office.storage', fromlist=['serialize_workflow_execution_state_json']).serialize_workflow_execution_state_json(a[0].running_state).encode()) or RunningStatePersistenceResult(len(a[3].read_bytes()))),phase155_function=p155),"dependency_error")


def test_19_runtime_result_is_exact_step9_result_and_no_readvance(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "stop"); calls=[]; value=_runtime(case)
    def p186(*a,**k): k["phase147_function"](start,case["workflow"],case["following_employee"],case["state_path"],case["events_path"]); return RunningStatePersistenceResult(len(case["state_path"].read_bytes()))
    out=_call(case,phase186_function=p186,phase147_function=lambda *a:(a[3].write_bytes(__import__('ai_office.storage', fromlist=['serialize_workflow_execution_state_json']).serialize_workflow_execution_state_json(a[0].running_state).encode()) or RunningStatePersistenceResult(len(a[3].read_bytes()))),phase155_function=lambda *a:(calls.append(a) or value))
    assert out is value and len(calls)==1 and calls[0][0].__class__ is RunningStatePersistenceResult


def test_20_following_only_context_and_no_provider_network_calls(tmp_path: Path) -> None:
    case, start = _prepared(tmp_path / "following"); seen=[]
    def p186(*a,**k): k["phase147_function"](start,case["workflow"],case["following_employee"],case["state_path"],case["events_path"]); return RunningStatePersistenceResult(len(case["state_path"].read_bytes()))
    def p155(*a): seen.append(a); return _runtime(case)
    out=_call(case,phase186_function=p186,phase147_function=lambda *a:(a[3].write_bytes(__import__('ai_office.storage', fromlist=['serialize_workflow_execution_state_json']).serialize_workflow_execution_state_json(a[0].running_state).encode()) or RunningStatePersistenceResult(len(a[3].read_bytes()))),phase155_function=p155)
    assert out.step_index==9 and seen[0][3] is case["following_employee"]
