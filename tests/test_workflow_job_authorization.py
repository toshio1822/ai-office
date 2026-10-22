"""Offline proof for the bounded Job authorization vertical slice."""

from __future__ import annotations

import base64
import json
import threading
from contextlib import contextmanager
from dataclasses import asdict, replace
from hashlib import sha256
from multiprocessing import get_context
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr
from typer.testing import CliRunner

import ai_office.cli as cli_module
import ai_office.engine.workflow_bounded_job as bounded_job_module
import ai_office.engine.workflow_job_authorization as job_authorization_module
from ai_office.cli import app
from ai_office.definitions.employee import (
    EmployeeDefinition,
    LoadedEmployee,
    load_employee_file,
)
from ai_office.definitions.workflow import (
    LoadedWorkflow,
    WorkflowDefinition,
    load_workflow_file,
)
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
    build_execution_approval_evidence_for_tools,
    persist_business_approval_evidence,
    persist_execution_approval_evidence,
)
from ai_office.engine.workflow_bounded_job import (
    BoundedJobActionAuthorization,
    BoundedJobActionSlot,
    bounded_document_action_input,
    bounded_job_execution_view,
    bounded_review_action_plan,
    derive_bounded_job_action_authorization,
    issue_bounded_workflow_job_scope,
    load_bounded_workflow_job_scope,
    run_bounded_document_job,
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
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    create_workflow_run_manifest,
    load_workflow_run_manifest,
)
from ai_office.execution_destination import load_execution_destination_registry
from ai_office.execution_evidence import (
    ExecutionAttemptAlreadyClaimedError,
    ExecutionEvidenceError,
    build_execution_evidence_context,
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
from ai_office.runtime.run_binding import WorkflowRunBinding
from ai_office.storage import WorkflowExecutionPersistenceTargets
from ai_office.storage.workflow_execution_history import (
    load_workflow_execution_history_with_source_digests,
)
from tests._run_test_support import create_test_run


def _grounded_pass_for_request(request) -> str:
    """Build a structural PASS fixture grounded in the exact provider input."""
    payload = json.loads(request.body)
    task_input = (
        json.loads(payload["input"])
        if "input" in payload
        else json.loads(payload["messages"][1]["content"])
    )
    review_input = json.loads(task_input["run_input"])
    assignment_lines = review_input["assignment"].splitlines()
    assignment_line_number, assignment_line = next(
        (index, line)
        for index, line in enumerate(assignment_lines, start=1)
        if line.strip()
    )
    source = review_input.get("source_artifact") or review_input["revision_artifact"]
    artifact_quote = next(
        line.strip() for line in source["content"].splitlines() if line.strip()
    )
    return (
        "Verdict: PASS\n"
        "Unresolved material issues: none\n\n"
        "### Check 1\n"
        f"Requirement: {assignment_line}\n"
        f"Artifact location: `{artifact_quote}`\n"
        f"Evidence source/reference: assignment:L{assignment_line_number} "
        f"`{assignment_line}`\n"
        "Assessment: The exact quoted Artifact text addresses the cited requirement."
    )


def _assignment_from_model_request(request) -> str:
    """Extract the original assignment from a captured provider request."""
    payload = json.loads(request.body)
    task_input = (
        json.loads(payload["input"])
        if "input" in payload
        else json.loads(payload["messages"][1]["content"])
    )
    run_input = task_input["run_input"]
    if not isinstance(run_input, str):
        return ""
    try:
        review_input = json.loads(run_input)
    except json.JSONDecodeError:
        return run_input
    if isinstance(review_input, dict) and isinstance(
        review_input.get("assignment"), str
    ):
        return review_input["assignment"]
    return ""


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


def _competing_bounded_reservation_worker(authorization, gate, results) -> None:
    from ai_office.engine.workflow_bounded_job import reserve_bounded_job_attempt

    gate.wait()
    try:
        reserve_bounded_job_attempt(
            authorization,
            request_body_sha256="1" * 64,
            request_body_length=1,
        )
    except WorkflowJobAuthorizationError as error:
        results.put(error.classification)
    else:
        results.put("reserved")


def _competing_provider_worker(
    evidence_context,
    request,
    approval,
    start_gate,
    transport_calls,
    results,
) -> None:
    from ai_office.execution_evidence import ExecutionAttemptAlreadyClaimedError
    from ai_office.providers.openai import (
        OpenAIApiKey,
        OpenAIResponsesRawHttpResponse,
        execute_openai_model_invocation,
    )

    start_gate.wait()

    def transport(_request_value):
        with transport_calls.get_lock():
            transport_calls.value += 1
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (),
            b'{"id":"authority-race-response","object":"response",'
            b'"status":"completed","output":[{"type":"message",'
            b'"content":[{"type":"output_text","text":"offline"}]}]}',
        )

    try:
        execute_openai_model_invocation(
            request,
            (),
            OpenAIApiKey(value=SecretStr("synthetic-test-only")),
            approval,
            transport=transport,
            execution_evidence=evidence_context,
        )
    except ExecutionAttemptAlreadyClaimedError:
        results.put("claim_rejected")
    except ExecutionEvidenceError as error:
        results.put(f"evidence_error:{error.classification}")
    except BaseException as error:
        results.put(f"unexpected:{type(error).__name__}")
    else:
        results.put("transport_completed")


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
                    "model": "model",
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
        allowed_tools=[],
    )


def _snapshot_digest(snapshot: object) -> str:
    """Hash the current immutable Workflow snapshot shape."""
    value = asdict(snapshot)
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def test_legacy_bounded_scope_v2_is_rejected_without_migration(
    tmp_path: Path,
) -> None:
    job_id = "legacy-scope"
    scope_path = tmp_path / f"workflow-bounded-job.{job_id}.json"
    scope_path.write_text(
        json.dumps({"schema_version": "workflow-bounded-job-scope.v2"}),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowJobAuthorizationError):
        load_bounded_workflow_job_scope(WorkflowRunManifestStore(tmp_path), job_id)


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
        model=workflow.steps[0].model,
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
        model=workflow.steps[0].model,
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


def _additional_job_authorization(workflow, employee, run, request, job_id: str):
    manifest = load_workflow_run_manifest(run.store, run.binding.run_id)
    envelope = WorkflowJobAuthorizationEnvelope(
        job_id=job_id,
        run_id=run.binding.run_id,
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
        model=workflow.steps[0].model,
        request_fingerprint=build_model_invocation_execution_fingerprint(
            request, (), DIRECT_OPENAI_EXECUTION_TARGET
        ),
        expires_at_ns=9_999_999_999_999_999_999,
    )
    return issue_workflow_job_authorization(
        run.store,
        envelope,
        request=request,
        resolved_tools=(),
        target=DIRECT_OPENAI_EXECUTION_TARGET,
        business_approved_by="local-operator-label",
        business_grant_id=f"business-{job_id}",
        execution_approved_by="local-operator-label",
        execution_grant_id=f"execution-{job_id}",
        now_ns=1,
    )


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


