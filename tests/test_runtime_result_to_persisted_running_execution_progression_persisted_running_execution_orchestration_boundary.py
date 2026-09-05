"""Focused and real-default regressions for Issue #398 Phase 182."""

# ruff: noqa: E501,E701,E702,F401,I001

import importlib.util
import inspect
from pathlib import Path

import pytest

from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryCompatibilityError as Phase182Error,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase155,
    route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase147,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_orchestration_boundary as phase182,
    route_runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary as phase181,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterChainReentryContinuationCompatibilityError as Phase155Error,
)
from ai_office.engine.persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PersistedRunningExecutionCycleHandoffChainBridgeOuterReentryContinuationCompatibilityError as Phase141Error,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_prepared_start_persistence_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPreparedStartPersistenceOrchestrationBoundaryCompatibilityError as Phase181Error,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    UpstreamStepOutput,
    approve_model_invocation_execution,
)
from ai_office.runtime import StepRuntimeExecutionFailure, StepRuntimeExecutionSuccess
from ai_office.storage import RunningStatePersistenceResult

_HARNESS_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary.py"
)


def _harness():
    spec = importlib.util.spec_from_file_location("phase180_harness_for_182", _HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _phase179_harness():
    return _harness()._harness()


def _case(tmp_path: Path, *, steps: int = 8, current: int = 6) -> dict[str, object]:
    return _harness()._case(tmp_path, steps=steps, current=current)


def _runtime_success(case: dict[str, object], index: int = 8) -> StepRuntimeExecutionSuccess:
    return case["h"].runtime_success(case["workflow"], index, request_id_none=True)


def _second_context(case: dict[str, object]) -> dict[str, object]:
    employee = case["next_employee"]
    workflow = case["workflow"]
    step = workflow.steps[7]  # type: ignore[union-attr]
    request = ModelInvocationRequest(
        employee.model,
        employee.instructions,
        step.instructions,
        tuple(employee.allowed_tools),
        (
            UpstreamStepOutput(
                workflow.id,
                workflow.steps[6].id,
                7,
                workflow.steps[6].employee,
                "ok",
            ),
        ),
    )
    return {
        "tools": case["tools"],
        "api_key": case["api_key"],
        "approval": approve_model_invocation_execution(
            request,
            case["tools"],
            provider="openai",
            approved_by="step8-test",
            approval_id="step8-approval",
        ),
    }


def _args(case: dict[str, object], **overrides: object) -> list[object]:
    second = (
        _second_context(case)
        if case.get("next_employee") is not None
        else {"tools": None, "api_key": None, "approval": None}
    )
    names = (
        "result", "workflow", "preparation_approval", "employee", "state_path",
        "events_path", "resolved_tools", "api_key", "execution_approval",
        "transport", "next_preparation_approval", "next_employee",
        "next_resolved_tools", "next_api_key", "next_execution_approval",
        "next_transport",
    )
    values = (
        case["result"], case["workflow"], case["approval"], case["employee"],
        case["state_path"], case["events_path"], case["tools"], case["api_key"],
        case["execution_approval"], None, case["next_approval"], case["next_employee"],
        second["tools"], second["api_key"], second["approval"], None,
    )
    return [overrides.get(name, value) for name, value in zip(names, values, strict=True)]


def _call(case: dict[str, object], **kwargs: object) -> object:
    dependency_names = {"phase181_function", "phase147_function", "phase155_function"}
    dependencies = {name: kwargs.pop(name) for name in tuple(kwargs) if name in dependency_names}
    return phase182(*_args(case, **kwargs), **dependencies)


def _real_phase181(case: dict[str, object], *, failure: bool = False):
    captured: list[PreparedStepExecutionStart] = []
    calls: list[object] = []
    h = case["h"]

    def capture(start: object, workflow: object, employee: object, state: object, events: object) -> object:
        captured.append(start)  # type: ignore[arg-type]
        return phase147(start, workflow, employee, state, events)

    output = phase181(
        case["result"], case["workflow"], case["approval"], case["employee"],
        case["state_path"], case["events_path"], case["tools"], case["api_key"],
        case["execution_approval"],
        h.failure_transport(calls) if failure else h.success_transport(calls),
        case["next_approval"], case["next_employee"], phase147_function=capture,
    )
    assert type(output) is RunningStatePersistenceResult
    assert len(captured) == 1
    return output, captured[0], calls


def _phase181_stub(
    persisted: RunningStatePersistenceResult, start: PreparedStepExecutionStart
):
    def stub(*args: object, **kwargs: object) -> object:
        assert len(args) == 12
        kwargs["phase147_function"](
            start, args[1], args[11], args[4], args[5]
        )
        return persisted

    return stub


def _classification(fn, expected: str) -> None:
    with pytest.raises(Phase182Error) as caught:
        fn()
    assert caught.value.detail.classification == expected
    assert "secret" not in str(caught.value)


def test_01_public_signature_exports_defaults_and_source_audit() -> None:
    params = list(inspect.signature(phase182).parameters.values())
    assert [p.name for p in params[:16]] == [
        "result", "workflow", "preparation_approval", "employee", "state_path",
        "events_path", "resolved_tools", "api_key", "execution_approval", "transport",
        "next_preparation_approval", "next_employee", "next_resolved_tools", "next_api_key",
        "next_execution_approval", "next_transport",
    ]
    assert [p.name for p in params[16:]] == [
        "phase181_function", "phase147_function", "phase155_function"
    ]
    assert params[16].default is phase181
    assert params[17].default is phase147
    assert params[18].default is phase155
    assert issubclass(Phase182Error, RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryError)
    assert RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionOrchestrationBoundaryFailureDetail
    source = Path(phase182.__code__.co_filename).read_text()
    assert source.count("phase181_function(") == 1
    assert source.count("phase147_function(") == 1
    assert source.count("phase155_function(") == 1
    assert "phase141_function(" not in source and "phase133_function(" not in source
    assert "route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry" not in source


def test_02_phase181_exactly_once_canonical_twelve_plus_capture_keyword(tmp_path: Path) -> None:
    case = _case(tmp_path / "phase181")
    persisted, start, _ = _real_phase181(case)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        kwargs["phase147_function"](start, args[1], args[11], args[4], args[5])
        return persisted

    expected = _runtime_success(case)
    out = _call(
        case,
        phase181_function=dependency,
        phase147_function=lambda *args: persisted,
        phase155_function=lambda *args: expected,
    )
    assert out is expected
    assert len(calls) == 1 and len(calls[0][0]) == 12
    assert list(calls[0][1]) == ["phase147_function"]
    assert calls[0][0][:2] == (case["result"], case["workflow"])
    assert calls[0][0][2] is case["approval"] and calls[0][0][3] is case["employee"]
    assert calls[0][0][10] is case["next_approval"] and calls[0][0][11] is case["next_employee"]


def test_03_capture_adapter_exact_identity_five_args_and_unchanged_return(tmp_path: Path) -> None:
    case = _case(tmp_path / "adapter")
    persisted, start, _ = _real_phase181(case)
    phase147_calls: list[tuple[object, ...]] = []

    def p181(*args: object, **kwargs: object) -> object:
        kwargs["phase147_function"](start, args[1], args[11], args[4], args[5])
        return persisted

    def p147(*args: object, **kwargs: object) -> object:
        assert kwargs == {}
        phase147_calls.append(args)
        return persisted

    out = _call(case, phase181_function=p181, phase147_function=p147, phase155_function=lambda *args: _runtime_success(case))
    assert type(out) is StepRuntimeExecutionSuccess
    assert phase147_calls == [(start, case["workflow"], case["next_employee"], case["state_path"], case["events_path"])]


def test_04_phase155_exactly_once_ten_args_second_context_and_exact_result(tmp_path: Path) -> None:
    case = _case(tmp_path / "phase155")
    persisted, start, _ = _real_phase181(case)
    second = _second_context(case)
    marker = object()
    expected = _runtime_success(case)
    calls: list[tuple[object, ...]] = []

    def p155(*args: object, **kwargs: object) -> object:
        calls.append(args)
        assert kwargs == {}
        return expected

    out = _call(
        case,
        phase181_function=_phase181_stub(persisted, start),
        phase147_function=lambda *args: persisted,
        phase155_function=p155,
        next_resolved_tools=second["tools"],
        next_api_key=second["api_key"],
        next_execution_approval=second["approval"],
        next_transport=marker,
    )
    assert out is expected and len(calls) == 1 and len(calls[0]) == 10
    assert calls[0][0] is persisted and calls[0][1] is start
    assert calls[0][3] is case["next_employee"] and calls[0][6] is second["tools"]
    assert calls[0][9] is marker


def test_05_first_and_second_execution_contexts_are_not_interchangeable(tmp_path: Path) -> None:
    case = _case(tmp_path / "distinct")
    persisted, start, first_calls = _real_phase181(case)
    committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    second_calls: list[object] = []
    with pytest.raises(Phase155Error) as caught:
        _call(
            case,
            phase181_function=_phase181_stub(persisted, start),
            phase147_function=lambda *args: persisted,
            next_resolved_tools=case["tools"],
            next_api_key=case["api_key"],
            next_execution_approval=case["execution_approval"],
            next_transport=lambda value: second_calls.append(value),
        )
    assert caught.value.detail.classification == "approval_contract"
    assert second_calls == [] and len(first_calls) == 1
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_06_original_workflow_complete_identity_capture_zero_phase155_zero(tmp_path: Path) -> None:
    h = _phase179_harness()
    values = h.terminal_setup(tmp_path / "complete", steps=6)
    stop = h.complete_decision(values["workflow"], 6)
    before = (values["state_path"].read_bytes(), values["events_path"].read_bytes())
    calls: list[object] = []
    out = phase182(
        stop, values["workflow"], None, None, values["state_path"], values["events_path"],
        None, None, None, None, None, None, None, None, None, None,
        phase181_function=lambda *args, **kwargs: stop,
        phase155_function=lambda *args: calls.append(args),
    )
    assert out is stop and calls == []
    assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before


def test_07_original_persisted_failure_identity_capture_zero_phase155_zero(tmp_path: Path) -> None:
    h = _phase179_harness()
    values = h.terminal_setup(tmp_path / "failure", steps=6, fail=True)
    stop = h.failure_outcome(values["workflow"], 6)
    calls: list[object] = []
    out = phase182(
        stop, values["workflow"], None, None, values["state_path"], values["events_path"],
        None, None, None, None, None, None, None, None, None, None,
        phase181_function=lambda *args, **kwargs: stop,
        phase155_function=lambda *args: calls.append(args),
    )
    assert out is stop and calls == []


def test_08_runtime_to_workflow_complete_stop_phase155_zero(tmp_path: Path) -> None:
    case = _case(tmp_path / "runtime-complete", steps=7, current=6)
    calls: list[object] = []
    out = _call(
        case,
        transport=case["h"].success_transport([]),
        next_transport=case["h"].success_transport([]),
        phase155_function=lambda *args: calls.append(args),
    )
    assert type(out) is WorkflowProgressionDecision and out.decision == "workflow_complete"
    assert calls == []


def test_09_runtime_to_persisted_failure_stop_phase155_zero(tmp_path: Path) -> None:
    case = _case(tmp_path / "runtime-failure")
    calls: list[object] = []
    out = _call(
        case,
        transport=case["h"].failure_transport([]),
        next_transport=case["h"].success_transport([]),
        phase155_function=lambda *args: calls.append(args),
    )
    assert type(out) is PersistedExecutionOutcome and out.outcome == "persisted_failure"
    assert calls == []


def test_10_strict_initial_workflow_target_configuration_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path / "domain")
    h = _phase179_harness()
    persisted_success = PersistedExecutionOutcome("persisted_success", "w", "step-6", 6, "e6", None)
    class ResultSubclass(type(case["result"])):
        pass
    subclass = ResultSubclass(
        case["result"].workflow_id,
        case["result"].step_id,
        case["result"].step_index,
        case["result"].employee_id,
        case["result"].invocation_result,
    )
    for value, classification in (
        (object(), "result_type"),
        (subclass, "result_type"),
        (h.prepare_decision(case["workflow"], 6), "result_type"),
        (persisted_success, "result_type"),
    ):
        _classification(lambda value=value: _call(case, result=value), classification)
    _classification(lambda: _call(case, workflow=object()), "workflow_definition")
    _classification(lambda: _call(case, state_path="bad"), "state_target")
    _classification(lambda: _call(case, events_path=object()), "event_target")
    _classification(lambda: _call(case, events_path=case["state_path"]), "target_conflict")
    _classification(lambda: _call(case, phase181_function=None), "configuration")
    _classification(lambda: _call(case, phase147_function=object()), "configuration")
    _classification(lambda: _call(case, phase155_function=object()), "configuration")
    real_read_bytes = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: (_ for _ in ()).throw(OSError("state read"))
        if path == case["state_path"]
        else real_read_bytes(path),
    )
    _classification(lambda: _call(case), "dependency_error")
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda path: (_ for _ in ()).throw(OSError("events read"))
        if path == case["events_path"]
        else real_read_bytes(path),
    )
    _classification(lambda: _call(case), "dependency_error")


