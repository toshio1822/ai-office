"""Focused provider-free tests for the Phase 264 regeneration control contract."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

import ai_office.engine.publication_regeneration as publication_regeneration_module
from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.post_terminal_facts import (
    PublicationClaimContract,
    build_post_terminal_facts,
    build_publication_readiness_audit_record,
    load_persisted_terminal_snapshot,
    persist_publication_readiness_audit,
    post_terminal_facts_digest,
)
from ai_office.engine.publication_regeneration import (
    PublicationRegenerationApproval,
    PublicationRegenerationApprovalError,
    PublicationRegenerationAttemptAlreadyConsumedError,
    PublicationRegenerationAttemptClaim,
    PublicationRegenerationAttemptClaimError,
    PublicationRegenerationAttemptClaimLoadError,
    PublicationRegenerationAttemptClaimPersistenceError,
    PublicationRegenerationPlan,
    PublicationRegenerationPlanError,
    approve_publication_regeneration,
    build_publication_regeneration_attempt_claim,
    build_publication_regeneration_plan,
    claim_publication_regeneration_attempt,
    load_publication_regeneration_attempt_claim,
    publication_regeneration_approval_canonical_bytes,
    publication_regeneration_approval_digest,
    publication_regeneration_attempt_claim_canonical_bytes,
    publication_regeneration_attempt_claim_digest,
    publication_regeneration_attempt_claim_path,
    publication_regeneration_consumption_key,
    publication_regeneration_consumption_key_from_approval_id,
    publication_regeneration_plan_canonical_bytes,
    publication_regeneration_plan_digest,
    serialize_publication_regeneration_approval_canonical,
    serialize_publication_regeneration_attempt_claim_canonical,
    serialize_publication_regeneration_plan_canonical,
    validate_publication_regeneration_approval,
    validate_publication_regeneration_plan,
)
from ai_office.execution_target import (
    DIRECT_OPENAI_EXECUTION_TARGET,
    LOCAL_OMNIROUTE_EXECUTION_TARGET,
    execution_target_fingerprint,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    RuntimeFact,
    RuntimeFactProvenance,
    RuntimeFactsSnapshot,
    UpstreamStepOutput,
    approve_model_invocation_execution,
    build_model_invocation_execution_fingerprint,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)
from ai_office.tools import ToolDefinition, ToolParameterDefinition


class PublicationRegenerationPlanChild(PublicationRegenerationPlan):
    pass


class PublicationRegenerationApprovalChild(PublicationRegenerationApproval):
    pass


class PublicationRegenerationAttemptClaimChild(PublicationRegenerationAttemptClaim):
    pass


class StringChild(str):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "phase264-workflow",
            "name": "Phase 264 workflow",
            "description": "regeneration control fixture",
            "steps": [
                {
                    "id": "research",
                    "name": "Research",
                    "employee": "researcher",
                    "instructions": "Research.",
                },
                {
                    "id": "publish",
                    "name": "Publish",
                    "employee": "editor",
                    "instructions": "Prepare.",
                },
            ],
        }
    )


def success_history(
    output: str = "ORIGINAL BUSINESS OUTPUT 日本語",
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    definition = workflow()
    state = WorkflowExecutionState(
        workflow_id=definition.id,
        status="succeeded",
        current_step_id="publish",
        current_step_index=2,
        current_employee_id="editor",
        completed_step_ids=("research", "publish"),
        last_failure_category=None,
    )
    events = (
        RuntimeStepEvent(
            event_type="step_succeeded",
            workflow_id=definition.id,
            step_id="research",
            step_index=1,
            employee_id="researcher",
            previous_status="running",
            next_status="succeeded",
            provider="research-provider",
            failure_category=None,
            response_id="response-one",
            request_id="request-one",
            output_text="intermediate",
            message=None,
        ),
        RuntimeStepEvent(
            event_type="step_succeeded",
            workflow_id=definition.id,
            step_id="publish",
            step_index=2,
            employee_id="editor",
            previous_status="running",
            next_status="succeeded",
            provider="terminal-provider",
            failure_category=None,
            response_id="response-terminal",
            request_id="request-terminal",
            output_text=output,
            message=None,
        ),
    )
    return state, events


def failure_history() -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    definition = workflow()
    state = WorkflowExecutionState(
        workflow_id=definition.id,
        status="failed",
        current_step_id="research",
        current_step_index=1,
        current_employee_id="researcher",
        completed_step_ids=(),
        last_failure_category="api_error",
    )
    event = RuntimeStepEvent(
        event_type="step_failed",
        workflow_id=definition.id,
        step_id="research",
        step_index=1,
        employee_id="researcher",
        previous_status="running",
        next_status="failed",
        provider="failure-provider",
        failure_category="api_error",
        response_id=None,
        request_id="request-failure",
        output_text=None,
        message="safe failure",
    )
    return state, (event,)


def write_history(
    path: Path,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> WorkflowExecutionPersistenceTargets:
    path.mkdir(parents=True)
    targets = WorkflowExecutionPersistenceTargets(
        state_path=path / "state.json",
        events_path=path / "events.jsonl",
    )
    targets.state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    targets.events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    return targets


def success_facts(tmp_path: Path):
    state, events = success_history()
    targets = write_history(tmp_path / "history", state, events)
    snapshot = load_persisted_terminal_snapshot(workflow(), targets)
    return build_post_terminal_facts(snapshot)


def failed_facts(tmp_path: Path):
    state, events = failure_history()
    targets = write_history(tmp_path / "failure", state, events)
    snapshot = load_persisted_terminal_snapshot(workflow(), targets)
    return build_post_terminal_facts(snapshot)


def claim_contract_for(facts, output: str = "ORIGINAL BUSINESS OUTPUT 日本語"):
    return PublicationClaimContract(
        schema_version="publication-claims.v1",
        scope="post_terminal_runtime_consistency",
        workflow_id=facts.workflow_id,
        business_output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        post_terminal_facts_sha256=post_terminal_facts_digest(facts),
        asserted_terminal_status="workflow_complete",
    )


def request(
    *,
    model: str = "future-model",
    system_instructions: str = "SYSTEM SECRET INSTRUCTIONS",
    task_instructions: str = "TASK SECRET INSTRUCTIONS",
    allowed_tools: tuple[str, ...] = ("search",),
) -> ModelInvocationRequest:
    return ModelInvocationRequest(
        model=model,
        system_instructions=system_instructions,
        task_instructions=task_instructions,
        allowed_tools=allowed_tools,
    )


def tool(name: str = "search") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="tool description",
        parameters=(
            ToolParameterDefinition(
                name="query",
                description="query description",
                type="string",
                required=True,
            ),
        ),
    )


def plan_for(tmp_path: Path, target=DIRECT_OPENAI_EXECUTION_TARGET):
    facts = success_facts(tmp_path)
    audit = build_publication_readiness_audit_record(
        facts,
        "ORIGINAL BUSINESS OUTPUT 日本語",
    )
    invocation = request()
    tools = (tool(),)
    plan = build_publication_regeneration_plan(
        audit,
        "regen-20260911-01",
        invocation,
        tools,
        target,
    )
    return facts, audit, invocation, tools, plan


def approval_for(
    plan: PublicationRegenerationPlan,
    approval_id: str = "regeneration-approval-1",
) -> PublicationRegenerationApproval:
    return approve_publication_regeneration(
        plan,
        approved_by="human-reviewer",
        approval_id=approval_id,
    )


def claim_fixture(
    tmp_path: Path,
    approval_id: str = "regeneration-approval-1",
):
    _facts, audit, invocation, tools, plan = plan_for(tmp_path)
    approval = approval_for(plan, approval_id)
    claim = build_publication_regeneration_attempt_claim(plan, approval)
    return audit, invocation, tools, plan, approval, claim


def test_non_ready_audit_builds_frozen_plan_with_existing_fingerprints(
    tmp_path: Path,
) -> None:
    facts, audit, invocation, tools, plan = plan_for(tmp_path)

    assert audit.assessment.readiness == "insufficient_evidence"
    assert audit.assessment.reason_codes == ("claim_contract_missing",)
    assert plan.workflow_id == facts.workflow_id
    assert plan.source_audit_sha256 == audit.digest
    assert plan.source_post_terminal_facts_sha256 == facts.digest
    assert plan.source_business_output_sha256 == facts.final_output_sha256
    assert plan.source_readiness == "insufficient_evidence"
    assert plan.source_reason_codes == ("claim_contract_missing",)
    assert plan.provider == DIRECT_OPENAI_EXECUTION_TARGET.provider
    assert plan.execution_target_sha256 == execution_target_fingerprint(
        DIRECT_OPENAI_EXECUTION_TARGET
    )
    assert plan.invocation_request_sha256 == (
        build_model_invocation_execution_fingerprint(
            invocation, tools, DIRECT_OPENAI_EXECUTION_TARGET
        )
    )
    with pytest.raises(FrozenInstanceError):
        plan.regeneration_id = "changed"  # type: ignore[misc]


def test_failed_and_output_mismatch_audits_are_eligible_with_exact_reason_identity(
    tmp_path: Path,
) -> None:
    failed = failed_facts(tmp_path)
    failed_audit = build_publication_readiness_audit_record(failed, None)
    failed_plan = build_publication_regeneration_plan(
        failed_audit,
        "failed-regeneration",
        request(),
        (tool(),),
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    assert failed_plan.source_readiness == "insufficient_evidence"
    assert failed_plan.source_reason_codes == (
        "execution_not_workflow_complete",
        "final_output_missing",
    )
    assert failed_plan.source_business_output_sha256 is None

    facts = success_facts(tmp_path / "mismatch")
    mismatch_audit = build_publication_readiness_audit_record(
        facts,
        "DIFFERENT BUSINESS OUTPUT",
    )
    mismatch_plan = build_publication_regeneration_plan(
        mismatch_audit,
        "mismatch-regeneration",
        request(),
        (tool(),),
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    assert mismatch_plan.source_readiness == "stale_or_inconsistent"
    assert mismatch_plan.source_reason_codes == ("final_output_mismatch",)


def test_ready_source_is_rejected_before_plan_creation(tmp_path: Path) -> None:
    facts = success_facts(tmp_path)
    contract = claim_contract_for(facts)
    audit = build_publication_readiness_audit_record(
        facts,
        "ORIGINAL BUSINESS OUTPUT 日本語",
        claim_contract=contract,
    )

    assert audit.assessment.readiness == "ready"
    with pytest.raises(PublicationRegenerationPlanError) as error:
        build_publication_regeneration_plan(
            audit,
            "ready-regeneration",
            request(),
            (tool(),),
            DIRECT_OPENAI_EXECUTION_TARGET,
        )
    assert str(error.value) == "publication regeneration plan is invalid"
    assert error.value.detail.classification == "source_ready"


def test_mismatching_claim_contract_identity_remains_bound_by_source_audit(
    tmp_path: Path,
) -> None:
    facts = success_facts(tmp_path)
    mismatch = replace(claim_contract_for(facts), workflow_id="other-workflow")
    audit = build_publication_readiness_audit_record(
        facts,
        "ORIGINAL BUSINESS OUTPUT 日本語",
        claim_contract=mismatch,
    )
    plan = build_publication_regeneration_plan(
        audit,
        "contract-mismatch-regeneration",
        request(),
        (tool(),),
        DIRECT_OPENAI_EXECUTION_TARGET,
    )

    assert audit.assessment.reason_codes == ("claim_contract_mismatch",)
    assert audit.evaluated_claim_contract is mismatch
    assert plan.source_audit_sha256 == audit.digest
    assert "other-workflow" not in serialize_publication_regeneration_plan_canonical(
        plan
    )


@pytest.mark.parametrize(
    "changed",
    [
        lambda value: replace(value, source_audit_sha256="a" * 64),
        lambda value: replace(value, source_post_terminal_facts_sha256="b" * 64),
        lambda value: replace(value, source_business_output_sha256="c" * 64),
        lambda value: replace(
            value,
            source_business_output_sha256="d" * 64,
            source_readiness="stale_or_inconsistent",
            source_reason_codes=("final_output_mismatch",),
        ),
        lambda value: replace(value, invocation_request_sha256="e" * 64),
    ],
)
def test_plan_identity_changes_are_digest_bound(
    tmp_path: Path,
    changed,
) -> None:
    _facts, audit, invocation, tools, plan = plan_for(tmp_path)
    changed_plan = changed(plan)
    assert changed_plan.digest != plan.digest
    with pytest.raises(PublicationRegenerationPlanError):
        validate_publication_regeneration_plan(
            changed_plan,
            audit,
            invocation,
            tools,
            DIRECT_OPENAI_EXECUTION_TARGET,
        )


def test_regeneration_id_changes_plan_identity_without_invalidating_binding(
    tmp_path: Path,
) -> None:
    _facts, audit, invocation, tools, plan = plan_for(tmp_path)
    changed_plan = replace(plan, regeneration_id="other-regeneration")

    assert changed_plan.digest != plan.digest
    validate_publication_regeneration_plan(
        changed_plan,
        audit,
        invocation,
        tools,
        DIRECT_OPENAI_EXECUTION_TARGET,
    )


def test_noncanonical_provider_or_target_identity_is_rejected_at_model_boundary(
    tmp_path: Path,
) -> None:
    _facts, _audit, _invocation, _tools, plan = plan_for(tmp_path)

    with pytest.raises(PublicationRegenerationPlanError):
        replace(plan, provider="omniroute")
    with pytest.raises(PublicationRegenerationPlanError):
        replace(plan, execution_target_sha256="f" * 64)


def test_canonical_plan_is_exact_and_contains_no_raw_inputs(tmp_path: Path) -> None:
    _facts, _audit, invocation, tools, plan = plan_for(tmp_path)
    canonical = serialize_publication_regeneration_plan_canonical(plan)
    expected = (
        '{"execution_target_sha256":"f8d7bc1febd55eeb8cd53563b0348a87930836c688dcf930df4a50e3cef744e9",'
        '"invocation_request_sha256":"1f92dff9be787d5f9adb8394eee514009f7d31f1d224d6b84ffd8afc3257637e",'
        '"provider":"openai","regeneration_id":"regen-20260911-01",'
        '"schema_version":"publication-regeneration-plan.v1",'
        '"source_audit_sha256":"6f0d017958a8623c0661663fd1fc9946012c5a9c3ddc4e06a13cbbd6adb9955f",'
        '"source_business_output_sha256":"f5a064be281eea4db190ed7268f4a1e005ca05227654bbfa260a6c5684da743e",'
        '"source_post_terminal_facts_sha256":"cb172fd45106de3fb570752bf142f9bdf507cfdfdcde65f4f71311e2ecce8e08",'
        '"source_readiness":"insufficient_evidence",'
        '"source_reason_codes":["claim_contract_missing"],'
        '"workflow_id":"phase264-workflow"}'
    )

    assert canonical == expected
    assert publication_regeneration_plan_canonical_bytes(plan) == canonical.encode(
        "utf-8"
    )
    assert publication_regeneration_plan_digest(plan) == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    assert plan.digest == (
        "528eecdfe39ec89e22da2a7c6af9358896115320f08df09a93b0546883be3e03"
    )
    assert "SYSTEM SECRET INSTRUCTIONS" not in canonical
    assert "TASK SECRET INSTRUCTIONS" not in canonical
    assert "ORIGINAL BUSINESS OUTPUT 日本語" not in canonical
    assert "request-terminal" not in canonical
    assert "state.json" not in canonical
    assert "credential" not in canonical
    assert json.loads(canonical) == {
        "execution_target_sha256": plan.execution_target_sha256,
        "invocation_request_sha256": plan.invocation_request_sha256,
        "provider": "openai",
        "regeneration_id": "regen-20260911-01",
        "schema_version": "publication-regeneration-plan.v1",
        "source_audit_sha256": plan.source_audit_sha256,
        "source_business_output_sha256": plan.source_business_output_sha256,
        "source_post_terminal_facts_sha256": plan.source_post_terminal_facts_sha256,
        "source_readiness": "insufficient_evidence",
        "source_reason_codes": ["claim_contract_missing"],
        "workflow_id": "phase264-workflow",
    }
    assert invocation.system_instructions not in canonical
    assert invocation.task_instructions not in canonical
    assert tools[0].description not in canonical


def test_plan_validator_rederives_exact_audit_request_tools_and_target(
    tmp_path: Path,
) -> None:
    _facts, audit, invocation, tools, plan = plan_for(tmp_path)

    validate_publication_regeneration_plan(
        plan, audit, invocation, tools, DIRECT_OPENAI_EXECUTION_TARGET
    )
    with pytest.raises(PublicationRegenerationPlanError):
        validate_publication_regeneration_plan(
            plan,
            audit,
            replace(invocation, task_instructions="changed task"),
            tools,
            DIRECT_OPENAI_EXECUTION_TARGET,
        )
    with pytest.raises(PublicationRegenerationPlanError):
        validate_publication_regeneration_plan(
            plan,
            audit,
            invocation,
            (replace(tools[0], description="changed tool"),),
            DIRECT_OPENAI_EXECUTION_TARGET,
        )
    with pytest.raises(PublicationRegenerationPlanError):
        validate_publication_regeneration_plan(
            plan,
            audit,
            invocation,
            tools,
            LOCAL_OMNIROUTE_EXECUTION_TARGET,
        )

    changed_facts = success_facts(tmp_path / "changed-audit")
    changed_audit = build_publication_readiness_audit_record(
        changed_facts,
        "different output",
    )
    with pytest.raises(PublicationRegenerationPlanError):
        validate_publication_regeneration_plan(
            plan,
            changed_audit,
            invocation,
            tools,
            DIRECT_OPENAI_EXECUTION_TARGET,
        )


def test_plan_revalidation_errors_are_generic_and_do_not_echo_sensitive_inputs(
    tmp_path: Path,
) -> None:
    _facts, audit, invocation, tools, plan = plan_for(tmp_path)
    changed_request = replace(
        invocation,
        task_instructions="TASK SECRET REVALIDATION INSTRUCTIONS",
    )

    with pytest.raises(PublicationRegenerationPlanError) as error:
        validate_publication_regeneration_plan(
            plan,
            audit,
            changed_request,
            tools,
            DIRECT_OPENAI_EXECUTION_TARGET,
        )

    assert str(error.value) == "publication regeneration plan is invalid"
    assert "TASK SECRET REVALIDATION INSTRUCTIONS" not in str(error.value)
    assert "ORIGINAL BUSINESS OUTPUT 日本語" not in str(error.value)


def test_request_identity_changes_create_distinct_regeneration_plans(
    tmp_path: Path,
) -> None:
    _facts, audit, invocation, tools, baseline = plan_for(tmp_path)
    runtime_facts = RuntimeFactsSnapshot(
        facts=(
            RuntimeFact(
                key="workflow.status",
                value_kind="enum",
                value="succeeded",
                provenance=RuntimeFactProvenance(
                    origin="persisted_state",
                    workflow_id="phase264-workflow",
                    source_ref="state",
                    source_sha256="a" * 64,
                ),
            ),
        )
    )
    variants = (
        replace(invocation, model="other-model"),
        replace(invocation, system_instructions="other system"),
        replace(invocation, task_instructions="other task"),
        replace(invocation, allowed_tools=("read",)),
        replace(
            invocation,
            upstream_inputs=(
                UpstreamStepOutput(
                    workflow_id="phase264-workflow",
                    step_id="research",
                    step_index=1,
                    employee_id="researcher",
                    output_text="upstream business text",
                ),
            ),
        ),
        replace(invocation, runtime_facts=runtime_facts),
    )

    plans = tuple(
        build_publication_regeneration_plan(
            audit,
            "regen-20260911-01",
            variant,
            tools,
            DIRECT_OPENAI_EXECUTION_TARGET,
        )
        for variant in variants
    )
    assert all(plan.digest != baseline.digest for plan in plans)
    assert len({plan.invocation_request_sha256 for plan in plans}) == len(plans)
    for plan in plans:
        canonical = serialize_publication_regeneration_plan_canonical(plan)
        assert "upstream business text" not in canonical
        assert "other system" not in canonical
        assert "other task" not in canonical


def test_outer_approval_binds_exact_plan_and_is_not_inner_model_approval(
    tmp_path: Path,
) -> None:
    _facts, audit, invocation, tools, plan = plan_for(tmp_path)
    approval = approve_publication_regeneration(
        plan,
        approved_by="human-reviewer",
        approval_id="regeneration-approval-1",
    )

    assert approval.approved is True
    assert approval.regeneration_plan_sha256 == plan.digest
    validate_publication_regeneration_approval(plan, approval)
    inner = approve_model_invocation_execution(
        invocation,
        tools,
        provider="openai",
        approved_by="human-reviewer",
        approval_id="inner-approval",
        execution_target=DIRECT_OPENAI_EXECUTION_TARGET,
    )
    with pytest.raises(PublicationRegenerationApprovalError):
        validate_publication_regeneration_approval(plan, inner)  # type: ignore[arg-type]

    changed_plan = build_publication_regeneration_plan(
        audit,
        "different-regeneration",
        invocation,
        tools,
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    with pytest.raises(PublicationRegenerationApprovalError):
        validate_publication_regeneration_approval(changed_plan, approval)


def test_old_outer_approval_rejects_rebuilt_audit_request_and_target_plans(
    tmp_path: Path,
) -> None:
    facts, audit, invocation, tools, plan = plan_for(tmp_path)
    approval = approve_publication_regeneration(
        plan,
        approved_by="human-reviewer",
        approval_id="regeneration-approval-1",
    )

    changed_audit = build_publication_readiness_audit_record(
        facts,
        "CHANGED BUSINESS OUTPUT",
    )
    changed_request_plan = build_publication_regeneration_plan(
        audit,
        plan.regeneration_id,
        replace(invocation, task_instructions="changed task"),
        tools,
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    changed_audit_plan = build_publication_regeneration_plan(
        changed_audit,
        plan.regeneration_id,
        invocation,
        tools,
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    changed_target_plan = build_publication_regeneration_plan(
        audit,
        plan.regeneration_id,
        invocation,
        tools,
        LOCAL_OMNIROUTE_EXECUTION_TARGET,
    )

    for changed_plan in (
        changed_request_plan,
        changed_audit_plan,
        changed_target_plan,
    ):
        assert changed_plan.digest != plan.digest
        with pytest.raises(PublicationRegenerationApprovalError):
            validate_publication_regeneration_approval(changed_plan, approval)


def test_approval_rejects_false_subclass_coercion_and_empty_metadata(
    tmp_path: Path,
) -> None:
    _facts, _audit, _invocation, _tools, plan = plan_for(tmp_path)
    approval = approve_publication_regeneration(
        plan,
        approved_by="human-reviewer",
        approval_id="regeneration-approval-1",
    )

    with pytest.raises(FrozenInstanceError):
        approval.approved_by = "changed"  # type: ignore[misc]
    with pytest.raises(PublicationRegenerationApprovalError):
        PublicationRegenerationApproval(
            approved=False,  # type: ignore[arg-type]
            regeneration_plan_sha256=plan.digest,
            approved_by="human-reviewer",
            approval_id="id",
        )
    with pytest.raises(PublicationRegenerationApprovalError):
        PublicationRegenerationApproval(
            approved=1,  # type: ignore[arg-type]
            regeneration_plan_sha256=plan.digest,
            approved_by="human-reviewer",
            approval_id="id",
        )
    with pytest.raises(PublicationRegenerationApprovalError):
        PublicationRegenerationApproval(
            approved=True,
            regeneration_plan_sha256="not-a-digest",
            approved_by="human-reviewer",
            approval_id="id",
        )
    with pytest.raises(TypeError):
        PublicationRegenerationApproval(**approval.__dict__, extra="not allowed")
    with pytest.raises(PublicationRegenerationApprovalError):
        PublicationRegenerationApprovalChild(
            approved=True,
            regeneration_plan_sha256=plan.digest,
            approved_by="human-reviewer",
            approval_id="id",
        )
    with pytest.raises(PublicationRegenerationApprovalError):
        approve_publication_regeneration(plan, approved_by="", approval_id="id")
    with pytest.raises(PublicationRegenerationApprovalError):
        approve_publication_regeneration(plan, approved_by="human", approval_id="")
    with pytest.raises(PublicationRegenerationApprovalError):
        approve_publication_regeneration(
            plan,
            approved_by=StringChild("human"),  # type: ignore[arg-type]
            approval_id="id",
        )


def test_plan_model_rejects_subclass_unknown_fields_and_invalid_source_identity(
    tmp_path: Path,
) -> None:
    _facts, _audit, _invocation, _tools, plan = plan_for(tmp_path)

    with pytest.raises(PublicationRegenerationPlanError):
        PublicationRegenerationPlanChild(**plan.__dict__)
    with pytest.raises(TypeError):
        PublicationRegenerationPlan(**plan.__dict__, extra="not allowed")
    with pytest.raises(PublicationRegenerationPlanError):
        replace(plan, schema_version="publication-regeneration-plan.v0")
    with pytest.raises(PublicationRegenerationPlanError):
        replace(plan, source_readiness="ready")
    with pytest.raises(PublicationRegenerationPlanError):
        replace(plan, regeneration_id="not valid")
    with pytest.raises(PublicationRegenerationPlanError):
        replace(plan, workflow_id=StringChild(plan.workflow_id))


def test_control_helpers_make_no_external_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _facts, audit, invocation, tools, _plan = plan_for(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external dependency was called")

    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(time, "time_ns", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)

    built = build_publication_regeneration_plan(
        audit,
        "no-external-regeneration",
        invocation,
        tools,
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    validate_publication_regeneration_plan(
        built, audit, invocation, tools, DIRECT_OPENAI_EXECUTION_TARGET
    )
    approval = approve_publication_regeneration(
        built,
        approved_by="human",
        approval_id="approval",
    )
    validate_publication_regeneration_approval(built, approval)


def test_control_helpers_leave_original_history_and_audit_sidecar_unchanged(
    tmp_path: Path,
) -> None:
    history = tmp_path / "history"
    state, events = success_history()
    targets = write_history(history, state, events)
    facts = build_post_terminal_facts(
        load_persisted_terminal_snapshot(workflow(), targets)
    )
    audit = build_publication_readiness_audit_record(
        facts,
        "ORIGINAL BUSINESS OUTPUT 日本語",
    )
    audit_path = tmp_path / "audit.json"
    persist_publication_readiness_audit(audit_path, audit)
    before_state = targets.state_path.read_bytes()
    before_events = targets.events_path.read_bytes()
    before_audit = audit_path.read_bytes()

    plan = build_publication_regeneration_plan(
        audit,
        "unchanged-lineage",
        request(),
        (tool(),),
        DIRECT_OPENAI_EXECUTION_TARGET,
    )
    approval = approve_publication_regeneration(
        plan,
        approved_by="human",
        approval_id="approval",
    )
    validate_publication_regeneration_approval(plan, approval)

    assert targets.state_path.read_bytes() == before_state
    assert targets.events_path.read_bytes() == before_events
    assert audit_path.read_bytes() == before_audit


def test_plan_digest_and_canonical_bytes_are_stable_in_fresh_process(
    tmp_path: Path,
) -> None:
    _facts, _audit, _invocation, _tools, plan = plan_for(tmp_path)
    source = """