def test_one_root_job_grant_authorizes_two_distinct_runs_through_shared_provider(
    tmp_path: Path,
) -> None:
    """The root grant is fixed; actions derive from durable lineage."""
    store_root = tmp_path / "bounded-job-store"
    workflow = _workflow()
    employee = _employee()
    first = create_test_run(
        store_root,
        "bounded-job-run-1",
        workflow,
        (employee,),
        with_business_approvals=False,
    )
    first.store.root.chmod(0o700)
    first_manifest = load_workflow_run_manifest(first.store, first.binding.run_id)
    workflow_snapshot_sha = _snapshot_digest(first_manifest.workflow_snapshot)
    employee_snapshot = first_manifest.employee_snapshots[0]
    employee_snapshot_sha = sha256(
        json.dumps(
            asdict(employee_snapshot),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    slot = BoundedJobActionSlot(
        ordinal=0,
        workflow_id=workflow.id,
        step_id=workflow.steps[0].id,
        step_index=1,
        employee_id=employee.id,
        workflow_snapshot_sha256=workflow_snapshot_sha,
        employee_snapshot_sha256=employee_snapshot_sha,
        provider=DIRECT_OPENAI_EXECUTION_TARGET.provider,
        execution_target_fingerprint=execution_target_fingerprint(
            DIRECT_OPENAI_EXECUTION_TARGET
        ),
        model=workflow.steps[0].model,
        input_source="assignment",
    )
    second_slot = replace(slot, ordinal=1, input_source="previous_artifact")
    third_slot = replace(slot, ordinal=2, input_source="previous_artifact")
    initial_input_sha = sha256(first_manifest.run_input.encode()).hexdigest()
    root_scope = issue_bounded_workflow_job_scope(
        first.store,
        job_id="bounded-job-two-runs",
        initial_run_id=first.binding.run_id,
        assignment_sha256=initial_input_sha,
        initial_input_sha256=initial_input_sha,
        actions=(slot, second_slot, third_slot),
        max_provider_attempts=3,
        max_revisions=0,
        expires_at_ns=9_999_999_999_999_999_999,
        business_approved_by="offline-operator-audit-label",
        business_grant_id="bounded-job-business",
        execution_approved_by="offline-operator-audit-label",
        execution_grant_id="bounded-job-execution",
        now_ns=1,
    )
    with pytest.raises(WorkflowJobAuthorizationError):
        derive_bounded_job_action_authorization(
            first.store,
            root_scope,
            ordinal=0,
            run_id=first.binding.run_id,
            request_fingerprint="f" * 64,
            now_ns=root_scope.expires_at_ns,
        )
    alternative_workflow = WorkflowDefinition.model_validate(
        {
            **workflow.model_dump(mode="python"),
            "id": "alternative-job-workflow",
            "steps": [
                {
                    **workflow.steps[0].model_dump(mode="python"),
                    "id": "alternative-step",
                }
            ],
        }
    )
    alternative_run = create_test_run(
        store_root,
        "bounded-job-alternative-run",
        alternative_workflow,
        (employee,),
        with_business_approvals=False,
    )
    with pytest.raises(WorkflowJobAuthorizationError):
        derive_bounded_job_action_authorization(
            first.store,
            root_scope,
            ordinal=0,
            run_id=alternative_run.binding.run_id,
            request_fingerprint="f" * 64,
            now_ns=2,
        )
    transport_calls: list[str] = []

    def execute_action(run, ordinal: int, predecessor=None):
        manifest = load_workflow_run_manifest(run.store, run.binding.run_id)
        request = ModelInvocationRequest(
            model=workflow.steps[0].model,
            system_instructions=employee.instructions,
            task_instructions=workflow.steps[0].instructions,
            allowed_tools=(),
            run_id=run.binding.run_id,
            manifest_digest=manifest.digest,
            run_input=manifest.run_input,
        )
        action_authorization = derive_bounded_job_action_authorization(
            run.store,
            root_scope,
            ordinal=ordinal,
            run_id=run.binding.run_id,
            request_fingerprint=build_model_invocation_execution_fingerprint(
                request, (), DIRECT_OPENAI_EXECUTION_TARGET
            ),
            now_ns=2 + ordinal,
            predecessor=predecessor,
        )
        context = ApprovedWorkflowBootstrapContext(
            preparation_approval=InitialStepPreparationApproval(
                approved=False,
                workflow_id=workflow.id,
                step_id="draft",
                step_index=1,
                employee_id=employee.id,
            ),
            employee=employee,
            resolved_tools=(),
            api_key=OpenAIApiKey(value=SecretStr("synthetic-test-only")),
            execution_approval=None,
            transport=lambda _value: (
                transport_calls.append(run.binding.run_id)
                or OpenAIResponsesRawHttpResponse(
                    200,
                    "synthetic",
                    (),
                    b'{"id":"bounded-two-run-response","object":"response",'
                    b'"status":"completed","output":[{"type":"message",'
                    b'"content":[{"type":"output_text","text":"# Durable handoff"}]}]}',
                )
            ),
            binding=run.binding,
            manifest_store=run.store,
            job_authorization=action_authorization,
            execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
        )
        outcome = route_approved_workflow_fresh_start(
            workflow, run.state_path, run.events_path, context
        )
        assert type(outcome) is WorkflowProgressionDecision
        assert outcome.decision == "workflow_complete"
        attempts = list_run_execution_evidence(run.store.root, run.binding.run_id)
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt.authorization_source == "job_action"
        assert attempt.job_id == root_scope.job_id
        assert attempt.job_action_ordinal == ordinal
        assert attempt.execution_approval_id is None
        assert (
            load_raw_response_evidence(
                run.store.root, run.binding.run_id, attempt.attempt_id
            ).status_code
            == 200
        )
        assert (
            load_normalized_result_evidence(
                run.store.root, run.binding.run_id, attempt.attempt_id
            ).category
            == "success"
        )
        return (
            request,
            action_authorization,
            attempt,
            list_run_artifacts(run.store, run.binding.run_id)[0],
        )

    _request_one, _action_one, attempt_one, artifact_one = execute_action(first, 0)
    forged_run = create_test_run(
        store_root,
        "bounded-job-forged-unrelated-run",
        workflow,
        (employee,),
        with_business_approvals=False,
    )
    forged_manifest = load_workflow_run_manifest(
        forged_run.store, forged_run.binding.run_id
    )
    forged_request = ModelInvocationRequest(
        model=workflow.steps[0].model,
        system_instructions=employee.instructions,
        task_instructions=workflow.steps[0].instructions,
        allowed_tools=(),
        run_id=forged_manifest.run_id,
        manifest_digest=forged_manifest.digest,
        run_input=forged_manifest.run_input,
    )
    forged_authorization = BoundedJobActionAuthorization(
        first.store.root,
        root_scope,
        1,
        forged_manifest.run_id,
        forged_manifest.digest,
        sha256(forged_manifest.run_input.encode()).hexdigest(),
        build_model_invocation_execution_fingerprint(
            forged_request, (), DIRECT_OPENAI_EXECUTION_TARGET
        ),
        first.binding.run_id,
        artifact_one.artifact_id,
        artifact_one.content_sha256,
        "f" * 64,
    )
    forged_context = _provider_evidence_context(
        workflow, employee, forged_run, forged_request, forged_authorization
    )
    from ai_office.providers.openai import execute_openai_model_invocation

    with pytest.raises(ExecutionEvidenceError):
        execute_openai_model_invocation(
            forged_request,
            (),
            OpenAIApiKey(value=SecretStr("synthetic-test-only")),
            None,
            transport=lambda _request: transport_calls.append("forged-run"),
            execution_evidence=forged_context,
        )
    assert transport_calls == [first.binding.run_id]
    second_run_id = "bounded-job-run-2"
    second_input = artifact_one.content.decode("utf-8")
    second_manifest = create_workflow_run_manifest(
        first.store,
        second_run_id,
        second_input,
        LoadedWorkflow(tmp_path / "workflow.yaml", workflow),
        (LoadedEmployee(tmp_path / "writer.yaml", employee),),
    )
    second_state, second_events = first.store.execution_paths(second_run_id)
    from tests._run_test_support import TestRun

    second = TestRun(
        first.store,
        type(first.binding)(second_run_id, second_manifest.digest),
        second_state,
        second_events,
    )
    with pytest.raises(WorkflowJobAuthorizationError):
        derive_bounded_job_action_authorization(
            first.store,
            root_scope,
            ordinal=1,
            run_id=second_run_id,
            request_fingerprint="e" * 64,
            predecessor=(
                first.binding.run_id,
                "f" * 64,
                artifact_one.content_sha256,
            ),
            now_ns=3,
        )
    _, action_two, attempt_two, artifact_two = execute_action(
        second,
        1,
        (
            first.binding.run_id,
            artifact_one.artifact_id,
            artifact_one.content_sha256,
        ),
    )
    assert attempt_one.attempt_id != attempt_two.attempt_id
    assert attempt_one.run_id != attempt_two.run_id
    assert transport_calls == [first.binding.run_id, second_run_id]
    assert artifact_two.run_id == second_run_id
    assert load_bounded_workflow_job_scope(first.store, root_scope.job_id) == root_scope

    third_run_id = "bounded-job-run-concurrent"
    third_manifest = create_workflow_run_manifest(
        first.store,
        third_run_id,
        artifact_two.content.decode("utf-8"),
        LoadedWorkflow(tmp_path / "workflow.yaml", workflow),
        (LoadedEmployee(tmp_path / "writer.yaml", employee),),
    )
    third_state, third_events = first.store.execution_paths(third_run_id)
    TestRun(
        first.store,
        type(first.binding)(third_run_id, third_manifest.digest),
        third_state,
        third_events,
    )
    third_request = ModelInvocationRequest(
        model=workflow.steps[0].model,
        system_instructions=employee.instructions,
        task_instructions=workflow.steps[0].instructions,
        allowed_tools=(),
        run_id=third_run_id,
        manifest_digest=third_manifest.digest,
        run_input=third_manifest.run_input,
    )
    third_authorization = derive_bounded_job_action_authorization(
        first.store,
        root_scope,
        ordinal=2,
        run_id=third_run_id,
        request_fingerprint=build_model_invocation_execution_fingerprint(
            third_request, (), DIRECT_OPENAI_EXECUTION_TARGET
        ),
        now_ns=4,
        predecessor=(
            second_run_id,
            artifact_two.artifact_id,
            artifact_two.content_sha256,
        ),
    )
    mp_context = get_context("fork")
    start = mp_context.Barrier(2)
    results = mp_context.Queue()
    workers = [
        mp_context.Process(
            target=_competing_bounded_reservation_worker,
            args=(third_authorization, start, results),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
    if any(worker.is_alive() for worker in workers):
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
    assert all(worker.exitcode == 0 for worker in workers)
    race_results = [results.get(timeout=2) for _ in workers]
    assert race_results.count("reserved") == 1
    assert transport_calls == [first.binding.run_id, second_run_id]

    from ai_office.engine.workflow_bounded_job import (
        reserve_bounded_job_attempt,
        revoke_bounded_workflow_job_scope,
    )

    with pytest.raises(WorkflowJobAuthorizationError):
        reserve_bounded_job_attempt(
            action_two,
            request_body_sha256="0" * 64,
            request_body_length=0,
        )
    revoke_bounded_workflow_job_scope(first.store, root_scope.job_id, now_ns=4)
    with pytest.raises(WorkflowJobAuthorizationError):
        derive_bounded_job_action_authorization(
            first.store,
            root_scope,
            ordinal=0,
            run_id=first.binding.run_id,
            request_fingerprint="f" * 64,
            now_ns=5,
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


@pytest.mark.parametrize("competition", ["job_job", "job_run_approval"])
def test_run_step_attempt_claim_is_single_winner_across_authorities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    competition: str,
) -> None:
    workflow, employee, run, request, first_job = _run_and_authorization(
        tmp_path / competition, f"job-run-authority-race-{competition}"
    )
    import ai_office.execution_evidence as execution_evidence_module

    first_job_context = _provider_evidence_context(
        workflow, employee, run, request, first_job
    )
    authorities = [(first_job_context, None, first_job)]
    if competition == "job_job":
        second_job = _additional_job_authorization(
            workflow,
            employee,
            run,
            request,
            f"job-{competition}-second",
        )
        authorities.append(
            (
                _provider_evidence_context(
                    workflow, employee, run, request, second_job
                ),
                None,
                second_job,
            )
        )
    else:
        approval = approve_model_invocation_execution(
            request,
            (),
            provider="openai",
            approved_by="run-operator-label",
            approval_id=f"execution-{competition}-normal",
            execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
        )
        approval_evidence = build_execution_approval_evidence_for_tools(
            request,
            (),
            approval,
            workflow_id=workflow.id,
            step_id=workflow.steps[0].id,
            step_index=1,
            employee_id=employee.id,
            target=DIRECT_OPENAI_EXECUTION_TARGET,
        )
        persist_execution_approval_evidence(run.store, approval_evidence)
        normal_context = build_execution_evidence_context(
            store_root=run.store.root,
            binding=run.binding,
            workflow_id=workflow.id,
            step_id=workflow.steps[0].id,
            step_index=1,
            employee_id=employee.id,
            request=request,
            resolved_tools=(),
            approval=approval,
            target=DIRECT_OPENAI_EXECUTION_TARGET,
        )
        authorities.append((normal_context, approval, None))

    # Force two processes to reach the same Run/Step claim boundary together.
    # The lock then orders the evidence scan + durable exclusive claim.
    original_lock = execution_evidence_module._run_step_attempt_claim_lock
    mp_context = get_context("fork")
    claim_boundary = mp_context.Barrier(2)

    @contextmanager
    def synchronized_claim(context):
        claim_boundary.wait(timeout=5)
        with original_lock(context):
            yield

    monkeypatch.setattr(
        execution_evidence_module,
        "_run_step_attempt_claim_lock",
        synchronized_claim,
    )
    start = mp_context.Barrier(2)
    calls = mp_context.Value("i", 0)
    results = mp_context.Queue()
    workers = [
        mp_context.Process(
            target=_competing_provider_worker,
            args=(context, request, approval, start, calls, results),
        )
        for context, approval, _authorization in authorities
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(worker.exitcode == 0 for worker in workers)
    outcomes = sorted(results.get(timeout=2) for _ in workers)
    assert outcomes == ["claim_rejected", "transport_completed"]
    assert calls.value == 1
    attempts = list_run_execution_evidence(run.store.root, run.binding.run_id)
    assert len(attempts) == 1
    assert attempts[0].step_id == workflow.steps[0].id
    assert attempts[0].step_index == 1
    if competition == "job_job":
        assert attempts[0].authorization_source == "job_grant"
        assert attempts[0].job_id in {
            authorization.envelope.job_id for _, _, authorization in authorities
        }
    else:
        assert attempts[0].authorization_source in {"job_grant", "run_approval"}

    # A restart/replay under the losing authority cannot create a second
    # attempt identity or reach transport, even when its own Job slot remains.
    monkeypatch.setattr(
        execution_evidence_module,
        "_run_step_attempt_claim_lock",
        original_lock,
    )
    from ai_office.providers.openai import execute_openai_model_invocation

    def forbidden_transport(_value):
        raise AssertionError("a replay must not enter provider transport")

    for replay_context, replay_approval, _authorization in authorities:
        with pytest.raises(
            (ExecutionAttemptAlreadyClaimedError, ExecutionEvidenceError)
        ):
            execute_openai_model_invocation(
                request,
                (),
                OpenAIApiKey(value=SecretStr("synthetic-test-only")),
                replay_approval,
                transport=forbidden_transport,
                execution_evidence=replay_context,
            )
    assert calls.value == 1
    assert len(list_run_execution_evidence(run.store.root, run.binding.run_id)) == 1


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
                    "model": "model",
                    "instructions": "Return a short source note.",
                    "business_approval_required": True,
                },
                {
                    "id": "second",
                    "name": "Second",
                    "employee": "writer-2",
                    "model": "model",
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
            allowed_tools=[],
        ),
        EmployeeDefinition(
            id="writer-2",
            name="Writer 2",
            role="Worker",
            instructions="Write a final artifact.",
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
        model=workflow.steps[0].model,
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
        model=workflow.steps[1].model,
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


def test_bounded_root_grant_covers_both_steps_of_existing_workflow(
    tmp_path: Path,
) -> None:
    workflow = WorkflowDefinition.model_validate(
        {
            "id": "bounded-two-step",
            "name": "Bounded two-step",
            "description": "A fixed creator workflow.",
            "steps": [
                {
                    "id": "research",
                    "name": "Research",
                    "employee": "researcher",
                    "model": "model",
                    "instructions": "Return a short source note.",
                    "business_approval_required": True,
                },
                {
                    "id": "summarize",
                    "name": "Summarize",
                    "employee": "writer",
                    "model": "model",
                    "instructions": "Write a Markdown report from the note.",
                    "business_approval_required": True,
                    "artifact_content_type": "text/markdown",
                },
            ],
        }
    )
    employees = (
        EmployeeDefinition(
            id="researcher",
            name="Researcher",
            role="Research",
            instructions="Analyze the supplied evidence.",
            allowed_tools=[],
        ),
        EmployeeDefinition(
            id="writer",
            name="Writer",
            role="Writer",
            instructions="Write the report.",
            allowed_tools=[],
        ),
    )
    run = create_test_run(
        tmp_path / "bounded-two-step",
        "bounded-two-step-run",
        workflow,
        employees,
        with_business_approvals=False,
    )
    run.store.root.chmod(0o700)
    manifest = load_workflow_run_manifest(run.store, run.binding.run_id)
    workflow_sha = _snapshot_digest(manifest.workflow_snapshot)
    slots = []
    for ordinal, step_index in enumerate((1, 2)):
        employee_snapshot = next(
            item
            for item in manifest.employee_snapshots
            if item.id == workflow.steps[step_index - 1].employee
        )
        slots.append(
            BoundedJobActionSlot(
                ordinal=ordinal,
                workflow_id=workflow.id,
                step_id=workflow.steps[step_index - 1].id,
                step_index=step_index,
                employee_id=employee_snapshot.id,
                workflow_snapshot_sha256=workflow_sha,
                employee_snapshot_sha256=sha256(
                    json.dumps(
                        asdict(employee_snapshot), sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                provider=DIRECT_OPENAI_EXECUTION_TARGET.provider,
                execution_target_fingerprint=execution_target_fingerprint(
                    DIRECT_OPENAI_EXECUTION_TARGET
                ),
                model=manifest.workflow_snapshot.steps[step_index - 1].model,
                input_source="assignment" if ordinal == 0 else "previous_step",
            )
        )
    scope = issue_bounded_workflow_job_scope(
        run.store,
        job_id="bounded-two-step-job",
        initial_run_id=run.binding.run_id,
        assignment_sha256=sha256(manifest.run_input.encode()).hexdigest(),
        initial_input_sha256=sha256(manifest.run_input.encode()).hexdigest(),
        actions=tuple(slots),
        max_provider_attempts=2,
        max_revisions=0,
        expires_at_ns=9_999_999_999_999_999_999,
        business_approved_by="offline-audit-label",
        business_grant_id="bounded-two-step-business",
        execution_approved_by="offline-audit-label",
        execution_grant_id="bounded-two-step-execution",
        now_ns=1,
    )
    calls: list[str] = []

    def transport(label: str):
        def invoke(_request):
            calls.append(label)
            body = "# Final\n\nBounded." if label == "summary" else "source note"
            response = json.dumps(
                {
                    "id": label,
                    "object": "response",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": body,
                                }
                            ],
                        }
                    ],
                }
            ).encode()
            return OpenAIResponsesRawHttpResponse(200, "synthetic", (), response)

        return invoke

    first_employee = employees[0]
    first_request = ModelInvocationRequest(
        model=workflow.steps[0].model,
        system_instructions=first_employee.instructions,
        task_instructions=workflow.steps[0].instructions,
        allowed_tools=(),
        run_id=manifest.run_id,
        manifest_digest=manifest.digest,
        run_input=manifest.run_input,
    )
    first_auth = derive_bounded_job_action_authorization(
        run.store,
        scope,
        ordinal=0,
        run_id=manifest.run_id,
        request_fingerprint=build_model_invocation_execution_fingerprint(
            first_request, (), DIRECT_OPENAI_EXECUTION_TARGET
        ),
        now_ns=2,
    )
    first_outcome = route_approved_workflow_fresh_start(
        workflow,
        run.state_path,
        run.events_path,
        ApprovedWorkflowBootstrapContext(
            preparation_approval=InitialStepPreparationApproval(
                False, workflow.id, "research", 1, first_employee.id
            ),
            employee=first_employee,
            resolved_tools=(),
            api_key=OpenAIApiKey(value=SecretStr("synthetic-only")),
            execution_approval=None,
            transport=transport("research"),
            binding=run.binding,
            manifest_store=run.store,
            job_authorization=first_auth,
            execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
        ),
    )
    assert type(first_outcome) is WorkflowProgressionDecision
    assert first_outcome.decision == "prepare_next_step"
    history, state_sha, _ = load_workflow_execution_history_with_source_digests(
        WorkflowExecutionPersistenceTargets(
            run.state_path, run.events_path, binding=run.binding
        )
    )
    validate_loaded_persisted_execution_history(
        workflow, history, require_execution_provenance=True
    )
    preparation = NextStepPreparationApproval(
        False, workflow.id, "research", 1, "summarize", 2, "writer"
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
        state_source_sha256=state_sha,
        run_input=manifest.run_input,
    )
    second_auth = derive_bounded_job_action_authorization(
        run.store,
        scope,
        ordinal=1,
        run_id=manifest.run_id,
        request_fingerprint=build_model_invocation_execution_fingerprint(
            prepared_start.request, (), DIRECT_OPENAI_EXECUTION_TARGET
        ),
        now_ns=3,
    )
    from ai_office.engine.workflow_bounded_job import _action_path
    from ai_office.execution_evidence import build_execution_evidence_context
    from ai_office.providers.openai import execute_openai_model_invocation

    second_action_path = _action_path(run.store.root, scope.job_id, 1)
    second_action_bytes = second_action_path.read_bytes()
    evidence_context = build_execution_evidence_context(
        store_root=run.store.root,
        binding=run.binding,
        workflow_id=workflow.id,
        step_id="summarize",
        step_index=2,
        employee_id="writer",
        request=prepared_start.request,
        resolved_tools=(),
        approval=None,
        target=DIRECT_OPENAI_EXECUTION_TARGET,
        job_authorization=second_auth,
    )
    for tampered_bytes in (None, b"not canonical action json"):
        if tampered_bytes is None:
            second_action_path.unlink()
        else:
            second_action_path.write_bytes(tampered_bytes)
        with pytest.raises(ExecutionEvidenceError):
            execute_openai_model_invocation(
                prepared_start.request,
                (),
                OpenAIApiKey(value=SecretStr("synthetic-test-only")),
                None,
                transport=lambda _request: calls.append("unauthorized"),
                execution_evidence=evidence_context,
            )
        assert calls == ["research"]
        second_action_path.write_bytes(second_action_bytes)
        second_action_path.chmod(0o600)
    final = route_approved_workflow_continuation_cycle(
        first_outcome,
        workflow,
        preparation,
        employees[1],
        run.state_path,
        run.events_path,
        (),
        OpenAIApiKey(value=SecretStr("synthetic-only")),
        None,
        transport("summary"),
        job_authorization=second_auth,
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    assert type(final) is WorkflowProgressionDecision
    assert final.decision == "workflow_complete"
    assert calls == ["research", "summary"]
    assert len(list_run_artifacts(run.store, run.binding.run_id)) == 1
    evidence = list_run_execution_evidence(run.store.root, run.binding.run_id)
    assert len(evidence) == 2
    assert sorted(item.job_action_ordinal for item in evidence) == [0, 1]
    assert all(item.authorization_source == "job_action" for item in evidence)


@pytest.mark.parametrize(
    (
        "first_verdict",
        "expected_calls",
        "expected_status",
        "max_revisions",
        "expected_reason",
        "final_verdict",
    ),
    [
        ("PASS", 3, "candidate", 1, None, "PASS"),
        (
            "BARE_PASS",
            3,
            "stopped",
            1,
            "document_job_nonactionable_review",
            "PASS",
        ),
        ("CHANGES_REQUIRED", 5, "reviewed", 1, None, "PASS"),
        ("MULTI_FINDING", 5, "reviewed", 1, None, "PASS"),
        (
            "INCOMPLETE",
            3,
            "stopped",
            1,
            "document_job_nonactionable_review",
            "PASS",
        ),
        (
            "CHANGES_REQUIRED",
            5,
            "stopped",
            1,
            "document_job_revision_budget_exhausted",
            "CHANGES_REQUIRED",
        ),
        (
            "JAPANESE_CHANGES_REQUIRED",
            5,
            "stopped",
            1,
            "document_job_revision_budget_exhausted",
            "JAPANESE_CHANGES_REQUIRED",
        ),
        ("BLOCKED", 3, "stopped", 1, "document_job_review_blocked", "PASS"),
        ("MALFORMED", 3, "stopped", 1, "document_job_invalid_verdict", "PASS"),
        ("NONACTIONABLE", 3, "stopped", 1, "document_job_unverified_finding", "PASS"),
    ],
)
def test_fixed_bounded_document_job_uses_artifacts_and_stops_on_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_verdict: str,
    expected_calls: int,
    expected_status: str,
    max_revisions: int,
    expected_reason: str | None,
    final_verdict: str,
) -> None:
    from ai_office.engine.workflow_bounded_job import inspect_bounded_document_job

    repository = Path(__file__).resolve().parents[1]
    target = DIRECT_OPENAI_EXECUTION_TARGET
    # Generic multi-step compatibility fixture is local to this test; none of
    # these retired definitions is selectable from the repository.
    workflow_names = (
        "research-and-summarize",
        "review-supplied-artifact",
        "revise-reviewed-artifact",
    )
    workflows: dict[str, LoadedWorkflow] = {}
    for name, steps in (
        (
            workflow_names[0],
            (
                ("research", "general-researcher"),
                ("summarize", "article-writer"),
            ),
        ),
        (workflow_names[1], (("review", "general-researcher"),)),
        (workflow_names[2], (("revise", "article-writer"),)),
    ):
        workflows[name] = LoadedWorkflow(
            source_path=Path(f"<offline-test:{name}>"),
            definition=WorkflowDefinition.model_validate(
                {
                    "id": name,
                    "name": name,
                    "description": "Offline generic compatibility fixture.",
                    "steps": [
                        {
                            "id": step_id,
                            "name": step_id,
                            "employee": employee_id,
                            "model": "model",
                            "instructions": "Use the supplied immutable input only.",
                            "business_approval_required": True,
                            "artifact_content_type": "text/markdown",
                        }
                        for step_id, employee_id in steps
                    ],
                }
            ),
        )
    employees: dict[str, LoadedEmployee] = {
        name: load_employee_file(repository / "employees" / f"{name}.yaml")
        for name in ("general-researcher", "article-writer")
    }
    store_root = tmp_path / "fixed-document-job"
    store_root.mkdir(mode=0o700, parents=True)
    store = WorkflowRunManifestStore(store_root)
    assignment = (
        "Requirements: summarize only the supplied evidence.\n"
        "Evidence reference: fixture:evidence-1\n"
        "**Evidence A — `src/example.py`, lines 1–2**\n"
        "**Evidence D — `src/boundary.py`, lines 3–4**"
    )
    initial_run_id = "bounded-document-initial"
    initial = create_workflow_run_manifest(
        store,
        initial_run_id,
        assignment,
        workflows["research-and-summarize"],
        tuple(
            employees[employee] for employee in ("general-researcher", "article-writer")
        ),
        execution_target=target,
    )
    snapshots: dict[str, object] = {initial.workflow_id: initial}
    for name in workflow_names[1:]:
        snapshots[name] = create_workflow_run_manifest(
            store,
            f"snapshot-{name}",
            assignment,
            workflows[name],
            (
                employees[
                    "general-researcher"
                    if name == "review-supplied-artifact"
                    else "article-writer"
                ],
            ),
            execution_target=target,
        )
    plan = (
        ("research-and-summarize", "research", 1, "general-researcher", "assignment"),
        (
            "research-and-summarize",
            "summarize",
            2,
            "article-writer",
            "previous_step",
        ),
        ("review-supplied-artifact", "review", 1, "general-researcher", "review_input"),
        (
            "revise-reviewed-artifact",
            "revise",
            1,
            "article-writer",
            "revision_input",
        ),
        (
            "review-supplied-artifact",
            "review",
            1,
            "general-researcher",
            "re_review_input",
        ),
    )
    slots = []
    for ordinal, (
        workflow_id,
        step_id,
        step_index,
        employee_id,
        input_source,
    ) in enumerate(plan):
        snapshot_manifest = snapshots[workflow_id]
        employee_snapshot = next(
            item
            for item in snapshot_manifest.employee_snapshots
            if item.id == employee_id
        )
        slots.append(
            BoundedJobActionSlot(
                ordinal=ordinal,
                workflow_id=workflow_id,
                step_id=step_id,
                step_index=step_index,
                employee_id=employee_id,
                workflow_snapshot_sha256=_snapshot_digest(
                    snapshot_manifest.workflow_snapshot
                ),
                employee_snapshot_sha256=sha256(
                    json.dumps(
                        asdict(employee_snapshot),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                provider=target.provider,
                execution_target_fingerprint=execution_target_fingerprint(target),
                model=snapshot_manifest.workflow_snapshot.steps[step_index - 1].model,
                input_source=input_source,
            )
        )
    assignment_sha = sha256(assignment.encode()).hexdigest()
    scope = issue_bounded_workflow_job_scope(
        store,
        job_id="fixed-bounded-document-job",
        initial_run_id=initial_run_id,
        assignment_sha256=assignment_sha,
        initial_input_sha256=assignment_sha,
        actions=tuple(slots),
        max_provider_attempts=3 + (2 * max_revisions),
        max_revisions=max_revisions,
        expires_at_ns=9_999_999_999_999_999_999,
        business_approved_by="offline-audit-label",
        business_grant_id="fixed-document-business",
        execution_approved_by="offline-audit-label",
        execution_grant_id="fixed-document-execution",
        now_ns=1,
    )
    call_labels: list[str] = []

    def fake_transport(request):
        index = len(call_labels)
        if index == 0:
            label, text = "research", "Synthetic source-supported note."
        elif index == 1:
            label, text = "summary", "# Report\n\nSynthetic report with limits."
        elif index == 2:
            label = "review-1"
            if first_verdict == "PASS":
                text = _grounded_pass_for_request(request)
            elif first_verdict == "BARE_PASS":
                text = "Verdict: PASS"
            elif first_verdict == "BLOCKED":
                text = "Verdict: BLOCKED\n\nEvidence is insufficient."
            elif first_verdict == "MALFORMED":
                text = "The report is not ready."
            elif first_verdict == "INCOMPLETE":
                text = (
                    "Verdict: CHANGES_REQUIRED\n\n"
                    "### Finding 1\n"
                    "Requirement: preserve the stated limit.\n"
                    "Artifact location: Conclusion.\n"
                    "Evidence source/reference: Evidence A\n"
                    "Impact: unsupported certainty."
                )
            elif first_verdict == "MULTI_FINDING":
                text = (
                    "Verdict: CHANGES_REQUIRED\n\n"
                    "### F1: unsupported statement\n"
                    "- **要件**: keep claims within supplied evidence.\n"
                    "- **Artifact位置**: Conclusion, paragraph 1.\n"
                    "- **証拠**: Evidence A\n"
                    "- **影響**: readers may infer an unsupported guarantee.\n"
                    "- **修正要求**: narrow the claim to the cited excerpt.\n\n"
                    "### F2: missing boundary\n"
                    "- **要件**: preserve stated unknowns.\n"
                    "- **Artifact位置**: Unknowns, item 2.\n"
                    "- **証拠**: Evidence D\n"
                    "- **影響**: the implementation boundary is overstated.\n"
                    "- **修正要求**: state only what the excerpt shows.\n\n"
                    "### F3: citation scope\n"
                    "- **要件**: cite supplied sources precisely.\n"
                    "- **Artifact位置**: Sources, final bullet.\n"
                    "- **証拠**: Evidence A\n"
                    "- **影響**: the source range cannot be audited.\n"
                    "- **修正要求**: retain the provided path and line range."
                )
            elif first_verdict == "JAPANESE_CHANGES_REQUIRED":
                text = (
                    "**確定事項**： supplied material reviewed.\n\n"
                    "判定： `CHANGES_REQUIRED`\n\n"
                    "### Finding 1\n"
                    "Requirement: preserve the stated limit.\n"
                    "Artifact location: Conclusion.\n"
                    "Evidence source/reference: fixture:evidence-1\n"
                    "Impact: unsupported certainty.\n"
                    "Requested correction: retain the limitation."
                )
            else:
                evidence_reference = (
                    "Synthetic report with limits"
                    if first_verdict == "NONACTIONABLE"
                    else "fixture:evidence-1"
                )
                text = (
                    "Verdict: CHANGES_REQUIRED\n\n"
                    "Requirement: preserve the stated limit.\n"
                    "Artifact location: Conclusion.\n"
                    f"Evidence source/reference: {evidence_reference}\n"
                    "Impact: unsupported certainty.\n"
                    "Requested correction: retain the limitation."
                )
        elif index == 3:
            label, text = (
                "revision",
                "# Revised report\n\nSynthetic report with limits.",
            )
        else:
            label = "review-2"
            if final_verdict == "JAPANESE_CHANGES_REQUIRED":
                text = (
                    "**確定事項**： revised report reviewed.\n\n"
                    "判定： `CHANGES_REQUIRED`\n\n"
                    "### Finding 1\n"
                    "Requirement: preserve the stated limit.\n"
                    "Artifact location: Conclusion.\n"
                    "Evidence source/reference: fixture:evidence-1\n"
                    "Impact: unsupported certainty.\n"
                    "Requested correction: retain the limitation."
                )
            elif final_verdict == "CHANGES_REQUIRED":
                text = (
                    "Verdict: CHANGES_REQUIRED\n\n"
                    "Requirement: preserve the stated limit.\n"
                    "Artifact location: Conclusion.\n"
                    "Evidence source/reference: fixture:evidence-1\n"
                    "Impact: unsupported certainty.\n"
                    "Requested correction: retain the limitation."
                )
            else:
                text = _grounded_pass_for_request(request)
        call_labels.append(label)
        assert request.url == target.endpoint
        raw = json.dumps(
            {
                "id": label,
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": text}],
                    }
                ],
            }
        ).encode()
        return OpenAIResponsesRawHttpResponse(200, "synthetic", (), raw)

    outcome = run_bounded_document_job(
        store,
        scope,
        initial_run_id=initial_run_id,
        assignment=assignment,
        workflows=workflows,
        employees=employees,
        api_key=OpenAIApiKey(value=SecretStr("synthetic-only")),
        transport=fake_transport,
    )
    assert outcome.status == expected_status
    assert outcome.stop_reason == expected_reason
    assert len(call_labels) == expected_calls
    assert call_labels[:2] == ["research", "summary"]
    assert call_labels[2] == "review-1"
    if final_verdict == "JAPANESE_CHANGES_REQUIRED":
        assert call_labels == [
            "research",
            "summary",
            "review-1",
            "revision",
            "review-2",
        ]
    dispatches = inspect_bounded_document_job(store, scope.job_id)["dispatch_order"]
    assert len(dispatches) == expected_calls
    for dispatch in dispatches:
        action_manifest = load_workflow_run_manifest(store, dispatch["run_id"])
        destination = action_manifest.execution_destination_for_step(1)
        assert destination.target == target
        assert destination.fingerprint == execution_target_fingerprint(target)
    final_manifest = load_workflow_run_manifest(store, outcome.final_artifact_run_id)
    assert final_manifest.execution_destination_for_step(1).target == target
    assert final_manifest.execution_destination_for_step(
        1
    ).fingerprint == execution_target_fingerprint(target)
    assert final_manifest.workflow_snapshot.steps[0].model == next(
        action.model
        for action in scope.actions
        if action.step_id == final_manifest.workflow_snapshot.steps[0].id
    )
    final_artifact = read_run_artifact(
        store, outcome.final_artifact_run_id, outcome.final_artifact_id
    )
    first_review_run_id = (
        "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + "-2"
    )
    review_manifest = load_workflow_run_manifest(store, first_review_run_id)
    review_input_value = json.loads(review_manifest.run_input)
    assert review_manifest.run_id != initial_run_id
    assert (
        final_artifact.run_id == final_manifest.run_id == outcome.final_artifact_run_id
    )
    assert review_input_value["assignment"] == assignment
    source_artifact = next(
        artifact
        for artifact in list_run_artifacts(store, initial_run_id)
        if artifact.content.decode("utf-8")
        == "# Report\n\nSynthetic report with limits."
    )
    assert review_input_value["source_artifact"] == {
        "artifact_id": source_artifact.artifact_id,
        "content": source_artifact.content.decode("utf-8"),
        "content_length": source_artifact.content_length,
        "content_sha256": source_artifact.content_sha256,
        "run_id": source_artifact.run_id,
    }

    if (
        first_verdict
        not in {
            "CHANGES_REQUIRED",
            "JAPANESE_CHANGES_REQUIRED",
            "MULTI_FINDING",
        }
        and max_revisions > 0
    ):
        # Even with unused revision quota, the public derivation/input path
        # must not create revision authority from PASS, BLOCKED, malformed, or
        # ungrounded review evidence.
        review_artifact = list_run_artifacts(store, first_review_run_id)[0]
        revision_input_value = {
            "assignment": assignment,
            "source_artifact": {
                "artifact_id": source_artifact.artifact_id,
                "content": source_artifact.content.decode("utf-8"),
                "content_length": source_artifact.content_length,
                "content_sha256": source_artifact.content_sha256,
                "run_id": source_artifact.run_id,
            },
            "review_artifacts": [
                {
                    "artifact_id": review_artifact.artifact_id,
                    "content": review_artifact.content.decode("utf-8"),
                    "content_length": review_artifact.content_length,
                    "content_sha256": review_artifact.content_sha256,
                    "run_id": review_artifact.run_id,
                }
            ],
        }
        revision_input = json.dumps(
            revision_input_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        revision_run_id = (
            "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + "-3"
        )
        revision_manifest = create_workflow_run_manifest(
            store,
            revision_run_id,
            revision_input,
            workflows["revise-reviewed-artifact"],
            (employees["article-writer"],),
        )
        # The revision's predecessor is the Artifact it revises (the latest
        # producer), not the review action; the engine derives the round's
        # reviewer outcomes itself.
        predecessor = (
            source_artifact.run_id,
            source_artifact.artifact_id,
            source_artifact.content_sha256,
        )
        with pytest.raises(WorkflowJobAuthorizationError):
            bounded_document_action_input(store, scope.job_id, 3, predecessor)
        revision_employee = employees["article-writer"].definition
        revision_step = workflows["revise-reviewed-artifact"].definition.steps[0]
        revision_request = ModelInvocationRequest(
            model=revision_step.model,
            system_instructions=revision_employee.instructions,
            task_instructions=revision_step.instructions,
            allowed_tools=(),
            run_id=revision_run_id,
            manifest_digest=revision_manifest.digest,
            run_input=revision_manifest.run_input,
        )
        with pytest.raises(WorkflowJobAuthorizationError):
            derive_bounded_job_action_authorization(
                store,
                scope,
                ordinal=3,
                run_id=revision_run_id,
                request_fingerprint=build_model_invocation_execution_fingerprint(
                    revision_request, (), target
                ),
                predecessor=predecessor,
            )
        assert len(call_labels) == expected_calls

        if first_verdict == "PASS":
            # Exercise the shared provider route as if a corrupted/legacy
            # caller had persisted an otherwise well-bound action. Provider
            # admission must reload the authentic review, not trust the
            # caller-held object or unused quota.
            original_verdict = bounded_job_module._review_verdict
            monkeypatch.setattr(
                bounded_job_module,
                "_review_verdict",
                lambda _content, *, review_input=None: "CHANGES_REQUIRED",
            )
            forged_action = derive_bounded_job_action_authorization(
                store,
                scope,
                ordinal=3,
                run_id=revision_run_id,
                request_fingerprint=build_model_invocation_execution_fingerprint(
                    revision_request, (), target
                ),
                predecessor=predecessor,
            )
            monkeypatch.setattr(bounded_job_module, "_review_verdict", original_verdict)
            state_path, events_path = store.execution_paths(revision_run_id)
            with pytest.raises(FreshWorkflowBootstrapCompatibilityError):
                route_approved_workflow_fresh_start(
                    workflows["revise-reviewed-artifact"].definition,
                    state_path,
                    events_path,
                    ApprovedWorkflowBootstrapContext(
                        preparation_approval=InitialStepPreparationApproval(
                            False,
                            "revise-reviewed-artifact",
                            "revise",
                            1,
                            revision_employee.id,
                        ),
                        employee=revision_employee,
                        resolved_tools=(),
                        api_key=OpenAIApiKey(value=SecretStr("synthetic-only")),
                        execution_approval=None,
                        transport=fake_transport,
                        execution_target=target,
                        binding=WorkflowRunBinding(
                            revision_run_id, revision_manifest.digest
                        ),
                        manifest_store=store,
                        job_authorization=forged_action,
                    ),
                )
            assert len(call_labels) == expected_calls
            bounded_job_module._action_path(store.root, scope.job_id, 3).unlink()
        assert final_manifest.run_id == outcome.final_artifact_run_id
    if expected_status == "reviewed":
        assert call_labels[3:] == ["revision", "review-2"]
        assert outcome.revisions_used == 1
        assert outcome.review_artifact_run_id == (
            "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + "-4"
        )
        assert outcome.review_artifact_run_id != first_review_run_id
        revision_input_value = json.loads(
            load_workflow_run_manifest(
                store,
                "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + "-3",
            ).run_input
        )
        first_review_artifact = list_run_artifacts(store, review_manifest.run_id)[0]
        assert revision_input_value["review_artifacts"][0]["artifact_id"] == (
            first_review_artifact.artifact_id
        )
        final_review_input = json.loads(
            load_workflow_run_manifest(
                store,
                "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + "-4",
            ).run_input
        )
        assert final_review_input["source_artifact"]["artifact_id"] == (
            outcome.final_artifact_id
        )
    else:
        assert outcome.revisions_used == (1 if expected_calls == 5 else 0)
        assert outcome.review_artifact_run_id is not None
    from ai_office.engine.workflow_bounded_job import inspect_bounded_document_job

    inspection = inspect_bounded_document_job(store, scope.job_id)
    assert inspection["assignment"] == assignment
    assert inspection["consumed_provider_attempts"] == expected_calls
    assert inspection["remaining_provider_attempts"] == (
        scope.max_provider_attempts - expected_calls
    )
    assert inspection["status"] == expected_status
    assert inspection["revisions_used"] == outcome.revisions_used
    assert inspection["stop_reason"] == expected_reason
    assert len(inspection["dispatch_order"]) == expected_calls
    if final_verdict == "JAPANESE_CHANGES_REQUIRED":
        assert outcome.revisions_used == 1
        assert outcome.final_artifact_id is not None
        assert outcome.review_artifact_id is not None
        persisted_final = read_run_artifact(
            store, outcome.final_artifact_run_id, outcome.final_artifact_id
        )
        persisted_review = read_run_artifact(
            store, outcome.review_artifact_run_id, outcome.review_artifact_id
        )
        assert persisted_review.content.decode().splitlines()[2] == (
            "判定： `CHANGES_REQUIRED`"
        )
        assert persisted_review.content_sha256
        assert persisted_final.artifact_id == outcome.final_artifact_id
    replay = run_bounded_document_job(
        store,
        scope,
        initial_run_id=initial_run_id,
        assignment=assignment,
        workflows=workflows,
        employees=employees,
        api_key=OpenAIApiKey(value=SecretStr("synthetic-only")),
        transport=fake_transport,
    )
    assert replay == outcome
    assert len(call_labels) == expected_calls
    before_cli = sorted(path.name for path in store.root.iterdir())
    cli_result = CliRunner().invoke(
        app,
        [
            "workflows",
            "bounded-job",
            scope.job_id,
            "--run-store",
            str(store.root),
        ],
    )
    assert cli_result.exit_code == 0, cli_result.stderr
    cli_value = json.loads(cli_result.stdout)
    assert cli_value["operation"] == "bounded-job"
    assert cli_value["assignment"] == assignment
    assert cli_value["status"] == expected_status
    assert cli_value["stop_reason"] == expected_reason
    assert cli_value["consumed_provider_attempts"] == expected_calls
    assert sorted(path.name for path in store.root.iterdir()) == before_cli
    assert (
        sum(
            len(list_run_execution_evidence(store.root, run_id))
            for run_id in (
                initial_run_id,
                "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + "-2",
                "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + "-3",
                "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + "-4",
            )
            if store.manifest_path(run_id).exists()
        )
        == expected_calls
    )
    if expected_status in {"candidate", "reviewed"}:
        from ai_office.engine.artifact import workflow_artifact_path

        # Delete the final report in the no-revision case; corrupt the final
        # independent review Artifact after a revision. Each public read path
        # must reject the damaged terminal proof without calling transport.
        if expected_status == "candidate":
            damaged_result = (outcome.final_artifact_run_id, outcome.final_artifact_id)
            damaged_bytes = None
        else:
            damaged_result = (
                outcome.review_artifact_run_id,
                outcome.review_artifact_id,
            )
            damaged_bytes = b"{}"
        result_path = workflow_artifact_path(store, *damaged_result)
        durable_artifact = result_path.read_bytes()
        if damaged_bytes is None:
            result_path.unlink()
        else:
            result_path.write_bytes(damaged_bytes)
        with pytest.raises(WorkflowJobAuthorizationError):
            inspect_bounded_document_job(store, scope.job_id)
        with pytest.raises(WorkflowJobAuthorizationError):
            run_bounded_document_job(
                store,
                scope,
                initial_run_id=initial_run_id,
                assignment=assignment,
                workflows=workflows,
                employees=employees,
                api_key=OpenAIApiKey(value=SecretStr("synthetic-only")),
                transport=fake_transport,
            )
        assert len(call_labels) == expected_calls
        result_path.write_bytes(durable_artifact)


@pytest.mark.parametrize("revision_count", [0, 1, 2, 3])
def test_stopped_outcome_counts_distinct_revision_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision_count: int
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        bounded_job_module, "_artifact_for_action", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        bounded_job_module,
        "_reservation_records",
        lambda *_args: [
            {"ordinal": ordinal} for ordinal in range(2, 2 + (2 * revision_count), 2)
        ],
    )
    scope = SimpleNamespace(
        job_id="revision-count-job",
        actions=tuple(
            SimpleNamespace(
                input_source=(
                    "revision_input" if ordinal in {2, 4, 6} else "review_input"
                )
            )
            for ordinal in range(7)
        ),
    )

    outcome = bounded_job_module._stopped_document_job_outcome(
        SimpleNamespace(root=tmp_path), scope, "document_job_revision_budget_exhausted"
    )

    assert outcome.revisions_used == revision_count


def test_changes_required_needs_exact_original_assignment_evidence_reference() -> None:
    from ai_office.engine.workflow_bounded_job import _review_verdict

    review_input = json.dumps(
        {
            "assignment": "Requirements: assess the supplied report.\n"
            "Evidence reference: source:AC2#p4\n"
            "**Evidence A — `src/report.py`, lines 4–8**\n"
            "**Evidence D — `src/dispatch.py`, lines 10–12**",
            "source_artifact": {
                "content": "The report mentions a weak unverified phrase."
            },
        }
    )
    review = (
        "Verdict: CHANGES_REQUIRED\n\n"
        "Requirement: use the cited evidence.\n"
        "Artifact location: Conclusion.\n"
        "Evidence source/reference: source:AC2#p4\n"
        "Impact: unsupported statement.\n"
        "Requested correction: remove unsupported wording."
    )
    assert _review_verdict(review, review_input=review_input) == "CHANGES_REQUIRED"
    package_reference_review = review.replace(
        "source:AC2#p4",
        "Evidence A / Evidence B / Evidence C / Evidence D (the only evidence package)",
    )
    package_review_input = json.loads(review_input)
    package_review_input["assignment"] += (
        "\n**Evidence B — `src/review.py`, lines 1–3**"
        "\n**Evidence C — `src/manifest.py`, lines 1–3**"
    )
    assert (
        _review_verdict(
            package_reference_review,
            review_input=json.dumps(package_review_input),
        )
        == "CHANGES_REQUIRED"
    )
    assert (
        _review_verdict(
            review.replace("source:AC2#p4", "Evidence A"), review_input=review_input
        )
        == "CHANGES_REQUIRED"
    )
    incidental_assignment = json.loads(review_input)
    incidental_assignment["assignment"] += (
        "\nWe lack sources and requirements. The token sourcea is prose, not an ID."
    )
    for unsupported_reference in (
        "weak unverified phrase",
        "assignment",
        "source:AC2",
        "Evidence B",
        "Evidence S",
        "Criterion A",
        "Source A",
    ):
        unsupported_review = review.replace("source:AC2#p4", unsupported_reference)
        with pytest.raises(WorkflowJobAuthorizationError) as caught:
            _review_verdict(
                unsupported_review,
                review_input=json.dumps(incidental_assignment),
            )
        assert caught.value.classification == "document_job_unverified_finding"


def test_note_findings_can_cite_verbatim_lines_of_unchanged_assignment() -> None:
    from ai_office.engine.workflow_bounded_job import _review_verdict

    assignment = (
        "タイトル案: 複雑な仕事を抱え込まないために\n"
        "ToDoリストを否定せず、不明点を確認可能な問いに変える。\n"
        "著者が未提示の実体験や数字を捏造しない。"
    )
    artifact = (
        "# 複雑な仕事を整理する\n\n"
        "ToDoリストは役に立たない。\n"
        "私は顧客の重大障害を必ず解決してきた。"
    )
    artifact_bytes = artifact.encode("utf-8")
    review_input = json.dumps(
        {
            "assignment": assignment,
            "source_artifact": {
                "artifact_id": "note-draft-1",
                "content": artifact,
                "content_length": len(artifact_bytes),
                "content_sha256": sha256(artifact_bytes).hexdigest(),
            },
        },
        ensure_ascii=False,
    )
    review = (
        "Verdict: CHANGES_REQUIRED\n\n"
        "### Finding 1\n"
        "Requirement: Do not reject ToDo lists.\n"
        "Artifact location: `ToDoリストは役に立たない。`\n"
        "Evidence source/reference: "
        "assignment:L2 `ToDoリストを否定せず、不明点を確認可能な問いに変える。`\n"
        "Impact: the statement contradicts the supplied brief.\n"
        "Requested correction: explain the different roles of ToDo and unknowns lists."
        "\n\n"
        "### Finding 2\n"
        "Requirement: Do not invent personal experiences or numbers.\n"
        "Artifact location: `重大障害を必ず解決してきた`\n"
        "Evidence source/reference: "
        "assignment:L3 `著者が未提示の実体験や数字を捏造しない。`\n"
        "Impact: the draft presents an unsupported experience as fact.\n"
        "Requested correction: remove the claim or mark it for author confirmation."
    )

    assert _review_verdict(review, review_input=review_input) == "CHANGES_REQUIRED"
    assert json.loads(review_input)["assignment"] == assignment
    for invalid_reference in (
        "assignment:L0 `ToDoリストを否定せず、不明点を確認可能な問いに変える。`",
        "assignment:L3 `ToDoリストを否定せず、不明点を確認可能な問いに変える。`",
        "assignment:L2 `not present in the cited assignment line`",
        "assignment:L999 `Artifact: "
        "ToDoリストを否定せず、不明点を確認可能な問いに変える。`",
    ):
        invalid_review = review.replace(
            "assignment:L2 `ToDoリストを否定せず、不明点を確認可能な問いに変える。`",
            invalid_reference,
            1,
        )
        with pytest.raises(WorkflowJobAuthorizationError) as caught:
            _review_verdict(invalid_review, review_input=review_input)
        assert caught.value.classification == "document_job_unverified_finding"

    declared_evidence_input = json.loads(review_input)
    declared_evidence_input["assignment"] += "\nEvidence A: author-provided brief"
    invalid_id_fallback = review.replace(
        "assignment:L2 `ToDoリストを否定せず、不明点を確認可能な問いに変える。`",
        "Evidence A; assignment:L999 `ToDoリストを否定せず、"
        "不明点を確認可能な問いに変える。`",
        1,
    )
    with pytest.raises(WorkflowJobAuthorizationError) as caught:
        _review_verdict(
            invalid_id_fallback,
            review_input=json.dumps(declared_evidence_input, ensure_ascii=False),
        )
    assert caught.value.classification == "document_job_unverified_finding"


def test_issue_772_personal_history_findings_and_supported_first_person_stay_distinct():
    from ai_office.engine.workflow_bounded_job import _review_verdict

    assignment = (
        "著者の一人称で書く。\n"
        "私は複雑な仕事に取りかかるとき、まず「不明点リスト」を作る。\n"
        "著者が未提示の実体験・過去の経験・個人的な習慣などを、"
        "与えられていないのに実体験として捏造しない。"
    )
    unsupported_claims = (
        "私も以前はそうだった。",
        "私は「技術」「仕様」「人」「リスク」の4つに分けることが多い。",
        "私はすべての不明点を一度に潰そうとはしない。",
    )
    artifact = "\n".join(unsupported_claims)
    artifact_bytes = artifact.encode("utf-8")
    review_input = json.dumps(
        {
            "assignment": assignment,
            "source_artifact": {
                "artifact_id": "issue-772-fixture",
                "content": artifact,
                "content_length": len(artifact_bytes),
                "content_sha256": sha256(artifact_bytes).hexdigest(),
            },
        },
        ensure_ascii=False,
    )
    findings = [
        f"### Finding {index}\n"
        "Requirement: Do not invent unsupplied personal experiences or habits.\n"
        f"Artifact location: `{claim}`\n"
        "Evidence source/reference: assignment:L3 `"
        "著者が未提示の実体験・過去の経験・個人的な習慣などを、"
        "与えられていないのに実体験として捏造しない。`\n"
        "Impact: This states unsupported author history as fact.\n"
        "Requested correction: Remove or generalize the claim, or move it to an "
        "author-confirmation note outside the article."
        for index, claim in enumerate(unsupported_claims, start=1)
    ]
    review = "Verdict: CHANGES_REQUIRED\n\n" + "\n\n".join(findings)

    assert _review_verdict(review, review_input=review_input) == "CHANGES_REQUIRED"

    supported_artifact = "私は複雑な仕事に取りかかるとき、まず「不明点リスト」を作る。"
    supported_bytes = supported_artifact.encode("utf-8")
    supported_input = json.dumps(
        {
            "assignment": assignment,
            "source_artifact": {
                "artifact_id": "issue-772-supported-fixture",
                "content": supported_artifact,
                "content_length": len(supported_bytes),
                "content_sha256": sha256(supported_bytes).hexdigest(),
            },
        },
        ensure_ascii=False,
    )
    supported_pass = (
        "Verdict: PASS\n"
        "Unresolved material issues: none\n\n"
        "### Check 1\n"
        "Requirement: Use the supplied author voice and supported fact.\n"
        f"Artifact location: `{supported_artifact}`\n"
        "Evidence source/reference: assignment:L2 `"
        "私は複雑な仕事に取りかかるとき、まず「不明点リスト」を作る。`\n"
        "Assessment: This first-person claim is explicitly supported by the brief."
    )
    assert _review_verdict(supported_pass, review_input=supported_input) == "PASS"


@pytest.mark.parametrize(
    ("reviewer_count", "case"),
    [(1, "pass"), (1, "revise"), (3, "revise"), (5, "revise"), (3, "blocked")],
)
def test_configured_bounded_job_reviewers_execute_and_audit_offline(
    tmp_path: Path, reviewer_count: int, case: str
) -> None:
    from ai_office.definitions.style_profile import (
        add_style_profile_to_assignment,
        load_style_profile,
    )
    from ai_office.engine.workflow_bounded_job import inspect_bounded_document_job

    repository = Path(__file__).resolve().parents[1]
    base = load_workflow_file(repository / "workflows/article-production.yaml")
    raw_workflow = base.definition.model_dump(mode="python")
    review_ids = ["review-quality", "review-evidence"][:reviewer_count]
    employees = {
        employee_id: load_employee_file(
            repository / "employees" / f"{employee_id}.yaml"
        )
        for employee_id in ("article-writer", "document-reviewer", "evidence-auditor")
    }
    raw_steps = raw_workflow["steps"]
    if reviewer_count == 1:
        raw_steps[:] = [
            step
            for step in raw_steps
            if step["id"] in {"write-draft", "review-quality", "revise"}
        ]
    else:
        reviewer_template = next(
            step for step in raw_steps if step["id"] == "review-evidence"
        )
        for index in range(3, reviewer_count + 1):
            reviewer_id = f"review-evidence-{index}"
            employee_id = f"evidence-auditor-{index}"
            reviewer_step = dict(reviewer_template)
            reviewer_step.update(
                id=reviewer_id, name=f"Evidence Audit {index}", employee=employee_id
            )
            raw_steps.insert(-1, reviewer_step)
            definition = employees["evidence-auditor"].definition.model_copy(
                update={"id": employee_id, "name": f"Evidence Auditor {index}"}
            )
            employees[employee_id] = LoadedEmployee(
                Path(f"<offline-test:{employee_id}>"), definition
            )
            review_ids.append(reviewer_id)
    raw_workflow["bounded_review"]["review_steps"] = review_ids
    workflow = LoadedWorkflow(
        base.source_path, WorkflowDefinition.model_validate(raw_workflow)
    )
    workflows = {workflow.definition.id: workflow}
    assignment = (
        "**タイトル案**：複雑な仕事を抱え込まないために、20年SEの私がやっている“外に出す”整理術\n"
        "**サブタイトル案**：ToDoリストではなく、不明点リストから始める\n"
        "複雑な仕事を前にしたとき、著者は、いきなりToDoリストを作るより、まず「分からないこと」を書き出す。\n"
        "不明点リストは仕事を減らす魔法ではない。何を誰に確認するか、何を試すか、どこから着手するかを見えるようにする。\n"
        "**「ToDoリストは仕事の量を見せるもの。不明点リストは仕事を前に進めるための足場を作るもの。」**\n"
        "対象読者：複雑な仕事で疲れやすい人、抱え込みがちな人、着手点が見えない人。\n"
        "1. 複雑な仕事がしんどくなる理由\n"
        "2. ToDoリストだけでは疲れてしまう理由\n"
        "3. 不明点リストを作ると何が変わるか\n"
        "4. 不明点を「技術・仕様・人・リスク」に分類する考え方\n"
        "5. 不明点を潰す順番\n"
        "6. 不安をタスクではなく確認可能な問いに変える方法\n"
        "7. 一人で抱え込まないための考え方\n"
        "ToDoそのものを否定せず、両者の役割の違いを説明する。\n"
        "著者が実際に経験した障害・顧客・発言・数字・成果・事件などを、与えられていないのに実体験として捏造しない。"
    )
    style_profile_file = None
    if reviewer_count == 1 and case == "revise":
        style_profile_directory = tmp_path / "style_profiles"
        style_profile_directory.mkdir()
        style_profile_file = style_profile_directory / "toshio-web.yaml"
        style_profile_file.write_bytes(
            (repository / "style_profiles/toshio-web.yaml").read_bytes()
        )
        style_profile = load_style_profile("toshio-web", style_profile_directory)
        assignment = add_style_profile_to_assignment(assignment, style_profile)
    store_root = tmp_path / "note-job"
    store_root.mkdir(mode=0o700)
    store = WorkflowRunManifestStore(store_root)
    target = DIRECT_OPENAI_EXECUTION_TARGET
    initial_run_id = f"note-{reviewer_count}-{case}-initial"

    def digest(value: object) -> str:
        return _snapshot_digest(value)

    source_manifest = create_workflow_run_manifest(
        store,
        "note-source-definition",
        assignment,
        workflow,
        tuple(employees.values()),
        execution_target=target,
    )
    initial_view = bounded_job_execution_view(workflow, "write-draft")
    initial_manifest = create_workflow_run_manifest(
        store,
        initial_run_id,
        assignment,
        initial_view,
        (employees["article-writer"],),
        execution_target=target,
    )
    slots = []
    for ordinal, (step_id, step_index, input_source) in enumerate(
        bounded_review_action_plan(workflow, 1)
    ):
        view = bounded_job_execution_view(workflow, step_id)
        snapshot = (
            initial_manifest
            if ordinal == 0
            else create_workflow_run_manifest(
                store,
                f"note-template-{ordinal}",
                assignment,
                view,
                (employees[view.definition.steps[0].employee],),
                execution_target=target,
            )
        )
        employee_id = view.definition.steps[step_index - 1].employee
        employee_snapshot = next(
            item for item in snapshot.employee_snapshots if item.id == employee_id
        )
        slots.append(
            BoundedJobActionSlot(
                ordinal=ordinal,
                workflow_id=workflow.definition.id,
                step_id=step_id,
                step_index=step_index,
                employee_id=employee_id,
                workflow_snapshot_sha256=digest(snapshot.workflow_snapshot),
                employee_snapshot_sha256=digest(employee_snapshot),
                provider=target.provider,
                execution_target_fingerprint=execution_target_fingerprint(target),
                model=snapshot.workflow_snapshot.steps[step_index - 1].model,
                input_source=input_source,
            )
        )
    assignment_sha = sha256(assignment.encode()).hexdigest()
    scope = issue_bounded_workflow_job_scope(
        store,
        job_id=f"note-{reviewer_count}-{case}",
        initial_run_id=initial_run_id,
        assignment_sha256=assignment_sha,
        initial_input_sha256=assignment_sha,
        actions=tuple(slots),
        max_provider_attempts=2 * (reviewer_count + 1),
        max_revisions=1,
        source_manifest_run_id=source_manifest.run_id,
        source_manifest_digest=source_manifest.digest,
        expires_at_ns=9_999_999_999_999_999_999,
        business_approved_by="offline-test",
        business_grant_id=f"note-business-{reviewer_count}-{case}",
        execution_approved_by="offline-test",
        execution_grant_id=f"note-execution-{reviewer_count}-{case}",
        now_ns=1,
    )
    if style_profile_file is not None:
        style_profile_file.unlink()
    requirement_lines = (
        "ToDoそのものを否定せず、両者の役割の違いを説明する。",
        "著者が実際に経験した障害・顧客・発言・数字・成果・事件などを、与えられていないのに実体験として捏造しない。",
    )
    refs = tuple(
        f"assignment:L{assignment.splitlines().index(line) + 1} `{line}`"
        for line in requirement_lines
    )
    draft = "# 整理術\n\nToDoリストは不要だ。\n私は顧客の重大障害を必ず解決してきた。"
    revised = (
        "# 整理術\n\nToDoリストは作業量、不明点リストは確認事項を見える化する。\n"
        "具体例は仮想例とし、著者の未確認経験は記載しない。"
    )
    findings = (
        "### Finding 1\n"
        "Requirement: ToDo自体を否定しない。\n"
        "Artifact location: `ToDoリストは不要だ。`\n"
        f"Evidence source/reference: {refs[0]}\n"
        "Impact: briefと逆の主張になる。\n"
        "Requested correction: 両者の役割の違いを説明する。\n\n"
        "### Finding 2\n"
        "Requirement: 未提示の経験を捏造しない。\n"
        "Artifact location: `顧客の重大障害を必ず解決してきた`\n"
        f"Evidence source/reference: {refs[1]}\n"
        "Impact: 未確認の経験を事実として提示する。\n"
        "Requested correction: 削除するか著者確認事項とする。"
    )
    labels = []
    request_assignments = []

    def fake_transport(request):
        index = len(labels)
        labels.append("transport-call")
        request_assignments.append(_assignment_from_model_request(request))
        if index == 0:
            response = draft
        elif case == "blocked" and index == 2:
            response = "Verdict: BLOCKED\n\nEvidence cannot be assessed."
        elif case in {"revise", "blocked"} and index == 1:
            response = "Verdict: CHANGES_REQUIRED\n\n" + findings
        elif case == "revise" and index == reviewer_count + 1:
            response = revised
        else:
            response = _grounded_pass_for_request(request)
        body = json.dumps(
            {
                "id": f"offline-note-{len(labels)}",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": response}],
                    }
                ],
            }
        ).encode()
        return OpenAIResponsesRawHttpResponse(200, "synthetic", (), body)

    outcome = run_bounded_document_job(
        store,
        scope,
        initial_run_id=initial_run_id,
        assignment=assignment,
        workflows=workflows,
        employees=employees,
        api_key=OpenAIApiKey(value=SecretStr("offline-only")),
        transport=fake_transport,
    )
    assert request_assignments == [assignment] * len(request_assignments)
    inspection = inspect_bounded_document_job(store, scope.job_id)
    expected_status = {"pass": "candidate", "revise": "reviewed", "blocked": "stopped"}[
        case
    ]
    expected_calls = {
        "pass": reviewer_count + 1,
        "revise": 2 * (reviewer_count + 1),
        "blocked": 3,
    }[case]
    assert outcome.status == expected_status, outcome
    assert outcome.revisions_used == (1 if case == "revise" else 0)
    assert len(labels) == inspection["consumed_provider_attempts"] == expected_calls
    assert inspection["status"] == expected_status
    assert inspection["max_provider_attempts"] == 2 * (reviewer_count + 1)
    assert inspection["reviewer_count"] == reviewer_count
    assert (
        inspection["remaining_provider_attempts"]
        == 2 * (reviewer_count + 1) - expected_calls
    )
    assert inspection["stop_reason"] == (
        "document_job_review_blocked" if case == "blocked" else None
    )
    initial_manifest = load_workflow_run_manifest(store, initial_run_id)
    assert initial_manifest.run_input == assignment
    run_prefix = "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + "-"
    # Both reviewers in a round read the same immutable version, and the second
    # round is a full re-review of the new Artifact produced by the revision.
    source_artifact = list_run_artifacts(store, initial_run_id)[0]
    first_round = min(reviewer_count, 2) if case == "blocked" else reviewer_count
    first_round_inputs = []
    for offset in range(first_round):
        run_input = json.loads(
            load_workflow_run_manifest(store, run_prefix + str(1 + offset)).run_input
        )
        first_round_inputs.append(run_input)
        assert run_input["assignment"] == assignment
        assert (
            run_input["source_artifact"]["artifact_id"] == source_artifact.artifact_id
        )
        assert (
            run_input["source_artifact"]["content_sha256"]
            == source_artifact.content_sha256
        )
    assert (
        len({item["source_artifact"]["content_sha256"] for item in first_round_inputs})
        == 1
    )
    if case == "revise":
        revision_ordinal = 1 + reviewer_count
        revision_input = json.loads(
            load_workflow_run_manifest(
                store, run_prefix + str(revision_ordinal)
            ).run_input
        )
        assert revision_input["assignment"] == assignment
        assert len(revision_input["review_artifacts"]) == reviewer_count
        assert "CHANGES_REQUIRED" in revision_input["review_artifacts"][0]["content"]
        assert all(
            "Verdict: PASS" in item["content"]
            for item in revision_input["review_artifacts"][1:]
        )
        re_review_inputs = [
            json.loads(
                load_workflow_run_manifest(
                    store, run_prefix + str(revision_ordinal + 1 + offset)
                ).run_input
            )
            for offset in range(reviewer_count)
        ]
        assert all(item["assignment"] == assignment for item in re_review_inputs)
        assert (
            len({item["source_artifact"]["artifact_id"] for item in re_review_inputs})
            == 1
        )
        assert all(
            item["source_artifact"]["artifact_id"] == outcome.final_artifact_id
            for item in re_review_inputs
        )
        final_artifact = read_run_artifact(
            store, outcome.final_artifact_run_id, outcome.final_artifact_id
        )
        assert "ToDoリストは作業量" in final_artifact.content.decode()
    elif case == "blocked":
        predecessor = (
            source_artifact.run_id,
            source_artifact.artifact_id,
            source_artifact.content_sha256,
        )
        revision_ordinal = 1 + reviewer_count
        with pytest.raises(WorkflowJobAuthorizationError) as input_error:
            bounded_document_action_input(
                store, scope.job_id, revision_ordinal, predecessor
            )
        assert input_error.value.classification == "document_job_review_blocked"
        review_payloads = []
        for offset in range(2):
            artifact = list_run_artifacts(store, run_prefix + str(1 + offset))[0]
            review_payloads.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "content": artifact.content.decode(),
                    "content_length": artifact.content_length,
                    "content_sha256": artifact.content_sha256,
                    "run_id": artifact.run_id,
                }
            )
        forged_input = json.dumps(
            {
                "assignment": assignment,
                "source_artifact": {
                    "artifact_id": source_artifact.artifact_id,
                    "content": source_artifact.content.decode(),
                    "content_length": source_artifact.content_length,
                    "content_sha256": source_artifact.content_sha256,
                    "run_id": source_artifact.run_id,
                },
                "review_artifacts": review_payloads,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        revision_view = bounded_job_execution_view(workflow, "revise")
        revision_run_id = run_prefix + str(revision_ordinal)
        revision_manifest = create_workflow_run_manifest(
            store,
            revision_run_id,
            forged_input,
            revision_view,
            (employees["article-writer"],),
            execution_target=target,
        )
        employee = employees["article-writer"].definition
        request = ModelInvocationRequest(
            model=revision_view.definition.steps[0].model,
            system_instructions=employee.instructions,
            task_instructions=revision_view.definition.steps[0].instructions,
            allowed_tools=(),
            run_id=revision_run_id,
            manifest_digest=revision_manifest.digest,
            run_input=forged_input,
        )
        with pytest.raises(WorkflowJobAuthorizationError) as authorization_error:
            derive_bounded_job_action_authorization(
                store,
                scope,
                ordinal=revision_ordinal,
                run_id=revision_run_id,
                request_fingerprint=build_model_invocation_execution_fingerprint(
                    request, (), target
                ),
                predecessor=predecessor,
            )
        assert authorization_error.value.classification == "document_job_review_blocked"
        assert len(labels) == inspection["consumed_provider_attempts"] == 3
    evidence = [
        item
        for dispatch in inspection["dispatch_order"]
        for item in list_run_execution_evidence(store.root, dispatch["run_id"])
    ]
    assert len(evidence) == expected_calls
    assert all(item.authorization_source == "job_action" for item in evidence)


def test_review_verdict_requires_one_unambiguous_declaration() -> None:
    from ai_office.engine.workflow_bounded_job import _review_verdict

    assignment = "Material requirement: preserve the supplied boundary."
    source_content = "# Result\n\nThe supplied boundary is preserved."
    source_bytes = source_content.encode()
    review_input = json.dumps(
        {
            "assignment": assignment,
            "source_artifact": {
                "content": source_content,
                "content_length": len(source_bytes),
                "content_sha256": sha256(source_bytes).hexdigest(),
            },
        }
    )
    grounded_pass = (
        "Verdict: PASS\n"
        "Unresolved material issues: none\n\n"
        "### Check 1\n"
        "Requirement: preserve the supplied boundary.\n"
        "Artifact location: `The supplied boundary is preserved.`\n"
        "Evidence source/reference: assignment:L1 `Material requirement: "
        "preserve the supplied boundary.`\n"
        "Assessment: The quoted sentence states the required boundary."
    )
    assert _review_verdict(grounded_pass, review_input=review_input) == "PASS"
    with pytest.raises(WorkflowJobAuthorizationError) as caught:
        _review_verdict("Verdict: PASS", review_input=review_input)
    assert caught.value.classification == "document_job_nonactionable_review"

    invalid_passes = (
        grounded_pass.replace("assignment:L1", "assignment:L9"),
        grounded_pass.replace(
            "`The supplied boundary is preserved.`", "`not in the Artifact`"
        ),
        grounded_pass.replace("Assessment:", "Rationale:"),
        grounded_pass.replace(
            "Unresolved material issues: none", "Unresolved material issues: unclear"
        ),
    )
    for invalid_pass in invalid_passes:
        with pytest.raises(WorkflowJobAuthorizationError) as caught:
            _review_verdict(invalid_pass, review_input=review_input)
        assert caught.value.classification in {
            "document_job_nonactionable_review",
            "document_job_unverified_finding",
        }

    quoted_examples_with_verdict = (
        "Assessment summary.\n"
        "> 判定： BLOCKED\n"
        "```markdown\n判定： BLOCKED\n```\n"
        "判定： PASS"
    )
    with pytest.raises(WorkflowJobAuthorizationError) as caught:
        _review_verdict(quoted_examples_with_verdict, review_input=review_input)
    assert caught.value.classification == "document_job_nonactionable_review"
    quoted_examples = (
        "Assessment summary.\n> 判定： BLOCKED",
        "Assessment summary.\n```markdown\n判定： BLOCKED\n```",
        "参考:\n\n    Verdict: PASS",
        "````markdown\n```text\nVerdict: PASS\n```\n````",
    )
    for review in quoted_examples:
        with pytest.raises(WorkflowJobAuthorizationError) as caught:
            _review_verdict(review)
        assert caught.value.classification == "document_job_invalid_verdict"

    ambiguous_or_invalid = (
        "Assessment summary.\n判定: PASS\nVerdict: PASS",
        "Assessment summary.\n判定： PASS\n判定: PASS",
        "Assessment summary.\n判定： MAYBE",
        "Assessment summary.\nVerdict: `PASS`",
    )
    for review in ambiguous_or_invalid:
        with pytest.raises(WorkflowJobAuthorizationError) as caught:
            _review_verdict(review)
        assert caught.value.classification == "document_job_invalid_verdict"


def test_saved_japanese_findings_accept_bound_artifact_and_assignment_evidence() -> (
    None
):
    import hashlib

    from ai_office.engine.workflow_bounded_job import _review_verdict

    source_content = (
        "調査範囲と資料 **前提の上.replace**\n"
        "資料から確認できた事実 **必頍引数**\n"
        "( Humans: hash確認含む) 読取search ・ publishing\n"
        "報告された観測事実 research-and-summarize\n"
        "Administrative veto Evidence D"
    )
    source_bytes = source_content.encode("utf-8")
    review_input = json.dumps(
        {
            "assignment": "Evidence supplied for review.\n"
            "**Evidence A — source A**\n"
            "**Evidence B — source B**\n"
            "**Evidence C — source C**\n"
            "**Evidence D — source D**",
            "source_artifact": {
                "artifact_id": "artifact-747",
                "content": source_content,
                "content_length": len(source_bytes),
                "content_sha256": hashlib.sha256(source_bytes).hexdigest(),
            },
        }
    )
    review = (
        "Verdict: CHANGES_REQUIRED\n\n"
        "### F1: Japanese review finding\n"
        "- **要件**: 日本語Markdown報告。\n"
        "- **Artifact位置**: `**前提の上.replace**`、`**必頍引数**`、"
        "`( Humans: hash確認含む)`、`読取search`、`・ publishing`。\n"
        "- **証拠**: Artifact本文の当該文字列（供給内容のまま引用）。\n"
        "- **影響**: 文意が不明瞭。\n"
        "- **修正要求**: 誤記を直す。\n\n"
        "### F2: Evidence range\n"
        "- **要件**: 供給証拠の範囲。\n"
        "- **Artifact位置**: `research-and-summarize`。\n"
        "- **証拠**: 本Runに供給された証拠はコード断片A–Dのみ。\n"
        "- **影響**: 検証不能。\n"
        "- **修正要求**: 記述を削除する。\n\n"
        "### F3: Evidence D\n"
        "- **要件**: 断片の表示範囲。\n"
        "- **Artifact位置**: 経路説明。\n"
        "- **証拠**: 課題の Evidence D は条件列で切断。\n"
        "- **影響**: 断定が強すぎる。\n"
        "- **修正要求**: 表現を限定する。"
    )
    assert _review_verdict(review, review_input=review_input) == "CHANGES_REQUIRED"

    unsupported = review.replace("`**前提の上.replace**`", "`not-in-artifact`")
    with pytest.raises(WorkflowJobAuthorizationError) as caught:
        _review_verdict(unsupported, review_input=review_input)
    assert caught.value.classification == "document_job_unverified_finding"


@pytest.mark.parametrize(
    ("real_uid", "effective_uid", "is_tty"),
    ((2202, 2202, False), (0, 0, True), (2202, 1001, True)),
)
def test_bounded_job_launcher_rejects_operator_context_before_credential_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_uid: int,
    effective_uid: int,
    is_tty: bool,
) -> None:
    from types import SimpleNamespace

    credential_lookups: list[str] = []

    def fail_if_credential_loaded(_target):
        credential_lookups.append("lookup")
        raise AssertionError("offline launcher guard must precede credentials")

    monkeypatch.setattr(
        cli_module, "_load_api_key_for_target", fail_if_credential_loaded
    )
    monkeypatch.setattr(
        cli_module,
        "sys",
        SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: is_tty)),
    )
    monkeypatch.setattr(
        cli_module,
        "os",
        SimpleNamespace(getuid=lambda: real_uid, geteuid=lambda: effective_uid),
    )
    repository = Path(__file__).resolve().parents[1]
    result = CliRunner().invoke(
        app,
        [
            "workflows",
            "bounded-job-run",
            "offline-job",
            "--workflow",
            "article-production",
            "--run-id",
            "offline-run",
            "--assignment-file",
            str(tmp_path / "assignment.md"),
            "--run-store",
            str(tmp_path / "runs"),
            "--directory",
            str(repository / "workflows"),
            "--employees-directory",
            str(repository / "employees"),
            "--execution-target",
            "opencode-go-chat",
            "--execution-destinations",
            str(repository / "docs/examples/openai-compatible-destinations.yaml"),
            "--step-model",
            "write-draft=glm-5.3-flash",
            "--step-model",
            "review-quality=glm-5.3-flash",
            "--step-model",
            "review-evidence=glm-5.3-flash",
            "--step-model",
            "revise=glm-5.3-flash",
        ],
    )
    assert result.exit_code != 0
    assert credential_lookups == []
    assert "operator_context" not in result.output


