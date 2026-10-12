"""Offline proof for the bounded Job authorization vertical slice."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from multiprocessing import get_context
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.engine.workflow_job_authorization as job_authorization_module
from ai_office.cli import app
from ai_office.definitions.employee import EmployeeDefinition
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.approved_workflow_continuation_cycle import (
    route_approved_workflow_continuation_cycle,
)
from ai_office.engine.approved_workflow_fresh_start import (
    ApprovedWorkflowBootstrapContext,
    FreshWorkflowBootstrapCompatibilityError,
    InitialStepPreparationApproval,
    route_approved_workflow_fresh_start,
)
from ai_office.engine.artifact import list_run_artifacts, read_run_artifact
from ai_office.engine.next_step_preparation import (
    NextStepPreparationApproval,
    _prepare_approved_next_workflow_step,
)
from ai_office.engine.persisted_execution_outcome_reentry import (
    validate_loaded_persisted_execution_history,
)
from ai_office.engine.prepared_step_execution_start import (
    prepare_prepared_step_execution_start,
)
from ai_office.engine.workflow_approval_evidence import (
    approve_business_step,
    persist_business_approval_evidence,
)
from ai_office.engine.workflow_job_authorization import (
    WorkflowJobAuthorizationEnvelope,
    WorkflowJobAuthorizationError,
    inspect_workflow_job_authorization,
    issue_workflow_job_authorization,
    load_workflow_job_authorization,
    reserve_job_attempt,
    revoke_workflow_job_authorization,
)
from ai_office.engine.workflow_progression import WorkflowProgressionDecision
from ai_office.engine.workflow_run_manifest import load_workflow_run_manifest
from ai_office.execution_evidence import (
    ExecutionEvidenceError,
    list_run_execution_evidence,
    load_normalized_result_evidence,
    load_raw_response_evidence,
)
from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    execution_target_fingerprint,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    approve_model_invocation_execution,
    build_model_invocation_execution_fingerprint,
)
from ai_office.providers.openai import (
    OpenAIApiKey,
    OpenAIResponsesRawHttpResponse,
)
from ai_office.storage import WorkflowExecutionPersistenceTargets
from ai_office.storage.workflow_execution_history import (
    load_workflow_execution_history_with_source_digests,
)
from tests._run_test_support import create_test_run


def _competing_reservation_worker(
    authorization, run_binding, request, gate, results
) -> None:
    gate.wait()
    try:
        reserve_job_attempt(
            authorization,
            binding=run_binding,
            workflow_id=authorization.envelope.workflow_id,
            step_id=authorization.envelope.step_id,
            step_index=authorization.envelope.step_index,
            employee_id=authorization.envelope.employee_id,
            request=request,
            resolved_tools=(),
            target=DIRECT_OPENAI_EXECUTION_TARGET,
            request_body_sha256="0" * 64,
            request_body_length=0,
        )
    except WorkflowJobAuthorizationError as error:
        results.put(error.classification)
    else:
        results.put("reserved")


def _workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "job-auth-workflow",
            "name": "Job Authorization Workflow",
            "description": "One exact action, offline only.",
            "steps": [
                {
                    "id": "draft",
                    "name": "Draft",
                    "employee": "writer",
                    "instructions": "Write the assigned artifact.",
                    "business_approval_required": True,
                    "artifact_content_type": "text/markdown",
                }
            ],
        }
    )


def _employee() -> EmployeeDefinition:
    return EmployeeDefinition(
        id="writer",
        name="Writer",
        role="Artifact writer",
        instructions="Follow the exact assigned task.",
        model="job-test-model",
        allowed_tools=[],
    )


def _run_and_authorization(
    root: Path,
    run_id: str,
    *,
    expires_at_ns: int = 9_999_999_999_999_999_999,
):
    workflow = _workflow()
    employee = _employee()
    run = create_test_run(
        root, run_id, workflow, (employee,), with_business_approvals=False
    )
    run.store.root.chmod(0o700)
    manifest = load_workflow_run_manifest(run.store, run_id)
    request = ModelInvocationRequest(
        model=employee.model,
        system_instructions=employee.instructions,
        task_instructions=workflow.steps[0].instructions,
        allowed_tools=(),
        run_id=run_id,
        manifest_digest=run.binding.manifest_digest,
        run_input=manifest.run_input,
    )
    fingerprint = build_model_invocation_execution_fingerprint(
        request, (), DIRECT_OPENAI_EXECUTION_TARGET
    )
    envelope = WorkflowJobAuthorizationEnvelope(
        job_id=f"job-{run_id}",
        run_id=run_id,
        manifest_digest=manifest.digest,
        workflow_id=workflow.id,
        step_id=workflow.steps[0].id,
        step_index=1,
        employee_id=employee.id,
        run_input_sha256=sha256(manifest.run_input.encode()).hexdigest(),
        provider=DIRECT_OPENAI_EXECUTION_TARGET.provider,
        execution_target_fingerprint=execution_target_fingerprint(
            DIRECT_OPENAI_EXECUTION_TARGET
        ),
        model=employee.model,
        request_fingerprint=fingerprint,
        expires_at_ns=expires_at_ns,
    )
    authorization = issue_workflow_job_authorization(
        run.store,
        envelope,
        request=request,
        resolved_tools=(),
        target=DIRECT_OPENAI_EXECUTION_TARGET,
        business_approved_by="local-operator-label",
        business_grant_id=f"business-{run_id}",
        execution_approved_by="local-operator-label",
        execution_grant_id=f"execution-{run_id}",
        now_ns=1,
    )
    return workflow, employee, run, request, authorization


def _success_transport(calls: list[object], run, authorization):
    def transport(_request: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(_request)
        attempts = list_run_execution_evidence(run.store.root, run.binding.run_id)
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt.authorization_source == "job_grant"
        assert attempt.execution_approval_id is None
        inspection = inspect_workflow_job_authorization(authorization)
        assert inspection.attempts_reserved == 1
        assert inspection.dispatch_admitted is True
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", "offline-job-1"),),
            b'{"id":"response-job-1","object":"response","status":"completed",'
            b'"output":[{"type":"message","content":[{"type":"output_text",'
            b'"text":"# Offline result\\n\\nBounded grant path verified."}]}]}',
        )

    return transport


def _provider_evidence_context(workflow, employee, run, request, authorization):
    from ai_office.execution_evidence import build_execution_evidence_context

    return build_execution_evidence_context(
        store_root=run.store.root,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id="draft",
        step_index=1,
        employee_id=employee.id,
        request=request,
        resolved_tools=(),
        approval=None,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
        job_authorization=authorization,
    )


def test_fresh_start_job_authority_uses_shared_provider_evidence_and_artifact(
    tmp_path: Path,
) -> None:
    workflow, employee, run, _request, authorization = _run_and_authorization(
        tmp_path / "store", "job-run-1"
    )
    calls: list[object] = []
    context = ApprovedWorkflowBootstrapContext(
        preparation_approval=InitialStepPreparationApproval(
            approved=False,
            workflow_id=workflow.id,
            step_id=workflow.steps[0].id,
            step_index=1,
            employee_id=employee.id,
        ),
        employee=employee,
        resolved_tools=(),
        api_key=OpenAIApiKey(value=SecretStr("synthetic-test-only")),
        execution_approval=None,
        transport=_success_transport(calls, run, authorization),
        binding=run.binding,
        manifest_store=run.store,
        job_authorization=authorization,
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )

    outcome = route_approved_workflow_fresh_start(
        workflow, run.state_path, run.events_path, context
    )

    assert type(outcome) is WorkflowProgressionDecision
    assert outcome.decision == "workflow_complete"
    assert len(calls) == 1
    attempts = list_run_execution_evidence(run.store.root, run.binding.run_id)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.authorization_source == "job_grant"
    raw = load_raw_response_evidence(
        run.store.root, run.binding.run_id, attempt.attempt_id
    )
    normalized = load_normalized_result_evidence(
        run.store.root, run.binding.run_id, attempt.attempt_id
    )
    assert raw.request_id == "offline-job-1"
    assert normalized.category == "success"
    artifact = list_run_artifacts(run.store, run.binding.run_id)[0]
    assert read_run_artifact(
        run.store, run.binding.run_id, artifact.artifact_id
    ).content.startswith(b"# Offline result")
    assert not tuple(run.store.root.glob(f"{run.binding.run_id}.business-approval.*"))
    assert not tuple(run.store.root.glob(f"{run.binding.run_id}.execution-approval.*"))
    persisted = load_workflow_job_authorization(run.store, authorization.envelope)
    assert persisted.business_grant.grant_id != persisted.execution_grant.grant_id
    assert (
        inspect_workflow_job_authorization(persisted).provider_response_recorded is True
    )


def test_job_authority_mismatch_expiry_and_revocation_fail_before_transport(
    tmp_path: Path,
) -> None:
    workflow, employee, run, request, authorization = _run_and_authorization(
        tmp_path / "store", "job-run-2"
    )
    calls: list[object] = []
    expired = replace(
        authorization,
        envelope=replace(authorization.envelope, expires_at_ns=2),
    )
    # The loaded immutable record is authoritative; a caller-mutated envelope
    # cannot replace its persisted grant scope.
    bad_context = ApprovedWorkflowBootstrapContext(
        preparation_approval=InitialStepPreparationApproval(
            False, workflow.id, "draft", 1, employee.id
        ),
        employee=employee,
        resolved_tools=(),
        api_key=OpenAIApiKey(value=SecretStr("synthetic-test-only")),
        execution_approval=None,
        transport=lambda value: calls.append(value),
        binding=run.binding,
        manifest_store=run.store,
        job_authorization=expired,
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    with pytest.raises(FreshWorkflowBootstrapCompatibilityError):
        route_approved_workflow_fresh_start(
            workflow, run.state_path, run.events_path, bad_context
        )
    assert calls == []


def test_competing_processes_consume_only_one_job_attempt_slot(tmp_path: Path) -> None:
    _workflow_value, _employee_value, run, request, authorization = (
        _run_and_authorization(tmp_path / "store", "job-run-race")
    )
    context = get_context("fork")
    gate = context.Barrier(2)
    results = context.Queue()
    workers = [
        context.Process(
            target=_competing_reservation_worker,
            args=(authorization, run.binding, request, gate, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
    assert all(worker.exitcode == 0 for worker in workers)
    outcomes = sorted(results.get(timeout=2) for _ in workers)
    assert outcomes == ["attempt_allowance_depleted", "reserved"]
    inspection = inspect_workflow_job_authorization(authorization)
    assert inspection.attempts_reserved == 1
    assert inspection.dispatch_admitted is False


def test_reservation_before_run_claim_is_not_refunded_after_interruption(
    tmp_path: Path,
) -> None:
    workflow, employee, run, request, authorization = _run_and_authorization(
        tmp_path / "store", "job-run-crash-before-claim"
    )
    reservation = reserve_job_attempt(
        authorization,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id="draft",
        step_index=1,
        employee_id=employee.id,
        request=request,
        resolved_tools=(),
        target=DIRECT_OPENAI_EXECUTION_TARGET,
        request_body_sha256="a" * 64,
        request_body_length=41,
    )
    assert len(list_run_execution_evidence(run.store.root, run.binding.run_id)) == 0
    assert inspect_workflow_job_authorization(authorization).attempts_reserved == 1
    assert len(reservation) == 64
    with pytest.raises(WorkflowJobAuthorizationError) as caught:
        reserve_job_attempt(
            authorization,
            binding=run.binding,
            workflow_id=workflow.id,
            step_id="draft",
            step_index=1,
            employee_id=employee.id,
            request=request,
            resolved_tools=(),
            target=DIRECT_OPENAI_EXECUTION_TARGET,
            request_body_sha256="a" * 64,
            request_body_length=41,
        )
    assert caught.value.classification == "attempt_allowance_depleted"


def test_revocation_after_attempt_claim_blocks_dispatch_and_keeps_slot_consumed(
    tmp_path: Path,
) -> None:
    workflow, employee, run, request, authorization = _run_and_authorization(
        tmp_path / "store", "job-run-revoke-dispatch"
    )
    from ai_office.execution_evidence import build_execution_evidence_context
    from ai_office.providers.openai import execute_openai_model_invocation

    context = build_execution_evidence_context(
        store_root=run.store.root,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id="draft",
        step_index=1,
        employee_id=employee.id,
        request=request,
        resolved_tools=(),
        approval=None,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
        job_authorization=authorization,
    )
    calls: list[object] = []
    with pytest.raises(ExecutionEvidenceError) as caught:
        execute_openai_model_invocation(
            request,
            (),
            OpenAIApiKey(value=SecretStr("synthetic-test-only")),
            None,
            transport=lambda value: calls.append(value),
            execution_evidence=context,
            before_transport=lambda: revoke_workflow_job_authorization(
                authorization, now_ns=3
            ),
        )
    assert caught.value.classification == "job_expired_revoked_or_binding"
    assert calls == []
    attempts = list_run_execution_evidence(run.store.root, run.binding.run_id)
    assert len(attempts) == 1
    assert attempts[0].authorization_source == "job_grant"
    status = inspect_workflow_job_authorization(authorization)
    assert status.attempts_reserved == 1
    assert status.dispatch_admitted is False
    # A restart/repeated command cannot refund the durable reservation.
    with pytest.raises(ExecutionEvidenceError) as caught_repeat:
        execute_openai_model_invocation(
            request,
            (),
            OpenAIApiKey(value=SecretStr("synthetic-test-only")),
            None,
            transport=lambda value: calls.append(value),
            execution_evidence=context,
        )
    assert caught_repeat.value.classification == "job_reservation"
    assert calls == []


def test_concurrent_revocation_wins_before_dispatch_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, employee, run, request, authorization = _run_and_authorization(
        tmp_path / "store", "job-run-revoke-race"
    )
    from ai_office.providers.openai import execute_openai_model_invocation

    context = _provider_evidence_context(
        workflow, employee, run, request, authorization
    )
    calls: list[object] = []
    original_lock = job_authorization_module._job_lifecycle_lock
    original_persist = job_authorization_module._persist_immutable
    revocation_write_waiting = threading.Event()
    revocation_committed = threading.Event()
    release_revoker = threading.Event()
    provider_lock_attempted = threading.Event()
    provider_result: list[object] = []
    revoke_errors: list[BaseException] = []
    revoke_path = run.store.root / (
        f"{run.binding.run_id}.job-revoked.{authorization.envelope.job_id}.json"
    )

    @contextmanager
    def observe_lock(store_root, envelope):
        if threading.current_thread().name == "provider":
            provider_lock_attempted.set()
        with original_lock(store_root, envelope):
            yield

    def hold_revocation_commit(path, payload):
        if path == revoke_path:
            revocation_write_waiting.set()
            assert release_revoker.wait(timeout=5)
        original_persist(path, payload)
        if path == revoke_path:
            revocation_committed.set()

    def revoke():
        try:
            revoke_workflow_job_authorization(authorization, now_ns=20)
        except BaseException as error:
            revoke_errors.append(error)

    def invoke():
        try:
            provider_result.append(
                execute_openai_model_invocation(
                    request,
                    (),
                    OpenAIApiKey(value=SecretStr("synthetic-test-only")),
                    None,
                    transport=lambda value: calls.append(value),
                    execution_evidence=context,
                )
            )
        except BaseException as error:
            provider_result.append(error)

    monkeypatch.setattr(job_authorization_module, "_job_lifecycle_lock", observe_lock)
    monkeypatch.setattr(
        job_authorization_module, "_persist_immutable", hold_revocation_commit
    )
    revoker = threading.Thread(target=revoke, name="revoker")
    revoker.start()
    assert revocation_write_waiting.wait(timeout=5)
    provider = threading.Thread(target=invoke, name="provider")
    provider.start()
    assert provider_lock_attempted.wait(timeout=5)
    assert calls == []
    release_revoker.set()
    revoker.join(timeout=5)
    provider.join(timeout=5)

    assert not revoker.is_alive() and not provider.is_alive()
    assert revocation_committed.is_set()
    assert revoke_errors == []
    assert len(provider_result) == 1
    assert isinstance(provider_result[0], ExecutionEvidenceError)
    assert calls == []
    inspection = inspect_workflow_job_authorization(authorization)
    assert inspection.revoked is True
    assert inspection.dispatch_admitted is False
    assert inspection.lifecycle_order == "revocation_before_dispatch"
    assert inspection.attempts_reserved == 1


def test_concurrent_dispatch_admission_wins_and_is_durably_ordered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, employee, run, request, authorization = _run_and_authorization(
        tmp_path / "store", "job-run-dispatch-race"
    )
    from ai_office.providers.openai import execute_openai_model_invocation

    context = _provider_evidence_context(
        workflow, employee, run, request, authorization
    )
    calls: list[object] = []
    original_lock = job_authorization_module._job_lifecycle_lock
    original_persist = job_authorization_module._persist_immutable
    dispatch_write_waiting = threading.Event()
    release_dispatch_write = threading.Event()
    revoke_lock_attempted = threading.Event()
    revoke_done = threading.Event()
    provider_result: list[object] = []
    revoke_errors: list[BaseException] = []
    dispatch_path = run.store.root / (
        f"{run.binding.run_id}.job-dispatch.{authorization.envelope.job_id}.json"
    )

    @contextmanager
    def observe_lock(store_root, envelope):
        if threading.current_thread().name == "revoker":
            revoke_lock_attempted.set()
        with original_lock(store_root, envelope):
            yield

    def hold_dispatch_commit(path, payload):
        if path == dispatch_path:
            dispatch_write_waiting.set()
            assert release_dispatch_write.wait(timeout=5)
        original_persist(path, payload)

    def invoke():
        try:
            provider_result.append(
                execute_openai_model_invocation(
                    request,
                    (),
                    OpenAIApiKey(value=SecretStr("synthetic-test-only")),
                    None,
                    transport=_success_transport(calls, run, authorization),
                    execution_evidence=context,
                )
            )
        except BaseException as error:
            provider_result.append(error)

    def revoke():
        try:
            revoke_workflow_job_authorization(authorization, now_ns=30)
        except BaseException as error:
            revoke_errors.append(error)
        finally:
            revoke_done.set()

    monkeypatch.setattr(job_authorization_module, "_job_lifecycle_lock", observe_lock)
    monkeypatch.setattr(
        job_authorization_module, "_persist_immutable", hold_dispatch_commit
    )
    provider = threading.Thread(target=invoke, name="provider")
    provider.start()
    assert dispatch_write_waiting.wait(timeout=5)
    revoker = threading.Thread(target=revoke, name="revoker")
    revoker.start()
    assert revoke_lock_attempted.wait(timeout=5)
    assert not revoke_done.wait(timeout=0.05)
    assert calls == []
    release_dispatch_write.set()
    provider.join(timeout=5)
    revoker.join(timeout=5)

    assert not provider.is_alive() and not revoker.is_alive()
    assert revoke_errors == []
    assert len(provider_result) == 1
    assert not isinstance(provider_result[0], BaseException)
    assert len(calls) == 1
    inspection = inspect_workflow_job_authorization(authorization)
    assert inspection.dispatch_admitted is True
    assert inspection.revoked is True
    assert inspection.lifecycle_order == "dispatch_before_revocation"


def test_expiry_at_locked_dispatch_admission_blocks_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [149]
    monkeypatch.setattr(job_authorization_module.time, "time_ns", lambda: clock[0])
    workflow, employee, run, request, authorization = _run_and_authorization(
        tmp_path / "store", "job-run-expiry-boundary", expires_at_ns=150
    )
    from ai_office.providers.openai import execute_openai_model_invocation

    context = _provider_evidence_context(
        workflow, employee, run, request, authorization
    )
    import ai_office.execution_evidence as execution_evidence_module

    load_attempt = execution_evidence_module.load_execution_attempt_evidence

    def cross_expiry_during_attempt_reload(*args, **kwargs):
        attempt = load_attempt(*args, **kwargs)
        clock[0] = 150
        return attempt

    monkeypatch.setattr(
        execution_evidence_module,
        "load_execution_attempt_evidence",
        cross_expiry_during_attempt_reload,
    )
    calls: list[object] = []
    with pytest.raises(ExecutionEvidenceError) as caught:
        execute_openai_model_invocation(
            request,
            (),
            OpenAIApiKey(value=SecretStr("synthetic-test-only")),
            None,
            transport=lambda value: calls.append(value),
            execution_evidence=context,
        )
    assert caught.value.classification == "job_expired_revoked_or_binding"
    assert calls == []
    inspection = inspect_workflow_job_authorization(authorization)
    assert inspection.attempts_reserved == 1
    assert inspection.dispatch_admitted is False
    assert inspection.lifecycle_order == "not_dispatched"


def test_granted_request_cannot_be_changed_at_provider_boundary(tmp_path: Path) -> None:
    workflow, employee, run, request, authorization = _run_and_authorization(
        tmp_path / "store", "job-run-request-mismatch"
    )
    from ai_office.execution_evidence import build_execution_evidence_context
    from ai_office.providers.openai import execute_openai_model_invocation

    changed_request = ModelInvocationRequest(
        model=request.model,
        system_instructions=request.system_instructions,
        task_instructions="changed after grant",
        allowed_tools=(),
        run_id=request.run_id,
        manifest_digest=request.manifest_digest,
        run_input=request.run_input,
    )
    context = build_execution_evidence_context(
        store_root=run.store.root,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id="draft",
        step_index=1,
        employee_id=employee.id,
        request=changed_request,
        resolved_tools=(),
        approval=None,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
        job_authorization=authorization,
    )
    calls: list[object] = []
    with pytest.raises(ExecutionEvidenceError):
        execute_openai_model_invocation(
            changed_request,
            (),
            OpenAIApiKey(value=SecretStr("synthetic-test-only")),
            None,
            transport=lambda value: calls.append(value),
            execution_evidence=context,
        )
    assert calls == []


def test_job_authorization_cli_inspection_is_read_only_and_concise(
    tmp_path: Path,
) -> None:
    _workflow, _employee, run, _request, authorization = _run_and_authorization(
        tmp_path / "store", "job-run-inspection"
    )
    before = sorted(path.name for path in run.store.root.iterdir())
    result = CliRunner().invoke(
        app,
        [
            "workflows",
            "job-authorization",
            run.binding.run_id,
            authorization.envelope.job_id,
            "--run-store",
            str(run.store.root),
        ],
    )
    assert result.exit_code == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["operation"] == "job-authorization"
    assert value["attempts_reserved"] == 0
    assert value["dispatch_admitted"] is False
    assert value["provider_response_recorded"] is False
    assert "not authenticated human identity" in value["security_note"]
    assert sorted(path.name for path in run.store.root.iterdir()) == before


def test_continuation_uses_job_business_and_execution_grants_without_legacy_approvals(
    tmp_path: Path,
) -> None:
    workflow = WorkflowDefinition.model_validate(
        {
            "id": "job-continuation-workflow",
            "name": "Job Continuation",
            "description": "Normal first action, Job-authorized second action.",
            "steps": [
                {
                    "id": "first",
                    "name": "First",
                    "employee": "writer-1",
                    "instructions": "Return a short source note.",
                    "business_approval_required": True,
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "writer-2",
                    "instructions": "Write the final artifact.",
                    "business_approval_required": True,
                    "artifact_content_type": "text/markdown",
                },
            ],
        }
    )
    employees = (
        EmployeeDefinition(
            id="writer-1",
            name="Writer 1",
            role="Worker",
            instructions="Write a source note.",
            model="job-test-model",
            allowed_tools=[],
        ),
        EmployeeDefinition(
            id="writer-2",
            name="Writer 2",
            role="Worker",
            instructions="Write a final artifact.",
            model="job-test-model",
            allowed_tools=[],
        ),
    )
    run = create_test_run(
        tmp_path / "continuation-store",
        "job-continuation-run",
        workflow,
        employees,
        with_business_approvals=False,
    )
    run.store.root.chmod(0o700)
    manifest = load_workflow_run_manifest(run.store, run.binding.run_id)
    business = approve_business_step(
        binding=run.binding,
        workflow_id=workflow.id,
        step_id="first",
        step_index=1,
        employee_id="writer-1",
        approved_by="local-operator-label",
        approval_id="business-first-only",
    )
    persist_business_approval_evidence(run.store, business)
    first_request = ModelInvocationRequest(
        model=employees[0].model,
        system_instructions=employees[0].instructions,
        task_instructions=workflow.steps[0].instructions,
        allowed_tools=(),
        run_id=run.binding.run_id,
        manifest_digest=run.binding.manifest_digest,
        run_input=manifest.run_input,
    )
    first_execution = approve_model_invocation_execution(
        first_request,
        (),
        provider="openai",
        approved_by="local-operator-label",
        approval_id="execution-first-only",
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    first_context = ApprovedWorkflowBootstrapContext(
        preparation_approval=InitialStepPreparationApproval(
            True,
            workflow.id,
            "first",
            1,
            "writer-1",
            business,
        ),
        employee=employees[0],
        resolved_tools=(),
        api_key=OpenAIApiKey(value=SecretStr("synthetic-test-only")),
        execution_approval=first_execution,
        transport=lambda _request: OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (),
            b'{"id":"first-response","object":"response","status":"completed",'
            b'"output":[{"type":"message","content":[{"type":"output_text",'
            b'"text":"source note"}]}]}',
        ),
        binding=run.binding,
        manifest_store=run.store,
    )
    first_outcome = route_approved_workflow_fresh_start(
        workflow, run.state_path, run.events_path, first_context
    )
    assert type(first_outcome) is WorkflowProgressionDecision
    assert first_outcome.decision == "prepare_next_step"

    history, state_digest, _events_digest = (
        load_workflow_execution_history_with_source_digests(
            WorkflowExecutionPersistenceTargets(
                run.state_path, run.events_path, binding=run.binding
            )
        )
    )
    validate_loaded_persisted_execution_history(
        workflow, history, require_execution_provenance=True
    )
    preparation = NextStepPreparationApproval(
        approved=False,
        workflow_id=workflow.id,
        current_step_id="first",
        current_step_index=1,
        next_step_id="second",
        next_step_index=2,
        next_employee_id="writer-2",
    )
    prepared = _prepare_approved_next_workflow_step(
        workflow,
        history,
        first_outcome,
        replace(preparation, approved=True),
        employees[1],
    )
    prepared_start = prepare_prepared_step_execution_start(
        prepared,
        history,
        state_source_sha256=state_digest,
        run_input=manifest.run_input,
    )
    second_request = prepared_start.request
    envelope = WorkflowJobAuthorizationEnvelope(
        job_id="job-continuation-one-action",
        run_id=run.binding.run_id,
        manifest_digest=manifest.digest,
        workflow_id=workflow.id,
        step_id="second",
        step_index=2,
        employee_id="writer-2",
        run_input_sha256=sha256(manifest.run_input.encode()).hexdigest(),
        provider="openai",
        execution_target_fingerprint=execution_target_fingerprint(
            DIRECT_OPENAI_EXECUTION_TARGET
        ),
        model=employees[1].model,
        request_fingerprint=build_model_invocation_execution_fingerprint(
            second_request, (), DIRECT_OPENAI_EXECUTION_TARGET
        ),
        expires_at_ns=9_999_999_999_999_999_999,
    )
    authorization = issue_workflow_job_authorization(
        run.store,
        envelope,
        request=second_request,
        resolved_tools=(),
        target=DIRECT_OPENAI_EXECUTION_TARGET,
        business_approved_by="local-operator-label",
        business_grant_id="business-second-job",
        execution_approved_by="local-operator-label",
        execution_grant_id="execution-second-job",
        now_ns=1,
    )
    calls: list[object] = []

    def continuation_transport(request_value: object) -> OpenAIResponsesRawHttpResponse:
        calls.append(request_value)
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (("x-request-id", "continuation-job-1"),),
            b'{"id":"continuation-job-1","object":"response","status":"completed",'
            b'"output":[{"type":"message","content":[{"type":"output_text",'
            b'"text":"# Continuation result\\n\\nJob authority verified."}]}]}',
        )

    result = route_approved_workflow_continuation_cycle(
        first_outcome,
        workflow,
        preparation,
        employees[1],
        run.state_path,
        run.events_path,
        (),
        OpenAIApiKey(value=SecretStr("synthetic-test-only")),
        None,
        continuation_transport,
        job_authorization=authorization,
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    assert type(result) is WorkflowProgressionDecision
    assert result.decision == "workflow_complete"
    assert len(calls) == 1
    job_attempts = [
        item
        for item in list_run_execution_evidence(run.store.root, run.binding.run_id)
        if item.authorization_source == "job_grant"
    ]
    assert len(job_attempts) == 1
    assert job_attempts[0].step_id == "second"
    assert job_attempts[0].execution_approval_id is None
    assert len(list_run_artifacts(run.store, run.binding.run_id)) == 1