import json
import sys
from ai_office.engine.publication_regeneration import (
    PublicationRegenerationPlan,
    publication_regeneration_plan_canonical_bytes,
    publication_regeneration_plan_digest,
)

value = json.loads(sys.argv[1])
value["source_reason_codes"] = tuple(value["source_reason_codes"])
plan = PublicationRegenerationPlan(**value)
print(publication_regeneration_plan_digest(plan))
print(publication_regeneration_plan_canonical_bytes(plan).decode("utf-8"))
"""
    # Use JSON to avoid importing any source/business values in the child.
    serialized = json.dumps(plan.__dict__)
    result = subprocess.run(
        [sys.executable, "-c", source, serialized],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    digest, canonical = result.stdout.splitlines()
    assert digest == plan.digest
    assert canonical == serialize_publication_regeneration_plan_canonical(plan)


def test_approval_canonical_identity_preserves_phase264_fields_and_semantics(
    tmp_path: Path,
) -> None:
    _audit, _invocation, _tools, plan, approval, _claim = claim_fixture(tmp_path)

    assert tuple(field.name for field in fields(PublicationRegenerationApproval)) == (
        "approved",
        "regeneration_plan_sha256",
        "approved_by",
        "approval_id",
    )
    canonical = serialize_publication_regeneration_approval_canonical(approval)
    expected = (
        '{"approval_id":"regeneration-approval-1","approved":true,'
        f'"approved_by":"human-reviewer","regeneration_plan_sha256":"{plan.digest}"'
        "}"
    )
    assert canonical == expected
    assert publication_regeneration_approval_canonical_bytes(approval) == (
        expected.encode("utf-8")
    )
    assert publication_regeneration_approval_digest(approval) == hashlib.sha256(
        expected.encode("utf-8")
    ).hexdigest()
    assert (
        publication_regeneration_approval_digest(approval)
        == "da7f86e47492062b6acd0170b564e3c1161593585699f57045978bda65db2807"
    )
    assert approval.digest == publication_regeneration_approval_digest(approval)
    validate_publication_regeneration_approval(plan, approval)


def test_attempt_claim_is_exact_frozen_and_contains_only_safe_identity(
    tmp_path: Path,
) -> None:
    _audit, invocation, tools, plan, approval, claim = claim_fixture(tmp_path)

    assert tuple(
        field.name for field in fields(PublicationRegenerationAttemptClaim)
    ) == (
        "schema_version",
        "consumption_key",
        "regeneration_id",
        "regeneration_plan_sha256",
        "regeneration_approval_sha256",
        "approval_id",
        "approved_by",
        "source_audit_sha256",
        "provider",
        "execution_target_sha256",
        "invocation_request_sha256",
        "state",
    )
    assert claim.schema_version == "publication-regeneration-attempt.v1"
    assert claim.state == "claimed"
    assert claim.regeneration_plan_sha256 == plan.digest
    assert claim.regeneration_approval_sha256 == approval.digest
    canonical = serialize_publication_regeneration_attempt_claim_canonical(claim)
    assert canonical == (
        '{"approval_id":"regeneration-approval-1","approved_by":"human-reviewer",'
        '"consumption_key":"8903ff50a60b437fda6d73426bfb02c0c14f27e9b88a692ed6bab4c2228ccb80",'
        '"execution_target_sha256":"f8d7bc1febd55eeb8cd53563b0348a87930836c688dcf930df4a50e3cef744e9",'
        '"invocation_request_sha256":"1f92dff9be787d5f9adb8394eee514009f7d31f1d224d6b84ffd8afc3257637e",'
        '"provider":"openai",'
        '"regeneration_approval_sha256":"da7f86e47492062b6acd0170b564e3c1161593585699f57045978bda65db2807",'
        '"regeneration_id":"regen-20260911-01",'
        '"regeneration_plan_sha256":"528eecdfe39ec89e22da2a7c6af9358896115320f08df09a93b0546883be3e03",'
        '"schema_version":"publication-regeneration-attempt.v1",'
        '"source_audit_sha256":"6f0d017958a8623c0661663fd1fc9946012c5a9c3ddc4e06a13cbbd6adb9955f",'
        '"state":"claimed"}'
    )
    assert publication_regeneration_attempt_claim_canonical_bytes(claim) == (
        canonical.encode("utf-8")
    )
    assert publication_regeneration_attempt_claim_digest(claim) == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    assert (
        publication_regeneration_attempt_claim_digest(claim)
        == "2a50cc28827b02178b691890242d4fcd7b444ce2833bd3ef6017c142de4a8b81"
    )
    assert claim.digest == publication_regeneration_attempt_claim_digest(claim)
    for unsafe in (
        invocation.system_instructions,
        invocation.task_instructions,
        tools[0].description,
        "ORIGINAL BUSINESS OUTPUT 日本語",
        "state.json",
        "credential",
    ):
        assert unsafe not in canonical
    with pytest.raises(FrozenInstanceError):
        claim.state = "other"  # type: ignore[misc]
    with pytest.raises(PublicationRegenerationAttemptClaimError):
        PublicationRegenerationAttemptClaimChild(**claim.__dict__)
    with pytest.raises(TypeError):
        PublicationRegenerationAttemptClaim(**claim.__dict__, extra="unknown")


def test_claim_builder_binds_exact_plan_approval_and_consumption_identity(
    tmp_path: Path,
) -> None:
    _audit, _invocation, _tools, plan, approval, first_claim = claim_fixture(tmp_path)
    different_id_approval = approval_for(plan, "different-approval")
    different_id_claim = build_publication_regeneration_attempt_claim(
        plan,
        different_id_approval,
    )
    assert (
        publication_regeneration_consumption_key(approval)
        == publication_regeneration_consumption_key_from_approval_id(
            approval.approval_id
        )
    )
    assert different_id_claim.consumption_key != first_claim.consumption_key
    assert different_id_claim.digest != first_claim.digest

    changed_plan = replace(plan, regeneration_id="different-regeneration")
    changed_approval = approval_for(changed_plan, approval.approval_id)
    changed_claim = build_publication_regeneration_attempt_claim(
        changed_plan,
        changed_approval,
    )
    assert changed_claim.consumption_key == first_claim.consumption_key
    assert (
        changed_claim.regeneration_plan_sha256
        != first_claim.regeneration_plan_sha256
    )
    assert changed_claim.digest != first_claim.digest

    with pytest.raises(PublicationRegenerationAttemptClaimError):
        build_publication_regeneration_attempt_claim(plan, changed_approval)


def test_claim_builder_makes_no_filesystem_provider_network_env_or_clock_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _audit, _invocation, _tools, plan, approval, _claim = claim_fixture(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("external dependency was called")

    monkeypatch.setattr(publication_regeneration_module.os, "getenv", forbidden)
    monkeypatch.setattr(publication_regeneration_module.os, "open", forbidden)
    monkeypatch.setattr(publication_regeneration_module.os, "fsync", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(time, "time_ns", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    built = build_publication_regeneration_attempt_claim(plan, approval)
    assert built == _claim


def test_claim_path_requires_existing_caller_directory_and_hides_raw_approval_id(
    tmp_path: Path,
) -> None:
    _audit, _invocation, _tools, plan, approval, claim = claim_fixture(tmp_path)
    missing = tmp_path / "missing-ledger"
    with pytest.raises(PublicationRegenerationAttemptClaimPersistenceError):
        publication_regeneration_attempt_claim_path(missing, claim.consumption_key)

    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = publication_regeneration_attempt_claim_path(
        ledger,
        publication_regeneration_consumption_key(approval),
    )
    assert marker.parent == ledger
    assert marker.name == f"{claim.consumption_key}.json"
    assert approval.approval_id not in marker.name
    assert not marker.exists()
    claim_publication_regeneration_attempt(ledger, plan, approval)
    assert marker.read_bytes() == (
        publication_regeneration_attempt_claim_canonical_bytes(claim)
    )


def test_first_claim_is_durable_exclusive_create_and_identical_replay_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _audit, _invocation, _tools, plan, approval, claim = claim_fixture(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    fsync_calls: list[int] = []
    original_fsync = publication_regeneration_module.os.fsync

    def record_fsync(descriptor: int) -> None:
        fsync_calls.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(publication_regeneration_module.os, "fsync", record_fsync)
    created = claim_publication_regeneration_attempt(ledger, plan, approval)
    marker = ledger / f"{claim.consumption_key}.json"
    before = marker.read_bytes()
    assert created == claim
    assert before == publication_regeneration_attempt_claim_canonical_bytes(claim)
    assert len(fsync_calls) == 2

    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError) as error:
        claim_publication_regeneration_attempt(ledger, plan, approval)
    assert error.value.detail.classification == "already_consumed"
    assert marker.read_bytes() == before


def test_fresh_process_rejects_second_claim_from_same_authoritative_ledger(
    tmp_path: Path,
) -> None:
    _audit, _invocation, _tools, plan, approval, _claim = claim_fixture(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    claim_publication_regeneration_attempt(ledger, plan, approval)
    source = """