@pytest.mark.parametrize(
    ("heterogeneous", "retry_scenario"),
    [
        (False, "retry_429"),
        (False, "retry_500"),
        (False, "retry_503"),
        (True, "retry_500"),
        (False, "zero_retry_budget"),
        (False, "quota_500"),
        (False, "repeated_500"),
    ],
)
def test_bounded_job_cli_retries_only_scoped_http_failures_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    heterogeneous: bool,
    retry_scenario: str,
) -> None:
    from hashlib import sha256
    from types import SimpleNamespace

    from ai_office.definitions.style_profile import (
        add_style_profile_to_assignment,
        load_style_profile,
    )
    from ai_office.engine.workflow_bounded_job import inspect_bounded_document_job

    repository = Path(__file__).resolve().parents[1]
    expected_models = {
        "write-draft": "deepseek-v4.1-flash",
        "review-quality": "deepseek-v4-pro",
        "review-evidence": "glm-5.3-flash",
        "revise": "deepseek-v4.1-flash",
    }
    destinations_file = tmp_path / "destinations.yaml"
    workflow_directory = repository / "workflows"
    if heterogeneous:
        models = sorted(set(expected_models.values()))
        destinations_file.write_text(
            yaml.safe_dump(
                {
                    "destinations": {
                        "opencode-zen": {
                            "protocol": "openai-responses",
                            "endpoint": "https://opencode.ai/zen/v1/responses",
                            "credential": "OPENCODE_API_KEY",
                            "models": models,
                        },
                        "opencode-go-chat": {
                            "protocol": "openai-chat-completions",
                            "endpoint": "https://opencode.ai/inference/go/openai/v1/chat/completions",
                            "credential": "OPENCODE_API_KEY",
                            "models": models,
                        },
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        workflow_directory = tmp_path / "workflows"
        workflow_directory.mkdir()
        raw_workflow = yaml.safe_load(
            (repository / "workflows/article-production.yaml").read_text(
                encoding="utf-8"
            )
        )
        raw_workflow["execution_destination"] = "opencode-zen"
        next(step for step in raw_workflow["steps"] if step["id"] == "review-quality")[
            "execution_destination"
        ] = "opencode-go-chat"
        (workflow_directory / "article-production.yaml").write_text(
            yaml.safe_dump(raw_workflow, sort_keys=False), encoding="utf-8"
        )
    else:
        destinations = (repository / "config/destinations.yaml").read_text(
            encoding="utf-8"
        )
        destinations_file.write_text(destinations, encoding="utf-8")
        workflow_directory = tmp_path / "workflows"
        workflow_directory.mkdir()
        (workflow_directory / "article-production.yaml").write_bytes(
            (repository / "workflows/article-production.yaml").read_bytes()
        )
    assignment_file = tmp_path / "brief.md"
    assignment_file.write_text(
        "Medium: technical blog\nAudience: engineers\n"
        "Requirement: Explain the supplied method.",
        encoding="utf-8",
    )
    style_profile_directory = tmp_path / "style_profiles"
    style_profile_directory.mkdir()
    style_profile_file = style_profile_directory / "toshio-web.yaml"
    style_profile_file.write_bytes(
        (repository / "style_profiles/toshio-web.yaml").read_bytes()
    )
    style_profile = load_style_profile("toshio-web", style_profile_directory)
    effective_assignment = add_style_profile_to_assignment(
        assignment_file.read_text(encoding="utf-8"), style_profile
    )
    store_root = tmp_path / "runs"
    monkeypatch.setattr(
        cli_module, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
    )
    monkeypatch.setattr(
        cli_module, "os", SimpleNamespace(geteuid=lambda: 1001, getuid=lambda: 1001)
    )
    monkeypatch.setattr(cli_module.getpass, "getuser", lambda: "offline-operator")
    key_providers: list[str] = []
    monkeypatch.setattr(
        cli_module,
        "_load_api_key_for_target",
        lambda target: (
            key_providers.append(target.provider)
            or OpenAIApiKey(value=SecretStr("synthetic-only"))
        ),
    )
    sent = []
    sent_models = []
    sent_providers = []

    def fake_transport(request):
        sent.append(request)
        sent_models.append(json.loads(request.body)["model"])
        if len(sent) == 1:
            # The Job has already pinned its effective input before the first
            # transport call. Removing the source must not alter retries or
            # any later reviewer Run.
            style_profile_file.unlink()
        provider = (
            "opencode-zen"
            if request.url == "https://opencode.ai/zen/v1/responses"
            else "opencode-go-chat"
        )
        sent_providers.append(provider)
        if len(sent) == 1 or (retry_scenario == "repeated_500" and len(sent) == 2):
            status_code = (
                int(retry_scenario.removeprefix("retry_"))
                if retry_scenario.startswith("retry_")
                else 500
            )
            response_body = (
                b'{"error":{"message":"insufficient_quota",'
                b'"type":"insufficient_quota","code":"insufficient_quota"}}'
                if retry_scenario == "quota_500"
                else b"synthetic upstream failure"
                if status_code == 500
                else b'{"error":{"message":"temporary unavailable",'
                b'"type":"server_error","code":"server_error"}}'
            )
            content_type = (
                "application/json"
                if retry_scenario in {"quota_500", "retry_429", "retry_503"}
                else "text/plain"
            )
            return OpenAIResponsesRawHttpResponse(
                status_code,
                "synthetic",
                (("content-type", content_type),),
                response_body,
            )
        content = (
            "# Article\n\nExplain the supplied method."
            if len(sent) == 2
            else _grounded_pass_for_request(request)
        )
        if provider == "opencode-zen":
            response = {
                "id": f"offline-{len(sent)}",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": content}],
                    }
                ],
            }
        else:
            response = {
                "id": f"offline-{len(sent)}",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            }
        return OpenAIResponsesRawHttpResponse(
            200,
            "synthetic",
            (),
            json.dumps(response).encode(),
        )

    monkeypatch.setattr(
        cli_module, "send_openai_responses_http_request", fake_transport
    )
    job_id = "single-workflow-offline"
    result = CliRunner().invoke(
        app,
        [
            "workflows",
            "bounded-job-run",
            job_id,
            "--workflow",
            "article-production",
            "--run-id",
            "article-initial",
            "--assignment-file",
            str(assignment_file),
            "--style-profile",
            "toshio-web",
            "--run-store",
            str(store_root),
            "--directory",
            str(workflow_directory),
            "--employees-directory",
            str(repository / "employees"),
            "--execution-target",
            "opencode-go-chat",
            "--execution-destinations",
            str(destinations_file),
            "--step-model",
            f"write-draft={expected_models['write-draft']}",
            "--step-model",
            f"review-quality={expected_models['review-quality']}",
            "--step-model",
            f"review-evidence={expected_models['review-evidence']}",
            "--step-model",
            f"revise={expected_models['revise']}",
            "--max-revisions",
            "0",
            "--max-retries",
            "0" if retry_scenario == "zero_retry_budget" else "1",
            "--retry-interval-seconds",
            "1",
        ],
        input=f"{job_id}\n{job_id}\n",
    )
    assert result.exit_code == 0, result.output
    preview = json.loads(result.output.splitlines()[0])
    assert preview["workflow_id"] == "article-production"
    assert preview["style_profile"] == {
        "id": "toshio-web",
        "version": "0.2",
        "content_sha256": style_profile.content_sha256,
    }
    assert (
        preview["assignment_sha256"]
        == sha256(effective_assignment.encode("utf-8")).hexdigest()
    )
    assert preview["guaranteed_initial_actions"] == 3
    assert preview["reviewer_count"] == 2
    max_retries = 0 if retry_scenario == "zero_retry_budget" else 1
    assert preview["max_retries"] == max_retries
    assert preview["retryable_http_statuses"] == [429, 500, 503]
    action_models = {
        step_id: model
        for step_id, model in expected_models.items()
        if step_id != "revise"
    }
    assert {
        item["step_id"]: item["model"] for item in preview["planned_steps"]
    } == action_models
    assert {
        item["step_id"]: item["model"] for item in preview["step_models"]
    } == expected_models
    is_success = retry_scenario.startswith("retry_")
    expected_send_count = (
        4 if is_success else 2 if retry_scenario == "repeated_500" else 1
    )
    assert len(sent) == expected_send_count, result.output
    expected_providers = (
        ["opencode-zen", "opencode-go-chat", "opencode-zen"]
        if heterogeneous
        else ["opencode-go-chat"] * 3
    )
    expected_attempt_providers = (
        [expected_providers[0], *expected_providers]
        if is_success
        else [expected_providers[0]] * expected_send_count
    )
    expected_route_providers = (
        ["opencode-zen", "opencode-go-chat", "opencode-zen", "opencode-zen"]
        if heterogeneous
        else ["opencode-go-chat"] * 4
    )
    assert sent_providers == expected_attempt_providers
    assert key_providers == expected_route_providers
    expected_action_models = [
        expected_models["write-draft"],
        expected_models["review-quality"],
        expected_models["review-evidence"],
    ]
    expected_sent_models = (
        [expected_action_models[0], *expected_action_models]
        if is_success
        else [expected_action_models[0]] * expected_send_count
    )
    assert sent_models == expected_sent_models

    assert [_assignment_from_model_request(request) for request in sent] == [
        effective_assignment
    ] * len(sent)
    store = WorkflowRunManifestStore(store_root)
    inspection = inspect_bounded_document_job(store, job_id)
    assert inspection["status"] == ("candidate" if is_success else "stopped")
    assert inspection["consumed_provider_attempts"] == len(sent)
    assert inspection["normal_actions_consumed"] == (3 if is_success else 1)
    expected_retry_count = 1 if is_success or retry_scenario == "repeated_500" else 0
    first_status = (
        int(retry_scenario.removeprefix("retry_"))
        if retry_scenario.startswith("retry_")
        else 500
    )
    assert inspection["retry_attempts_consumed"] == expected_retry_count
    expected_dispatches = (
        [(0, 0, first_status), (0, 1, 200), (1, 0, 200), (2, 0, 200)]
        if is_success
        else [(0, 0, 500), (0, 1, 500)]
        if retry_scenario == "repeated_500"
        else [(0, 0, 500)]
    )
    assert [
        (item["action_ordinal"], item["retry_index"], item["http_status"])
        for item in sorted(
            inspection["dispatch_order"],
            key=lambda item: (item["action_ordinal"], item["retry_index"]),
        )
    ] == expected_dispatches
    assert inspection["retryable_http_statuses"] == [429, 500, 503]
    scope = load_bounded_workflow_job_scope(store, job_id)
    assert scope.schema_version == "workflow-bounded-job-scope.v3"
    assert scope.max_retries == max_retries
    assert scope.max_provider_attempts == 3 + max_retries
    assert scope.retryable_http_statuses == (429, 500, 503)
    assert scope.assignment_sha256 == preview["assignment_sha256"]
    assert scope.initial_input_sha256 == preview["assignment_sha256"]
    assert {slot.step_id: slot.model for slot in scope.actions} == action_models
    assert len(inspection["dispatch_order"]) == len(sent)
    assert len(scope.actions) == 3

    # A v3 scope persisted before HTTP 500 was added remains digest-valid and
    # retains its original retry semantics when reloaded.
    legacy_statuses = (429, 503)
    legacy_digest = bounded_job_module._scope_intent_digest(
        scope.job_id,
        scope.assignment_sha256,
        scope.initial_run_id,
        scope.initial_input_sha256,
        scope.actions,
        scope.max_provider_attempts,
        scope.max_revisions,
        scope.expires_at_ns,
        scope.max_retries,
        scope.retry_interval_seconds,
        scope.source_manifest_run_id,
        scope.source_manifest_digest,
        legacy_statuses,
    )
    legacy_scope = replace(
        scope,
        retryable_http_statuses=legacy_statuses,
        business_grant=replace(scope.business_grant, envelope_sha256=legacy_digest),
        execution_grant=replace(scope.execution_grant, envelope_sha256=legacy_digest),
    )
    legacy_root = tmp_path / "legacy-runs"
    legacy_root.mkdir(mode=0o700)
    bounded_job_module._persist_immutable(
        bounded_job_module._scope_path(legacy_root, job_id), legacy_scope.canonical
    )
    legacy_loaded = load_bounded_workflow_job_scope(
        WorkflowRunManifestStore(legacy_root), job_id
    )
    assert legacy_loaded == legacy_scope
    assert legacy_loaded.retryable_http_statuses == legacy_statuses
    assert not bounded_job_module._retryable_http_response(
        500, b"synthetic upstream failure", legacy_loaded.retryable_http_statuses
    )

    if not is_success:
        first_action_dispatches = [
            item for item in inspection["dispatch_order"] if item["action_ordinal"] == 0
        ]
        action_run_id = first_action_dispatches[0]["run_id"]
        attempts = list_run_execution_evidence(store.root, action_run_id)
        assert len(attempts) == expected_send_count
        assert len({attempt.request_body_sha256 for attempt in attempts}) == 1
        for attempt in attempts:
            raw = load_raw_response_evidence(
                store.root, action_run_id, attempt.attempt_id
            )
            normalized = load_normalized_result_evidence(
                store.root, action_run_id, attempt.attempt_id
            )
            assert raw.status_code == first_status
            assert normalized.result.category in {"api_error", "invalid_response"}
            normalized_status = getattr(normalized.result, "status_code", None)
            if normalized_status is None:
                normalized_status = getattr(
                    getattr(normalized.result, "response_diagnostics", None),
                    "status_code",
                    None,
                )
            assert normalized_status == 500
        return

    evidence_models = {}
    evidence_providers = []

    def manifest_assignment(run_input: str) -> str:
        try:
            value = json.loads(run_input)
        except json.JSONDecodeError:
            return run_input
        if isinstance(value, dict) and isinstance(value.get("assignment"), str):
            return value["assignment"]
        return run_input

    for slot in scope.actions:
        action_dispatches = [
            item
            for item in inspection["dispatch_order"]
            if item["action_ordinal"] == slot.ordinal
        ]
        expected_retries = 1 if slot.ordinal == 0 else 0
        assert [item["retry_index"] for item in action_dispatches] == list(
            range(expected_retries + 1)
        )
        manifest = load_workflow_run_manifest(store, action_dispatches[0]["run_id"])
        assert manifest_assignment(manifest.run_input) == effective_assignment
        assert manifest.workflow_snapshot.steps[0].id == slot.step_id
        assert (
            manifest.workflow_snapshot.steps[0].model == expected_models[slot.step_id]
        )
        expected_provider = expected_providers[slot.ordinal]
        assert manifest.execution_destination_for_step(1).provider == expected_provider
        attempts = list_run_execution_evidence(
            store.root, action_dispatches[0]["run_id"]
        )
        assert len(attempts) == len(action_dispatches)
        for attempt in attempts:
            assert attempt.authorization_source == "job_action"
            assert attempt.job_id == job_id
            assert attempt.step_id == slot.step_id
            evidence_models[slot.step_id] = json.loads(
                base64.b64decode(attempt.request_body_base64)
            )["model"]
            evidence_request = SimpleNamespace(
                body=base64.b64decode(attempt.request_body_base64)
            )
            assert _assignment_from_model_request(evidence_request) == (
                effective_assignment
            )
            evidence_providers.append(attempt.provider)
    assert evidence_models == action_models
    assert evidence_providers == expected_attempt_providers
    assert scope.source_manifest_run_id is not None
    source = load_workflow_run_manifest(store, scope.source_manifest_run_id)
    assert manifest_assignment(source.run_input) == effective_assignment
    assert source.digest == scope.source_manifest_digest
    assert source.workflow_snapshot.bounded_review is not None
    assert [
        source.execution_destination_for_step(index).provider
        for index in range(1, len(source.workflow_snapshot.steps) + 1)
    ] == expected_route_providers
    assert (
        load_workflow_run_manifest(
            store, "article-initial"
        ).workflow_snapshot.bounded_review
        is None
    )


@pytest.mark.parametrize(
    ("status_code", "body", "statuses", "expected"),
    [
        (500, b"upstream unavailable", (429, 500, 503), True),
        (500, b"upstream unavailable", (429, 503), False),
        (429, b"rate limited", (429, 500, 503), True),
        (
            503,
            b'{"error":{"type":"server_error","message":"unavailable"}}',
            (429, 500, 503),
            True,
        ),
        (502, b"bad gateway", (429, 500, 503), False),
        (504, b"gateway timeout", (429, 500, 503), False),
        (200, b"not valid output", (429, 500, 503), False),
        (
            500,
            b'{"error":{"code":"insufficient_quota"}}',
            (429, 500, 503),
            False,
        ),
        (500, b'{"error":{"message":"billing limit reached"}}', (429, 500, 503), False),
        (
            500,
            b'{"error":{"message":"payment required"}}',
            (429, 500, 503),
            False,
        ),
    ],
)
def test_bounded_job_http_retry_status_and_quota_gates(
    status_code: int,
    body: bytes,
    statuses: tuple[int, ...],
    expected: bool,
) -> None:
    assert (
        bounded_job_module._retryable_http_response(status_code, body, statuses)
        is expected
    )


def test_bounded_job_invalid_style_profile_stops_before_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    repository = Path(__file__).resolve().parents[1]
    assignment_file = tmp_path / "brief.md"
    assignment_file.write_text("A short article brief.", encoding="utf-8")
    store_root = tmp_path / "runs"
    credential_lookups: list[str] = []
    transport_calls: list[object] = []
    monkeypatch.setattr(
        cli_module, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
    )
    monkeypatch.setattr(
        cli_module, "os", SimpleNamespace(geteuid=lambda: 1001, getuid=lambda: 1001)
    )
    monkeypatch.setattr(
        cli_module,
        "_load_api_key_for_target",
        lambda target: credential_lookups.append(target.provider),
    )
    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        lambda request: transport_calls.append(request),
    )

    result = CliRunner().invoke(
        app,
        [
            "workflows",
            "bounded-job-run",
            "invalid-style-job",
            "--workflow",
            "article-production",
            "--run-id",
            "invalid-style-run",
            "--assignment-file",
            str(assignment_file),
            "--style-profile",
            "../outside",
            "--run-store",
            str(store_root),
            "--execution-destinations",
            str(repository / "config/destinations.yaml"),
        ],
    )

    assert result.exit_code != 0
    assert "selected style profile is invalid or unavailable" in result.output
    assert not store_root.exists()
    assert credential_lookups == []
    assert transport_calls == []


def test_bounded_job_cli_rejects_destination_changed_after_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    repository = Path(__file__).resolve().parents[1]
    destination_file = tmp_path / "destinations.yaml"
    source_destination_file = (
        repository / "docs/examples/openai-compatible-destinations.yaml"
    )
    destination_file.write_text(source_destination_file.read_text(encoding="utf-8"))
    assignment_file = tmp_path / "assignment.md"
    assignment_file.write_text("A bounded assignment.", encoding="utf-8")
    run_store = tmp_path / "run-store"
    job_id = "stale-confirmation-job"
    prompts = 0

    def confirm_and_mutate(*_args, **_kwargs):
        nonlocal prompts
        prompts += 1
        if prompts == 2:
            contents = destination_file.read_text(encoding="utf-8")
            destination_file.write_text(
                contents.replace(
                    "https://ai-gateway.example.com/v1/chat/completions",
                    "https://ai-gateway.example.com/other/v1/chat/completions",
                ),
                encoding="utf-8",
            )
        return job_id

    monkeypatch.setattr(
        cli_module,
        "sys",
        SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)),
    )
    monkeypatch.setattr(
        cli_module, "os", SimpleNamespace(geteuid=lambda: 2202, getuid=lambda: 2202)
    )
    monkeypatch.setattr(cli_module.typer, "prompt", confirm_and_mutate)
    credential_lookups: list[str] = []
    transport_calls: list[object] = []
    monkeypatch.setattr(
        cli_module,
        "_load_api_key_for_target",
        lambda _target: credential_lookups.append("lookup"),
    )
    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        lambda request: transport_calls.append(request),
    )
    result = CliRunner().invoke(
        app,
        [
            "workflows",
            "bounded-job-run",
            job_id,
            "--workflow",
            "article-production",
            "--run-id",
            "stale-confirmation-run",
            "--assignment-file",
            str(assignment_file),
            "--run-store",
            str(run_store),
            "--directory",
            str(repository / "workflows"),
            "--employees-directory",
            str(repository / "employees"),
            "--execution-target",
            "internal-chat-gateway",
            "--execution-destinations",
            str(destination_file),
            "--step-model",
            "write-draft=approved-chat-model",
            "--step-model",
            "review-quality=approved-chat-model",
            "--step-model",
            "review-evidence=approved-chat-model",
            "--step-model",
            "revise=approved-chat-model",
        ],
    )
    assert result.exit_code != 0
    assert prompts == 2
    assert credential_lookups == []
    assert transport_calls == []
    assert not run_store.exists()