def test_11_all_operational_and_next_execution_inputs_not_prevalidated(tmp_path: Path) -> None:
    h = _phase179_harness()
    values = h.terminal_setup(tmp_path / "stop", steps=6)
    stop = h.complete_decision(values["workflow"], 6)
    calls: list[tuple[object, ...]] = []

    def p181(*args: object, **kwargs: object) -> object:
        calls.append(args)
        return stop

    out = phase182(
        stop, values["workflow"], None, None, values["state_path"], values["events_path"],
        None, None, None, None, None, None, None, None, None, None,
        phase181_function=p181,
        phase155_function=lambda *args: pytest.fail("Phase155 called"),
    )
    assert out is stop and len(calls) == 1 and len(calls[0]) == 12


def test_12_phase181_safe_unexpected_and_malformed_are_identity_sanitized_or_contract(tmp_path: Path) -> None:
    case = _case(tmp_path / "safe")
    safe = Phase181Error("dependency_error")
    with pytest.raises(Phase181Error) as caught:
        _call(case, phase181_function=lambda *args, **kwargs: (_ for _ in ()).throw(safe))
    assert caught.value is safe

    case = _case(tmp_path / "unexpected")
    before = case["state_path"].read_bytes()

    def unexpected(*args: object, **kwargs: object) -> object:
        case["state_path"].write_bytes(before + b"secret")
        raise RuntimeError("secret")

    _classification(lambda: _call(case, phase181_function=unexpected), "dependency_error")
    assert case["state_path"].read_bytes() == before + b"secret"
    case = _case(tmp_path / "malformed")
    _classification(lambda: _call(case, phase181_function=lambda *args, **kwargs: object()), "phase181_contract")

    case = _case(tmp_path / "capture-contract")
    persisted, start, _ = _real_phase181(case)
    phase155_calls: list[object] = []
    for capture in (
        lambda adapter, args: adapter(object(), args[1], args[11], args[4], args[5]),
        lambda adapter, args: adapter(start, object(), args[11], args[4], args[5]),
        lambda adapter, args: adapter(start, args[1], object(), args[4], args[5]),
        lambda adapter, args: adapter(start, args[1], args[11], object(), args[5]),
        lambda adapter, args: adapter(start, args[1], args[11], args[4], object()),
    ):
        current_adapter = [None]
        def p181_with_adapter(*args: object, **kwargs: object) -> object:
            current_adapter[0] = kwargs["phase147_function"]
            capture(current_adapter[0], args)
            return persisted
        _classification(
            lambda: _call(
                case,
                phase181_function=p181_with_adapter,
                phase147_function=lambda *args: persisted,
                phase155_function=lambda *args: phase155_calls.append(args),
            ),
            "phase181_contract",
        )
    def p181_twice(*args: object, **kwargs: object) -> object:
        adapter = kwargs["phase147_function"]
        adapter(start, args[1], args[11], args[4], args[5])
        adapter(start, args[1], args[11], args[4], args[5])
        return persisted
    _classification(
        lambda: _call(
            case,
            phase181_function=p181_twice,
            phase147_function=lambda *args: persisted,
            phase155_function=lambda *args: phase155_calls.append(args),
        ),
        "phase181_contract",
    )
    assert phase155_calls == []

    case = _case(tmp_path / "linkage")
    persisted, start, _ = _real_phase181(case)
    phase155_calls: list[object] = []
    for malformed in (
        StepRuntimeExecutionSuccess(
            case["result"].workflow_id, "wrong-step", case["result"].step_index,
            case["result"].employee_id, case["result"].invocation_result,
        ),
        StepRuntimeExecutionSuccess(
            case["result"].workflow_id, case["result"].step_id, case["result"].step_index,
            "wrong-employee", case["result"].invocation_result,
        ),
        StepRuntimeExecutionSuccess(
            case["result"].workflow_id, case["result"].step_id, 0,
            case["result"].employee_id, case["result"].invocation_result,
        ),
        StepRuntimeExecutionSuccess(
            case["result"].workflow_id, case["result"].step_id, -1,
            case["result"].employee_id, case["result"].invocation_result,
        ),
    ):
        _classification(
            lambda malformed=malformed: _call(
                case,
                result=malformed,
                phase181_function=_phase181_stub(persisted, start),
                phase147_function=lambda *args: persisted,
                phase155_function=lambda *args: phase155_calls.append(args),
            ),
            "phase181_contract",
        )
    assert phase155_calls == []