import json
import sys
from pathlib import Path
from ai_office.engine.publication_regeneration import (
    PublicationRegenerationApproval,
    PublicationRegenerationAttemptAlreadyConsumedError,
    PublicationRegenerationPlan,
    claim_publication_regeneration_attempt,
)

plan_value = json.loads(sys.argv[2])
plan_value["source_reason_codes"] = tuple(plan_value["source_reason_codes"])
plan = PublicationRegenerationPlan(**plan_value)
approval = PublicationRegenerationApproval(**json.loads(sys.argv[3]))
try:
    claim_publication_regeneration_attempt(Path(sys.argv[1]), plan, approval)
except PublicationRegenerationAttemptAlreadyConsumedError as error:
    print(error.detail.classification)
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            source,
            str(ledger),
            json.dumps(plan.__dict__),
            json.dumps(approval.__dict__),
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        env={"PYTHONPATH": str(Path.cwd() / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "already_consumed"


@pytest.mark.parametrize(
    "existing",
    [b"", b"truncated", b"not-json", b"different", b"\\xff"],
)
def test_existing_corrupt_or_truncated_marker_rejects_reuse_and_remains_unchanged(
    tmp_path: Path,
    existing: bytes,
) -> None:
    _audit, _invocation, _tools, plan, approval, claim = claim_fixture(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{claim.consumption_key}.json"
    marker.write_bytes(existing)
    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        claim_publication_regeneration_attempt(ledger, plan, approval)
    assert marker.read_bytes() == existing


def test_different_plan_with_same_approval_id_is_blocked_by_same_ledger_key(
    tmp_path: Path,
) -> None:
    _audit, _invocation, _tools, plan, approval, first_claim = claim_fixture(tmp_path)
    changed_plan = replace(plan, regeneration_id="different-regeneration")
    changed_approval = approval_for(changed_plan, approval.approval_id)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    claim_publication_regeneration_attempt(ledger, plan, approval)
    marker = ledger / f"{first_claim.consumption_key}.json"
    before = marker.read_bytes()
    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        claim_publication_regeneration_attempt(ledger, changed_plan, changed_approval)
    assert marker.read_bytes() == before


def test_write_or_fsync_ambiguity_leaves_marker_and_never_returns_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _audit, _invocation, _tools, plan, approval, claim = claim_fixture(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated durability failure")

    monkeypatch.setattr(publication_regeneration_module.os, "fsync", fail_fsync)
    with pytest.raises(PublicationRegenerationAttemptClaimPersistenceError) as error:
        claim_publication_regeneration_attempt(ledger, plan, approval)
    assert error.value.detail.classification == "ambiguous"
    marker = ledger / f"{claim.consumption_key}.json"
    assert marker.exists()
    assert marker.read_bytes() == (
        publication_regeneration_attempt_claim_canonical_bytes(claim)
    )
    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        claim_publication_regeneration_attempt(ledger, plan, approval)


def test_write_failure_after_exclusive_create_is_ambiguous_and_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _audit, _invocation, _tools, plan, approval, claim = claim_fixture(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{claim.consumption_key}.json"
    canonical = publication_regeneration_attempt_claim_canonical_bytes(claim)
    original_open = Path.open
    exclusive_create_succeeded = False

    class FailingWriteHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def __enter__(self) -> FailingWriteHandle:
            self.handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self.handle.__exit__(*args)  # type: ignore[attr-defined]

        def write(self, contents: bytes) -> int:
            self.handle.write(contents[:1])  # type: ignore[attr-defined]
            raise OSError("simulated write failure")

        def flush(self) -> None:
            raise AssertionError("write failure must happen before flush")

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

    def fail_write(path: Path, *args: object, **kwargs: object) -> object:
        nonlocal exclusive_create_succeeded
        handle = original_open(path, *args, **kwargs)
        if path == marker and args and args[0] == "xb":
            exclusive_create_succeeded = True
            return FailingWriteHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", fail_write)
    with pytest.raises(
        PublicationRegenerationAttemptClaimPersistenceError
    ) as error:
        claim_publication_regeneration_attempt(ledger, plan, approval)
    assert exclusive_create_succeeded is True
    assert error.value.detail.classification == "ambiguous"
    assert marker.exists()
    assert marker.read_bytes() == canonical[:1]
    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        claim_publication_regeneration_attempt(ledger, plan, approval)


def test_flush_failure_after_write_is_ambiguous_and_consumed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _audit, _invocation, _tools, plan, approval, claim = claim_fixture(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{claim.consumption_key}.json"
    canonical = publication_regeneration_attempt_claim_canonical_bytes(claim)
    original_open = Path.open
    exclusive_create_succeeded = False

    class FailingFlushHandle:
        def __init__(self, handle: object) -> None:
            self.handle = handle

        def __enter__(self) -> FailingFlushHandle:
            self.handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            self.handle.close()  # type: ignore[attr-defined]
            return False

        def write(self, contents: bytes) -> int:
            return self.handle.write(contents)  # type: ignore[attr-defined]

        def flush(self) -> None:
            raise OSError("simulated flush failure")

        def fileno(self) -> int:
            return self.handle.fileno()  # type: ignore[attr-defined]

    def fail_flush(path: Path, *args: object, **kwargs: object) -> object:
        nonlocal exclusive_create_succeeded
        handle = original_open(path, *args, **kwargs)
        if path == marker and args and args[0] == "xb":
            exclusive_create_succeeded = True
            return FailingFlushHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", fail_flush)
    with pytest.raises(
        PublicationRegenerationAttemptClaimPersistenceError
    ) as error:
        claim_publication_regeneration_attempt(ledger, plan, approval)
    assert exclusive_create_succeeded is True
    assert error.value.detail.classification == "ambiguous"
    assert marker.exists()
    assert marker.read_bytes() == canonical
    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        claim_publication_regeneration_attempt(ledger, plan, approval)


def test_parent_directory_fsync_failure_leaves_canonical_consumed_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _audit, _invocation, _tools, plan, approval, claim = claim_fixture(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{claim.consumption_key}.json"
    canonical = publication_regeneration_attempt_claim_canonical_bytes(claim)
    original_fsync = publication_regeneration_module.os.fsync
    fsync_calls = 0

    def fail_parent_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 1:
            original_fsync(descriptor)
            return
        raise OSError("simulated parent-directory fsync failure")

    monkeypatch.setattr(
        publication_regeneration_module.os,
        "fsync",
        fail_parent_fsync,
    )
    with pytest.raises(
        PublicationRegenerationAttemptClaimPersistenceError
    ) as error:
        claim_publication_regeneration_attempt(ledger, plan, approval)
    assert fsync_calls == 2
    assert error.value.detail.classification == "ambiguous"
    assert marker.exists()
    assert marker.read_bytes() == canonical
    with pytest.raises(PublicationRegenerationAttemptAlreadyConsumedError):
        claim_publication_regeneration_attempt(ledger, plan, approval)


def test_strict_claim_loader_accepts_exact_record_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    _audit, _invocation, _tools, plan, approval, claim = claim_fixture(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{claim.consumption_key}.json"
    marker.write_bytes(publication_regeneration_attempt_claim_canonical_bytes(claim))
    assert load_publication_regeneration_attempt_claim(marker) == claim

    valid = json.loads(marker.read_text(encoding="utf-8"))
    invalid_contents = (
        marker.read_bytes() + b"\n",
        json.dumps({**valid, "unknown": "field"}, separators=(",", ":")).encode(),
        json.dumps(
            {key: value for key, value in valid.items() if key != "state"},
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {**valid, "approved_by": "tampered"}, separators=(",", ":")
        ).encode(),
        b"\xff",
        b"{",
    )
    original = marker.read_bytes()
    for contents in invalid_contents:
        marker.write_bytes(contents)
        with pytest.raises(PublicationRegenerationAttemptClaimLoadError):
            load_publication_regeneration_attempt_claim(marker)
    marker.write_bytes(original)
    assert load_publication_regeneration_attempt_claim(marker) == claim


def test_strict_claim_loader_rejects_duplicate_keys_without_repair_or_delete(
    tmp_path: Path,
) -> None:
    _audit, _invocation, _tools, plan, approval, claim = claim_fixture(tmp_path)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    marker = ledger / f"{claim.consumption_key}.json"
    canonical = serialize_publication_regeneration_attempt_claim_canonical(claim)
    duplicate = canonical[:-1] + ',"state":"claimed"}'
    marker.write_bytes(duplicate.encode("utf-8"))
    before = marker.read_bytes()
    with pytest.raises(PublicationRegenerationAttemptClaimLoadError):
        load_publication_regeneration_attempt_claim(marker)
    assert marker.read_bytes() == before