def test_bounded_job_cli_cancel_does_not_issue_grants_or_load_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    repository = Path(__file__).resolve().parents[1]
    assignment_file = tmp_path / "assignment.md"
    assignment_file.write_text("A bounded assignment.", encoding="utf-8")
    run_store = tmp_path / "run-store"
    job_id = "cancelled-offline-job"
    credential_lookups: list[str] = []
    transport_calls: list[object] = []
    monkeypatch.setattr(
        cli_module,
        "sys",
        SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True)),
    )
    monkeypatch.setattr(
        cli_module, "os", SimpleNamespace(geteuid=lambda: 2202, getuid=lambda: 2202)
    )
    monkeypatch.setattr(
        cli_module,
        "_load_api_key_for_target",
        lambda _target: credential_lookups.append("lookup"),
    )
    monkeypatch.setattr(
        cli_module,
        "send_openai_responses_http_request",
        lambda request: transport_calls.append(request),
    )
    result = CliRunner().invoke(
        app,
        [
            "workflows",
            "bounded-job-run",
            job_id,
            "--workflow",
            "article-production",
            "--run-id",
            "cancelled-offline-run",
            "--assignment-file",
            str(assignment_file),
            "--run-store",
            str(run_store),
            "--directory",
            str(repository / "workflows"),
            "--employees-directory",
            str(repository / "employees"),
            "--execution-target",
            "opencode-go-chat",
            "--execution-destinations",
            str(repository / "docs/examples/openai-compatible-destinations.yaml"),
            "--step-model",
            "write-draft=glm-5.3-flash",
            "--step-model",
            "review-quality=glm-5.3-flash",
            "--step-model",
            "review-evidence=glm-5.3-flash",
            "--step-model",
            "revise=glm-5.3-flash",
        ],
        input="cancelled\n",
    )
    assert result.exit_code != 0
    assert "Business grant was not confirmed" in result.output
    assert credential_lookups == []
    assert transport_calls == []
    assert not run_store.exists()