def test_13_phase155_safe_error_identity_restores_post_phase181_snapshot(tmp_path: Path) -> None:
    for error_type in (Phase155Error, Phase141Error):
        case = _case(tmp_path / error_type.__name__)
        persisted, start, _ = _real_phase181(case)
        committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        safe = error_type("runtime_contract")

        def p155(*args: object, safe=safe) -> object:
            case["state_path"].write_text("mutated", encoding="utf-8")
            case["events_path"].write_text("mutated", encoding="utf-8")
            raise safe

        with pytest.raises(type(safe)) as caught:
            _call(
                case,
                phase181_function=_phase181_stub(persisted, start),
                phase147_function=lambda *args: persisted,
                phase155_function=p155,
            )
        assert caught.value is safe
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_14_phase155_unexpected_and_malformed_are_compensated_and_classified(tmp_path: Path) -> None:
    for label, returned in (("unexpected", False), ("malformed", True)):
        case = _case(tmp_path / label)
        persisted, start, _ = _real_phase181(case)
        committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())

        def p155(*args: object, returned=returned) -> object:
            case["state_path"].write_text("bad", encoding="utf-8")
            if returned:
                return object()
            raise RuntimeError("secret")

        expected = "phase155_contract" if returned else "dependency_error"
        _classification(lambda: _call(case, phase181_function=_phase181_stub(persisted, start), phase147_function=lambda *args: persisted, phase155_function=p155), expected)
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed

    case = _case(tmp_path / "persistence-result-contract")
    persisted, start, _ = _real_phase181(case)
    for malformed in (True, 0, -1, "wrong"):
        _classification(
            lambda malformed=malformed: _call(
                case,
                phase181_function=lambda *args, malformed=malformed, **kwargs: (
                    kwargs["phase147_function"](
                        start, args[1], args[11], args[4], args[5]
                    ),
                    RunningStatePersistenceResult(malformed),
                )[1],
                phase147_function=lambda *args: persisted,
                phase155_function=lambda *args: pytest.fail("Phase155 called"),
            ),
            "phase181_contract",
        )


