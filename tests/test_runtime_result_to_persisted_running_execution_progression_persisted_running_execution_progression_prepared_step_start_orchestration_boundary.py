"""Focused contract tests for Issue #404 Phase 185."""

# ruff: noqa: E501,I001

import importlib.util
import inspect
import json
from pathlib import Path

import pytest

from ai_office.engine import (
    PersistedExecutionOutcome,
    PreparedStepExecutionStart,
    PreparedWorkflowStep,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryCompatibilityError as Phase185Error,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError,
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryFailureDetail,
    WorkflowProgressionDecision,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary as phase146,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary as phase184,
    route_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_prepared_step_start_orchestration_boundary as phase185,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterChainReentryContinuationError as Phase146Error,
)
from ai_office.engine.prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary import (
    PreparedStepStartCycleHandoffChainBridgeOuterReentryContinuationError as Phase138Error,
    route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary as phase138,
)
from ai_office.engine.runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary import (
    RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionApprovedPreparationOrchestrationBoundaryError as Phase184Error,
)

_HARNESS_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_persisted_running_execution_progression_approved_preparation_orchestration_boundary.py"
)
_TERMINAL_HARNESS_PATH = Path(__file__).with_name(
    "test_runtime_result_to_persisted_running_execution_progression_orchestration_boundary.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _harness():
    return _load(_HARNESS_PATH, "phase184_harness_for_185")


def _terminal_harness():
    return _load(_TERMINAL_HARNESS_PATH, "terminal_harness_for_185")


def _case(tmp_path: Path, *, steps: int = 9) -> dict[str, object]:
    return _harness()._scenario(tmp_path, steps=steps)


def _args(case: dict[str, object], **overrides: object) -> list[object]:
    return [
        overrides.get("result", case["result"]),
        overrides.get("workflow", case["workflow"]),
        overrides.get("preparation_approval", case["approval"]),
        overrides.get("employee", case["employee"]),
        overrides.get("state_path", case["state_path"]),
        overrides.get("events_path", case["events_path"]),
        overrides.get("resolved_tools", case["tools"]),
        overrides.get("api_key", case["api_key"]),
        overrides.get("execution_approval", case["execution_approval"]),
        overrides.get("transport", None),
        overrides.get("next_preparation_approval", case["next_approval"]),
        overrides.get("next_employee", case["next_employee"]),
        overrides.get("next_resolved_tools", case["tools"]),
        overrides.get("next_api_key", case["api_key"]),
        overrides.get("next_execution_approval", case["next_execution_approval"]),
        overrides.get("next_transport", None),
        overrides.get("following_preparation_approval", case["following_approval"]),
        overrides.get("following_employee", case["following_employee"]),
    ]


def _call(case: dict[str, object], **overrides: object) -> object:
    dependency_names = {"phase184_function", "phase146_function"}
    dependencies = {name: overrides.pop(name) for name in tuple(overrides) if name in dependency_names}
    return phase185(*_args(case, **overrides), **dependencies)


def _start(prepared: PreparedWorkflowStep, workflow: object) -> PreparedStepExecutionStart:
    from ai_office.invocation import ModelInvocationRequest
    from ai_office.runtime import WorkflowExecutionState

    return PreparedStepExecutionStart(
        ModelInvocationRequest(
            prepared.model,
            prepared.employee_instructions,
            prepared.step_instructions,
            prepared.allowed_tool_names,
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


def _real_phase184(case: dict[str, object]) -> PreparedWorkflowStep | WorkflowProgressionDecision | PersistedExecutionOutcome:
    h = case["h"]
    return phase184(
        *_args(
            case,
            transport=h.success_transport([]),  # type: ignore[attr-defined]
            next_transport=h.success_transport([]),  # type: ignore[attr-defined]
        )
    )


def _prepared(case: dict[str, object]) -> PreparedWorkflowStep:
    value = _real_phase184(case)
    assert type(value) is PreparedWorkflowStep
    return value


def _terminal_stop(tmp_path: Path, *, failed: bool = False):
    h = _terminal_harness()
    values = h.terminal_setup(tmp_path, steps=6, fail=failed)
    stop = h.failure_outcome(values["workflow"], 6) if failed else h.complete_decision(values["workflow"], 6)
    return values, stop


def _classification(callable_, expected: str) -> None:
    with pytest.raises(Phase185Error) as caught:
        callable_()
    assert caught.value.detail.classification == expected
    assert "secret" not in str(caught.value)


def _mutate_targets(case: dict[str, object], committed: tuple[bytes, bytes], mode: str, suffix: bytes) -> None:
    if mode in ("state", "both"):
        case["state_path"].write_bytes(committed[0] + suffix)  # type: ignore[union-attr]
    if mode in ("events", "both"):
        case["events_path"].write_bytes(committed[1] + suffix)  # type: ignore[union-attr]


def _rewrite_event(path: Path, position: int, **changes: object) -> None:
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    lines[position - 1].update(changes)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def test_01_public_api_signature_exports_and_source_boundary() -> None:
    params = list(inspect.signature(phase185).parameters.values())
    assert [p.name for p in params[:18]] == [
        "result", "workflow", "preparation_approval", "employee", "state_path", "events_path",
        "resolved_tools", "api_key", "execution_approval", "transport", "next_preparation_approval",
        "next_employee", "next_resolved_tools", "next_api_key", "next_execution_approval", "next_transport",
        "following_preparation_approval", "following_employee",
    ]
    assert [p.name for p in params[18:]] == ["phase184_function", "phase146_function"]
    assert params[18].default is phase184 and params[19].default is phase146
    assert issubclass(Phase185Error, RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryError)
    assert RuntimeResultToPersistedRunningExecutionProgressionPersistedRunningExecutionProgressionPreparedStepStartOrchestrationBoundaryFailureDetail
    source = Path(phase185.__code__.co_filename).read_text()
    assert source.count("phase184_function(") == 1
    assert source.count("phase146_function(") == 1
    route_source = source[source.index("def route_") : source.index("def _check_inputs")]
    assert "while " not in route_source and "for " not in route_source
    assert "route_runtime_result_to_persisted_running_execution_progression_orchestration_boundary(" not in source


def test_02_phase184_is_called_once_with_exact_eighteen_positional_arguments(tmp_path: Path) -> None:
    case = _case(tmp_path / "phase184")
    prepared = _prepared(case)
    calls = []
    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return prepared
    out = _call(case, phase184_function=dependency, phase146_function=lambda *args: _start(prepared, case["workflow"]))
    assert type(out) is PreparedStepExecutionStart
    assert len(calls) == 1 and len(calls[0][0]) == 18 and calls[0][1] == {}
    assert calls[0][0][0] is case["result"] and calls[0][0][17] is case["following_employee"]


def test_03_phase146_is_called_once_with_exact_five_positional_arguments(tmp_path: Path) -> None:
    case = _case(tmp_path / "phase146")
    prepared = _prepared(case)
    calls = []
    def dependency(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return _start(prepared, case["workflow"])
    out = _call(case, phase184_function=lambda *args: prepared, phase146_function=dependency)
    assert type(out) is PreparedStepExecutionStart
    assert len(calls) == 1 and len(calls[0][0]) == 5 and calls[0][1] == {}
    assert calls[0][0] == (prepared, case["workflow"], case["following_employee"], case["state_path"], case["events_path"])


def test_04_following_employee_is_passed_not_earlier_employee(tmp_path: Path) -> None:
    case = _case(tmp_path / "ownership")
    prepared = _prepared(case)
    seen = []
    _call(case, phase184_function=lambda *args: prepared, phase146_function=lambda *args: (seen.append(args[2]) or _start(prepared, case["workflow"])))
    assert seen == [case["following_employee"]]
    assert seen[0] is not case["employee"] and seen[0] is not case["next_employee"]


def test_05_following_operational_inputs_are_not_prevalidated(tmp_path: Path) -> None:
    case = _case(tmp_path / "no-prevalidation")
    prepared = _prepared(case)
    calls = []
    _classification(lambda: _call(case, following_preparation_approval=object(), following_employee=object(), phase184_function=lambda *args: (calls.append(args) or prepared), phase146_function=lambda *args: _start(prepared, case["workflow"])), "phase184_contract")
    assert len(calls) == 1  # Phase184 was entered before seam validation.


def test_06_original_workflow_complete_stop_mutation_is_phase184_contract_and_unrolled(tmp_path: Path) -> None:
    _, stop = _terminal_stop(tmp_path / "original-complete", failed=False)
    for mode in ("state", "events", "both"):
        case = _case(tmp_path / f"original-complete-{mode}")
        before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        owned = (before[0] + b"phase184-owned", before[1] + b"phase184-owned")
        phase184_calls: list[object] = []
        phase146_calls: list[object] = []

        def dependency(*args: object) -> object:
            phase184_calls.append(args)
            _mutate_targets(case, before, mode, b"phase184-owned")
            return stop

        with pytest.raises(Phase185Error) as caught:
            _call(case, result=stop, phase184_function=dependency, phase146_function=lambda *args: phase146_calls.append(args))
        assert caught.value.detail.classification == "phase184_contract"
        assert len(phase184_calls) == 1 and phase146_calls == []
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == (
            owned[0] if mode in ("state", "both") else before[0],
            owned[1] if mode in ("events", "both") else before[1],
        )
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) != before


def test_07_original_persisted_failure_stop_mutation_is_phase184_contract_and_unrolled(tmp_path: Path) -> None:
    _, stop = _terminal_stop(tmp_path / "original-failure", failed=True)
    for mode in ("state", "events", "both"):
        case = _case(tmp_path / f"original-failure-{mode}")
        before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        phase184_calls: list[object] = []
        phase146_calls: list[object] = []

        def dependency(*args: object) -> object:
            phase184_calls.append(args)
            _mutate_targets(case, before, mode, b"phase184-owned")
            return stop

        with pytest.raises(Phase185Error) as caught:
            _call(case, result=stop, phase184_function=dependency, phase146_function=lambda *args: phase146_calls.append(args))
        assert caught.value.detail.classification == "phase184_contract"
        assert len(phase184_calls) == 1 and phase146_calls == []
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == (
            before[0] + b"phase184-owned" if mode in ("state", "both") else before[0],
            before[1] + b"phase184-owned" if mode in ("events", "both") else before[1],
        )
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) != before


def test_08_phase184_workflow_complete_stop_bypasses_phase146(tmp_path: Path) -> None:
    case = _case(tmp_path / "complete", steps=7)
    h = case["h"]
    stop = phase184(
        *_args(
            case,
            transport=h.success_transport([]),  # type: ignore[attr-defined]
            next_transport=h.success_transport([]),  # type: ignore[attr-defined]
        )
    )
    assert type(stop) is WorkflowProgressionDecision and stop.decision == "workflow_complete"
    calls = []
    out = _call(case, phase184_function=lambda *args: (calls.append(args) or stop), phase146_function=lambda *args: pytest.fail("called"))
    assert out is stop and calls


def test_09_phase184_persisted_failure_stop_bypasses_phase146(tmp_path: Path) -> None:
    case = _case(tmp_path / "failure", steps=8)
    h = case["h"]
    stop = phase184(
        *_args(
            case,
            transport=h.success_transport([]),  # type: ignore[attr-defined]
            next_transport=h.failure_transport([]),  # type: ignore[attr-defined]
        )
    )
    assert type(stop) is PersistedExecutionOutcome and stop.outcome == "persisted_failure"
    out = _call(case, phase184_function=lambda *args: stop, phase146_function=lambda *args: pytest.fail("called"))
    assert out is stop


def test_10_default_phase185_real_phase184_to_phase146_step9_start_is_read_only(tmp_path: Path) -> None:
    control = _case(tmp_path / "control")
    control_h = control["h"]
    control_first: list[object] = []
    control_second: list[object] = []
    control_prepared = phase184(
        *_args(
            control,
            transport=control_h.success_transport(control_first),  # type: ignore[attr-defined]
            next_transport=control_h.success_transport(control_second),  # type: ignore[attr-defined]
        )
    )
    assert type(control_prepared) is PreparedWorkflowStep
    post_phase184 = (control["state_path"].read_bytes(), control["events_path"].read_bytes())

    case = _case(tmp_path / "real")
    h = case["h"]
    first_calls: list[object] = []
    second_calls: list[object] = []
    out = phase185(
        *_args(
            case,
            transport=h.success_transport(first_calls),  # type: ignore[attr-defined]
            next_transport=h.success_transport(second_calls),  # type: ignore[attr-defined]
        )
    )
    assert type(out) is PreparedStepExecutionStart
    assert out.running_state.current_step_index == 9 and out.request.model == "model-name"
    assert len(control_first) == 1 and len(control_second) == 1
    assert len(first_calls) == 1 and len(second_calls) == 1
    assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == post_phase184
    assert json.loads(case["state_path"].read_text())["current_step_index"] == 8
    assert all(json.loads(line)["step_id"] != "step-9" for line in case["events_path"].read_text().splitlines())


def test_11_accumulated_provenance_narrowness_reuses_phase146_phase138_contract(tmp_path: Path) -> None:
    valid = _case(tmp_path / "aged-none-valid")
    valid_prepared = _prepared(valid)
    _rewrite_event(valid["events_path"], 2, provider="anthropic", request_id="older-request")
    valid_lines = [json.loads(line) for line in valid["events_path"].read_text().splitlines()]
    assert valid_lines[1]["provider"] == "anthropic" and valid_lines[1]["request_id"] == "older-request"
    assert valid_lines[4]["provider"] == "openai" and valid_lines[4]["request_id"] is None
    valid_out = _call(valid, phase184_function=lambda *args: valid_prepared)
    assert type(valid_out) is PreparedStepExecutionStart

    for label, position, changes in (
        ("early-position", 4, {"provider": "openai", "request_id": None}),
        ("non-openai", 5, {"provider": "anthropic", "request_id": None}),
    ):
        case = _case(tmp_path / label)
        prepared = _prepared(case)
        before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        _rewrite_event(case["events_path"], position, **changes)
        invalid_before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        with pytest.raises(Phase138Error) as lower_caught:
            phase138(prepared, case["workflow"], case["following_employee"], case["state_path"], case["events_path"])
        assert lower_caught.value.detail.classification == "terminal_contract"
        with pytest.raises(Phase146Error) as caught:
            phase146(prepared, case["workflow"], case["following_employee"], case["state_path"], case["events_path"])
        assert caught.value.detail.classification == "terminal_contract"
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == invalid_before
        assert invalid_before != before


def test_12_phase184_safe_error_identity_and_owned_mutations_are_preserved(tmp_path: Path) -> None:
    _, stop = _terminal_stop(tmp_path / "safe-stop")
    for mode in ("state", "events", "both"):
        case = _case(tmp_path / f"safe-{mode}")
        before = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        safe = Phase184Error("secret-safe-error")
        phase184_calls: list[object] = []
        phase146_calls: list[object] = []

        def dependency(*args: object) -> object:
            phase184_calls.append(args)
            _mutate_targets(case, before, mode, b"safe-owned")
            raise safe

        with pytest.raises(Phase184Error) as caught:
            _call(case, phase184_function=dependency, phase146_function=lambda *args: phase146_calls.append(args))
        assert caught.value is safe
        assert len(phase184_calls) == 1 and phase146_calls == []
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == (
            before[0] + b"safe-owned" if mode in ("state", "both") else before[0],
            before[1] + b"safe-owned" if mode in ("events", "both") else before[1],
        )
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) != before


def test_13_unexpected_phase184_error_is_sanitized(tmp_path: Path) -> None:
    case = _case(tmp_path / "unexpected")
    _classification(lambda: _call(case, phase184_function=lambda *args: (_ for _ in ()).throw(RuntimeError("secret"))), "dependency_error")


def test_14_malformed_phase184_output_skips_phase146(tmp_path: Path) -> None:
    case = _case(tmp_path / "malformed")
    _classification(lambda: _call(case, phase184_function=lambda *args: object(), phase146_function=lambda *args: pytest.fail("called")), "phase184_contract")


def test_15_malformed_phase146_output_is_contract_error(tmp_path: Path) -> None:
    case = _case(tmp_path / "bad-start")
    prepared = _prepared(case)
    _classification(lambda: _call(case, phase184_function=lambda *args: prepared, phase146_function=lambda *args: object()), "phase146_contract")


def test_16_phase146_and_phase138_safe_errors_restore_committed_snapshot_once(tmp_path: Path) -> None:
    for error_type in (Phase146Error, Phase138Error):
        for mode in ("state", "events", "both"):
            case = _case(tmp_path / f"safe-{error_type.__name__}-{mode}")
            prepared = _prepared(case)
            committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
            dependency_calls: list[object] = []
            safe = error_type("phase146-safe")

            def dependency(*args: object) -> object:
                dependency_calls.append(args)
                _mutate_targets(case, committed, mode, b"phase146-owned")
                raise safe

            with pytest.raises(error_type) as caught:
                _call(case, phase184_function=lambda *args: prepared, phase146_function=dependency)
            assert caught.value is safe and len(dependency_calls) == 1
            assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_17_unexpected_phase146_error_is_sanitized_and_compensated_without_retry(tmp_path: Path) -> None:
    for mode in ("state", "events", "both"):
        case = _case(tmp_path / f"phase146-unexpected-{mode}")
        prepared = _prepared(case)
        committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        phase184_calls: list[object] = []
        phase146_calls: list[object] = []

        def phase184_dependency(*args: object) -> object:
            phase184_calls.append(args)
            return prepared

        def dependency(*args: object) -> object:
            phase146_calls.append(args)
            _mutate_targets(case, committed, mode, b"unexpected-owned")
            raise RuntimeError("secret execution payload")

        _classification(lambda: _call(case, phase184_function=phase184_dependency, phase146_function=dependency), "dependency_error")
        assert len(phase184_calls) == 1 and len(phase146_calls) == 1
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_18_valid_phase146_return_mutation_is_committed_mutation_and_compensated(tmp_path: Path) -> None:
    for mode in ("state", "events", "both"):
        case = _case(tmp_path / f"mutation-{mode}")
        prepared = _prepared(case)
        committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
        dependency_calls: list[object] = []

        def dependency(*args: object) -> object:
            dependency_calls.append(args)
            _mutate_targets(case, committed, mode, b"valid-return-owned")
            return _start(prepared, case["workflow"])

        _classification(lambda: _call(case, phase184_function=lambda *args: prepared, phase146_function=dependency), "committed_mutation")
        assert len(dependency_calls) == 1
        assert (case["state_path"].read_bytes(), case["events_path"].read_bytes()) == committed


def test_19_unexpected_phase146_rollback_failure_attempts_both_targets_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(tmp_path / "rollback")
    prepared = _prepared(case)
    committed = (case["state_path"].read_bytes(), case["events_path"].read_bytes())
    phase184_calls: list[object] = []
    phase146_calls: list[object] = []
    restore_attempts: list[Path] = []
    original = Path.write_bytes

    def fail_restore(path: Path, data: bytes) -> int:
        restore_attempts.append(path)
        if path == case["events_path"]:
            raise OSError("restore")
        return original(path, data)

    def phase184_dependency(*args: object) -> object:
        phase184_calls.append(args)
        return prepared

    def dependency(*args: object) -> object:
        phase146_calls.append(args)
        original(case["state_path"], committed[0] + b"mutated-state")
        original(case["events_path"], committed[1] + b"mutated-events")
        raise RuntimeError("secret execution payload")

    monkeypatch.setattr(Path, "write_bytes", fail_restore)
    _classification(lambda: _call(case, phase184_function=phase184_dependency, phase146_function=dependency), "rollback_failure")
    assert len(phase184_calls) == 1 and len(phase146_calls) == 1
    assert restore_attempts.count(case["state_path"]) == 1
    assert restore_attempts.count(case["events_path"]) == 1
    assert case["state_path"].read_bytes() == committed[0]
    assert case["events_path"].read_bytes() == committed[1] + b"mutated-events"


def test_20_invalid_inputs_are_rejected_before_dependency_calls(tmp_path: Path) -> None:
    case = _case(tmp_path / "inputs")
    calls = []
    def dependency(*args: object) -> object:
        calls.append(args)
        return _prepared(case)
    invalid_prepare = WorkflowProgressionDecision(
        "prepare_next_step", "w", "step-6", 6, "e6", "step-7", 7, "e7", "next_step_available"
    )
    _classification(lambda: _call(case, result=invalid_prepare, phase184_function=dependency), "result_type")
    _classification(lambda: _call(case, workflow=object(), phase184_function=dependency), "workflow_definition")
    _classification(lambda: _call(case, state_path=case["events_path"], phase184_function=dependency), "target_conflict")
    _classification(lambda: _call(case, phase184_function=object()), "configuration")
    assert calls == []