def test_bounded_job_launcher_uses_generic_registered_destination_policy() -> None:
    repository = Path(__file__).resolve().parents[1]
    registry = load_execution_destination_registry(
        repository / "docs/examples/openai-compatible-destinations.yaml"
    )
    go = registry.resolve("opencode-go-chat")
    gateway = registry.resolve("internal-chat-gateway")
    assert go.target.protocol == gateway.target.protocol == "openai-chat-completions"
    assert go.select_model("glm-5.3-flash") == "glm-5.3-flash"
    assert gateway.select_model("approved-chat-model") == "approved-chat-model"
    assert go.target.provider != gateway.target.provider
    assert go.target.endpoint != gateway.target.endpoint


@pytest.mark.parametrize(
    (
        "medium",
        "assignment",
        "verdict",
        "expected_status",
        "expected_sent",
        "expected_dispatches",
    ),
    [
        (
            "note",
            "Medium: note\nAudience: readers\nClaim: start with unknowns",
            "PASS",
            "candidate",
            3,
            3,
        ),
        (
            "technical",
            "Medium: technical blog\nAudience: engineers\n"
            "Claim: cite supplied evidence",
            "PASS",
            "candidate",
            3,
            3,
        ),
        (
            "note",
            "Medium: note\nAudience: readers\nClaim: start with unknowns",
            "Verdict: BLOCKED",
            "stopped",
            2,
            2,
        ),
        (
            "technical",
            "Medium: technical blog\nAudience: engineers\n"
            "Claim: cite supplied evidence",
            "Verdict: MAYBE",
            "stopped",
            2,
            2,
        ),
        (
            "revised-note",
            "Medium: note\nAudience: readers\nClaim: start with unknowns",
            "CHANGES_REQUIRED",
            "reviewed",
            6,
            6,
        ),
        (
            "no-budget",
            "Medium: note\nAudience: readers\nClaim: start with unknowns",
            "CHANGES_REQUIRED_NO_BUDGET",
            "stopped",
            3,
            3,
        ),
        (
            "unknown-send",
            "Medium: technical blog\nAudience: engineers\n"
            "Claim: cite supplied evidence",
            "TIMEOUT",
            "stopped",
            3,
            3,
        ),
    ],
)
def test_single_article_workflow_uses_distinct_run_inputs_on_same_offline_path(
    tmp_path: Path,
    medium: str,
    assignment: str,
    verdict: str,
    expected_status: str,
    expected_sent: int,
    expected_dispatches: int,
) -> None:
    """The same source definition plans different briefs and preserves lineage."""
    from ai_office.engine.workflow_bounded_job import inspect_bounded_document_job
    from ai_office.providers.openai import OpenAIResponsesRawHttpResponse

    repository = Path(__file__).resolve().parents[1]
    workflow = load_workflow_file(repository / "workflows/article-production.yaml")
    employees = {
        employee_id: load_employee_file(
            repository / "employees" / f"{employee_id}.yaml"
        )
        for employee_id in ("article-writer", "document-reviewer", "evidence-auditor")
    }
    store_root = tmp_path / "configured-job"
    store_root.mkdir(mode=0o700)
    store = WorkflowRunManifestStore(store_root)
    target = DIRECT_OPENAI_EXECUTION_TARGET
    revision_case = verdict == "CHANGES_REQUIRED"
    max_revisions = 1 if revision_case else 0
    initial_run_id = f"configured-{medium}-initial"
    source_manifest = create_workflow_run_manifest(
        store,
        f"configured-{medium}-source",
        assignment,
        workflow,
        tuple(employees.values()),
        execution_target=target,
    )

    def digest_snapshot(snapshot: object) -> str:
        return _snapshot_digest(snapshot)

    slots = []
    for ordinal, (step_id, step_index, input_source) in enumerate(
        bounded_review_action_plan(workflow, max_revisions)
    ):
        view = bounded_job_execution_view(workflow, step_id)
        manifest = create_workflow_run_manifest(
            store,
            initial_run_id
            if ordinal == 0
            else f"configured-{medium}-template-{ordinal}",
            assignment,
            view,
            (employees[view.definition.steps[0].employee],),
            execution_target=target,
        )
        employee_snapshot = manifest.employee_snapshots[0]
        slots.append(
            BoundedJobActionSlot(
                ordinal=ordinal,
                workflow_id=workflow.definition.id,
                step_id=step_id,
                step_index=step_index,
                employee_id=employee_snapshot.id,
                workflow_snapshot_sha256=digest_snapshot(manifest.workflow_snapshot),
                employee_snapshot_sha256=digest_snapshot(employee_snapshot),
                provider=target.provider,
                execution_target_fingerprint=execution_target_fingerprint(target),
                model=manifest.workflow_snapshot.steps[step_index - 1].model,
                input_source=input_source,
            )
        )
    assignment_sha = sha256(assignment.encode()).hexdigest()

    def issue_scope(actions):
        return issue_bounded_workflow_job_scope(
            store,
            job_id=f"configured-{medium}",
            initial_run_id=initial_run_id,
            assignment_sha256=assignment_sha,
            initial_input_sha256=assignment_sha,
            actions=actions,
            max_provider_attempts=3 + 3 * max_revisions,
            max_revisions=max_revisions,
            source_manifest_run_id=source_manifest.run_id,
            source_manifest_digest=source_manifest.digest,
            expires_at_ns=9_999_999_999_999_999_999,
            business_approved_by="offline-test",
            business_grant_id=f"business-{medium}",
            execution_approved_by="offline-test",
            execution_grant_id=f"execution-{medium}",
            now_ns=1,
        )

    if medium == "note" and verdict == "PASS":
        # An action slot cannot silently swap the source-defined reviewer.
        tampered = (
            slots[0],
            replace(slots[1], employee_id="article-writer"),
        )
        with pytest.raises(WorkflowJobAuthorizationError):
            issue_scope(tampered)
    scope = issue_scope(tuple(slots))
    sent = []

    def grounded_changes(request) -> str:
        assignment_line = assignment.splitlines()[0]
        return (
            "Verdict: CHANGES_REQUIRED\n\n"
            "### Finding 1\n"
            f"Requirement: {assignment_line}\n"
            "Artifact location: `# Draft`\n"
            f"Evidence source/reference: assignment:L1 `{assignment_line}`\n"
            "Impact: the requested medium is not clear.\n"
            "Requested correction: label the revised draft for the stated medium."
        )

    def describe(request) -> str:
        index = len(sent)
        if index == 1:
            return "# Draft\n\nGenerated from supplied input only."
        if verdict == "PASS":
            return _grounded_pass_for_request(request)
        if verdict == "Verdict: BLOCKED":
            return "Verdict: BLOCKED\n\nEvidence is insufficient."
        if verdict == "Verdict: MAYBE":
            return "Verdict: MAYBE"
        if verdict == "CHANGES_REQUIRED":
            if index == 2:
                return grounded_changes(request)
            if index == 4:
                return "# Revised draft\n\nOnly supplied claims remain."
            return _grounded_pass_for_request(request)
        if verdict == "CHANGES_REQUIRED_NO_BUDGET":
            return (
                grounded_changes(request)
                if index == 2
                else _grounded_pass_for_request(request)
            )
        # TIMEOUT: the first reviewer returns a valid verdict and the second
        # reviewer's send has an ambiguous, unknown outcome.
        return _grounded_pass_for_request(request)

    def fake_transport(request):
        sent.append(request)
        if verdict == "TIMEOUT" and len(sent) == 3:
            raise TimeoutError("offline ambiguous send")
        output = describe(request)
        body = json.dumps(
            {
                "id": f"offline-{len(sent)}",
                "object": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": output}],
                    }
                ],
            }
        ).encode()
        return OpenAIResponsesRawHttpResponse(200, "synthetic", (), body)

    outcome = run_bounded_document_job(
        store,
        scope,
        initial_run_id=initial_run_id,
        assignment=assignment,
        workflows={workflow.definition.id: workflow},
        employees=employees,
        api_key=OpenAIApiKey(value=SecretStr("offline-only")),
        transport=fake_transport,
    )
    assert outcome.status == expected_status, outcome
    assert len(sent) == expected_sent
    inspection = inspect_bounded_document_job(store, scope.job_id)
    assert inspection["status"] == expected_status
    assert inspection["consumed_provider_attempts"] == expected_sent
    assert len(inspection["dispatch_order"]) == expected_dispatches
    if expected_status in {"candidate", "reviewed"}:
        review_manifest = load_workflow_run_manifest(
            store, outcome.review_artifact_run_id
        )
        review_input = json.loads(review_manifest.run_input)
        assert review_input["assignment"] == assignment
        assert (
            review_input["source_artifact"]["artifact_id"] == outcome.final_artifact_id
        )
        assert review_input["source_artifact"]["content_sha256"]