def test_15_valid_mutation_and_rollback_failure_attempt_both_targets_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(tmp_path / "mutation")
    persisted, start, _ = _real_phase181(case)
    committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    expected = _runtime_success(case)

    def p155(*args: object) -> object:
        case["state_path"].write_text("bad", encoding="utf-8")
        case["events_path"].write_text("bad", encoding="utf-8")
        return expected

    _classification(lambda: _call(case, phase181_function=_phase181_stub(persisted, start), phase147_function=lambda *args: persisted, phase155_function=p155), "committed_mutation")
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed

    case = _case(tmp_path / "rollback")
    persisted, start, _ = _real_phase181(case)
    attempts: list[Path] = []

    def fail_write(path: Path, contents: bytes) -> None:
        attempts.append(path)
        raise OSError("blocked")

    monkeypatch.setattr(Path, "write_bytes", fail_write)

    def p155b(*args: object) -> object:
        case["state_path"].write_text("bad", encoding="utf-8")
        case["events_path"].write_text("bad", encoding="utf-8")
        raise Phase155Error("runtime_contract")

    _classification(lambda: _call(case, phase181_function=_phase181_stub(persisted, start), phase147_function=lambda *args: persisted, phase155_function=p155b), "rollback_failure")
    assert attempts == [case["state_path"], case["events_path"]]


def test_16_exact_runtime_result_stops_without_result_persistence_or_second_execution(tmp_path: Path) -> None:
    case = _case(tmp_path / "stop-after")
    before_state = case["state_path"].read_bytes()
    before_events = case["events_path"].read_bytes()
    second_calls: list[object] = []
    out = _call(
        case,
        transport=case["h"].success_transport([]),
        next_transport=case["h"].success_transport(second_calls),
    )
    assert type(out) is StepRuntimeExecutionSuccess and out.step_index == 8
    assert len(second_calls) == 1
    assert case["state_path"].read_bytes() != before_state
    assert case["events_path"].read_bytes() != before_events
    assert case["events_path"].read_text().count("step_succeeded") == 7
    assert '"step_id":"step-8"' not in case["events_path"].read_text()


def test_17_real_a_phase182_step8_success_and_running_snapshot_preserved(tmp_path: Path) -> None:
    alone = _case(tmp_path / "alone")
    phase181_alone, _, first_alone = _real_phase181(alone)
    alone_bytes = (alone["state_path"].read_bytes(), alone["events_path"].read_bytes())
    assert len(first_alone) == 1 and phase181_alone.state_bytes_written > 0

    case = _case(tmp_path / "real-a")
    first_calls: list[object] = []
    second_calls: list[object] = []
    out = _call(case, transport=case["h"].success_transport(first_calls), next_transport=case["h"].success_transport(second_calls))
    assert type(out) is StepRuntimeExecutionSuccess
    assert (out.workflow_id, out.step_id, out.step_index, out.employee_id) == ("w", "step-8", 8, "e8")
    assert len(first_calls) == len(second_calls) == 1
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == alone_bytes
    assert '"step_id":"step-8"' not in case["events_path"].read_text()


def test_18_real_b_phase182_step8_failure_and_running_snapshot_preserved(tmp_path: Path) -> None:
    alone = _case(tmp_path / "alone")
    _real_phase181(alone)
    alone_bytes = (alone["state_path"].read_bytes(), alone["events_path"].read_bytes())

    case = _case(tmp_path / "real-b")
    first_calls: list[object] = []
    second_calls: list[object] = []
    out = _call(case, transport=case["h"].success_transport(first_calls), next_transport=case["h"].failure_transport(second_calls))
    assert type(out) is StepRuntimeExecutionFailure and out.step_index == 8
    assert out.invocation_result.category == "api_error"
    assert len(first_calls) == len(second_calls) == 1
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == alone_bytes
    assert '"step_id":"step-8"' not in case["events_path"].read_text()


def test_19_real_c_invalid_second_approval_after_durable_phase181_commit(tmp_path: Path) -> None:
    case = _case(tmp_path / "real-c")
    first_calls: list[object] = []
    second_calls: list[object] = []
    with pytest.raises(Phase155Error) as caught:
        _call(
            case,
            transport=case["h"].success_transport(first_calls),
            next_execution_approval=None,
            next_transport=case["h"].success_transport(second_calls),
        )
    assert caught.value.detail.classification == "approval_contract"
    assert len(first_calls) == 1 and len(second_calls) == 0
    assert case["state_path"].read_text().find('"current_step_index":8') >= 0
    assert '"step_id":"step-8"' not in case["events_path"].read_text()


def test_20_real_d_stop_routes_inline_identity_capture_zero_and_phase155_zero(tmp_path: Path) -> None:
    h = _phase179_harness()
    for label, failed in (("complete", False), ("failure", True)):
        values = h.terminal_setup(tmp_path / label, steps=6, fail=failed)
        stop = h.failure_outcome(values["workflow"], 6) if failed else h.complete_decision(values["workflow"], 6)
        before = (values["state_path"].read_bytes(), values["events_path"].read_bytes())
        captured: list[object] = []
        executed: list[object] = []
        out = phase182(
            stop, values["workflow"], None, None, values["state_path"], values["events_path"],
            None, None, None, None, None, None, None, None, None, None,
            phase181_function=lambda *args, **kwargs: stop,
            phase147_function=lambda *args: captured.append(args),
            phase155_function=lambda *args: executed.append(args),
        )
        assert out is stop and captured == [] and executed == []
        assert (values["state_path"].read_bytes(), values["events_path"].read_bytes()) == before
