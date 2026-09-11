"""Focused provider-free tests for Phase 261/262/263 post-terminal evidence."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from ai_office.definitions.workflow import WorkflowDefinition
from ai_office.engine.post_terminal_facts import (
    PersistedTerminalSnapshot,
    PersistedTerminalSnapshotError,
    PostTerminalFactsError,
    PublicationClaimContract,
    PublicationClaimContractError,
    PublicationReadinessAssessment,
    PublicationReadinessAuditConflictError,
    PublicationReadinessAuditError,
    PublicationReadinessAuditLoadError,
    PublicationReadinessAuditRecord,
    PublicationReadinessError,
    assess_terminal_publication_readiness,
    build_post_terminal_facts,
    build_publication_readiness_audit_record,
    load_persisted_terminal_snapshot,
    load_publication_readiness_audit,
    persist_publication_readiness_audit,
    post_terminal_facts_digest,
    publication_claim_contract_canonical_bytes,
    publication_claim_contract_digest,
    publication_readiness_assessment_digest,
    publication_readiness_audit_canonical_bytes,
    publication_readiness_audit_digest,
    serialize_post_terminal_facts_canonical,
    serialize_publication_claim_contract_canonical,
    serialize_publication_readiness_assessment_canonical,
    serialize_publication_readiness_audit_canonical,
    validate_publication_claim_contract,
)
from ai_office.runtime import RuntimeStepEvent, WorkflowExecutionState
from ai_office.storage import (
    WorkflowExecutionPersistenceTargets,
    serialize_runtime_step_event_jsonl,
    serialize_workflow_execution_state_json,
)


class StringChild(str):
    pass


class PublicationClaimContractChild(PublicationClaimContract):
    pass


class PublicationReadinessAuditRecordChild(PublicationReadinessAuditRecord):
    pass


def workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "phase261-workflow",
            "name": "Phase 261 workflow",
            "description": "post-terminal facts fixture",
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


def dogfood_workflow() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "phase262-dogfood-workflow",
            "name": "Phase 262 dogfood workflow",
            "description": "four-step publication consistency fixture",
            "steps": [
                {
                    "id": "research",
                    "name": "Research",
                    "employee": "researcher",
                    "instructions": "Research.",
                },
                {
                    "id": "draft",
                    "name": "Draft",
                    "employee": "writer",
                    "instructions": "Draft.",
                },
                {
                    "id": "review",
                    "name": "Review",
                    "employee": "reviewer",
                    "instructions": "Review.",
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


def success_history(output: str = "FINAL 日本語 😀") -> tuple[
    WorkflowExecutionState, tuple[RuntimeStepEvent, ...]
]:
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
            response_id="response-terminal-secret-like",
            request_id="request-terminal-secret-like",
            output_text=output,
            message=None,
        ),
    )
    return state, events


def dogfood_success_history(
    output: str = "FINAL DOGFOOD ARTICLE",
) -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
    definition = dogfood_workflow()
    step_identity = (
        ("research", "researcher"),
        ("draft", "writer"),
        ("review", "reviewer"),
        ("publish", "editor"),
    )
    state = WorkflowExecutionState(
        workflow_id=definition.id,
        status="succeeded",
        current_step_id="publish",
        current_step_index=4,
        current_employee_id="editor",
        completed_step_ids=tuple(step_id for step_id, _employee in step_identity),
        last_failure_category=None,
    )
    events = tuple(
        RuntimeStepEvent(
            event_type="step_succeeded",
            workflow_id=definition.id,
            step_id=step_id,
            step_index=step_index,
            employee_id=employee_id,
            previous_status="running",
            next_status="succeeded",
            provider=f"provider-{step_index}",
            failure_category=None,
            response_id=f"response-{step_index}",
            request_id=f"request-{step_index}",
            output_text=output if step_index == 4 else f"intermediate-{step_index}",
            message=None,
        )
        for step_index, (step_id, employee_id) in enumerate(step_identity, 1)
    )
    return state, events


def failed_history() -> tuple[WorkflowExecutionState, tuple[RuntimeStepEvent, ...]]:
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
        request_id="request-secret-like",
        output_text=None,
        message="safe failure",
    )
    return state, (event,)


def write_history(
    tmp_path: Path,
    state: WorkflowExecutionState,
    events: tuple[RuntimeStepEvent, ...],
) -> WorkflowExecutionPersistenceTargets:
    targets = WorkflowExecutionPersistenceTargets(
        state_path=tmp_path / "state.json",
        events_path=tmp_path / "events.jsonl",
    )
    targets.state_path.write_text(
        serialize_workflow_execution_state_json(state), encoding="utf-8"
    )
    targets.events_path.write_text(
        "".join(serialize_runtime_step_event_jsonl(event) for event in events),
        encoding="utf-8",
    )
    return targets


def load_facts(
    tmp_path: Path,
    *,
    output: str = "FINAL 日本語 😀",
):
    state, events = success_history(output)
    targets = write_history(tmp_path, state, events)
    snapshot = load_persisted_terminal_snapshot(workflow(), targets)
    return targets, snapshot, build_post_terminal_facts(snapshot)


def claim_contract_for(
    facts,
    *,
    output: str = "FINAL 日本語 😀",
    workflow_id: str | None = None,
    business_output_sha256: str | None = None,
    post_terminal_facts_sha256: str | None = None,
) -> PublicationClaimContract:
    return PublicationClaimContract(
        schema_version="publication-claims.v1",
        scope="post_terminal_runtime_consistency",
        workflow_id=workflow_id or facts.workflow_id,
        business_output_sha256=business_output_sha256
        or hashlib.sha256(output.encode("utf-8")).hexdigest(),
        post_terminal_facts_sha256=post_terminal_facts_sha256
        or post_terminal_facts_digest(facts),
        asserted_terminal_status="workflow_complete",
    )


def test_fresh_snapshot_uses_exact_source_bytes_and_terminal_event_identity(
    tmp_path: Path,
) -> None:
    targets, snapshot, facts = load_facts(tmp_path)

    assert snapshot.terminal_status == "workflow_complete"
    assert snapshot.terminal_reason == "last_step_succeeded"
    assert snapshot.state_sha256 == hashlib.sha256(
        targets.state_path.read_bytes()
    ).hexdigest()
    assert snapshot.events_sha256 == hashlib.sha256(
        targets.events_path.read_bytes()
    ).hexdigest()
    assert facts.workflow_id == "phase261-workflow"
    assert facts.terminal_step_id == "publish"
    assert facts.terminal_step_index == 2
    assert facts.terminal_employee_id == "editor"
    assert facts.terminal_provider == "terminal-provider"
    assert facts.completed_step_ids == ("research", "publish")
    assert facts.final_output_sha256 == hashlib.sha256(
        "FINAL 日本語 😀".encode()
    ).hexdigest()


def test_facts_omit_raw_output_request_response_path_and_diagnostics(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    canonical = serialize_post_terminal_facts_canonical(facts)

    assert "FINAL 日本語 😀" not in canonical
    assert "response-terminal-secret-like" not in canonical
    assert "request-terminal-secret-like" not in canonical
    assert "state.json" not in canonical
    assert "diagnostic" not in canonical
    assert set(json.loads(canonical)) == {
        "completed_step_ids",
        "events_sha256",
        "final_output_sha256",
        "schema_version",
        "state_sha256",
        "terminal_employee_id",
        "terminal_provider",
        "terminal_reason",
        "terminal_status",
        "terminal_step_id",
        "terminal_step_index",
        "workflow_id",
    }


def test_exact_output_match_is_insufficient_evidence_not_ready(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)

    assessment = assess_terminal_publication_readiness(facts, "FINAL 日本語 😀")

    assert assessment.execution_status == "workflow_complete"
    assert assessment.readiness == "insufficient_evidence"
    assert assessment.reason_codes == ("claim_contract_missing",)
    assert assessment.claim_contract_sha256 is None
    assert assessment.business_output_sha256 == facts.final_output_sha256
    assert "ready" not in assessment.reason_codes


def test_publication_claim_contract_is_explicit_canonical_and_deterministic(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)

    canonical = serialize_publication_claim_contract_canonical(contract)
    assert canonical == json.dumps(
        {
            "asserted_terminal_status": "workflow_complete",
            "business_output_sha256": facts.final_output_sha256,
            "post_terminal_facts_sha256": facts.digest,
            "schema_version": "publication-claims.v1",
            "scope": "post_terminal_runtime_consistency",
            "workflow_id": "phase261-workflow",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert canonical == (
        '{"asserted_terminal_status":"workflow_complete",'
        '"business_output_sha256":"075d0db2e91e812458d6769792b43c2c0d64bcf77a3203fd94ba8c84a8f39790",'
        '"post_terminal_facts_sha256":"967c35093c4ac3ece19a06d8f1878c73b457ad9329a026359fd225a32dc49a13",'
        '"schema_version":"publication-claims.v1",'
        '"scope":"post_terminal_runtime_consistency",'
        '"workflow_id":"phase261-workflow"}'
    )
    assert publication_claim_contract_canonical_bytes(contract) == canonical.encode(
        "utf-8"
    )
    assert contract.digest == publication_claim_contract_digest(contract)
    assert contract.digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert contract.digest == (
        "bdb60d1beea78d2a90b26c3589a26833c174a0e796b86d5cff3e807823da91b9"
    )
    assert replace(contract) == contract


def test_publication_claim_contract_digest_binds_identity_fields(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)
    variants = (
        replace(contract, workflow_id="other-workflow"),
        replace(contract, business_output_sha256="a" * 64),
        replace(contract, post_terminal_facts_sha256="b" * 64),
    )

    assert len({contract.digest, *(variant.digest for variant in variants)}) == 4
    with pytest.raises(PublicationClaimContractError):
        replace(contract, asserted_terminal_status="persisted_failure")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "publication-claims.v0"),
        ("scope", "arbitrary_scope"),
        ("workflow_id", ""),
        ("business_output_sha256", "A" * 64),
        ("post_terminal_facts_sha256", "B" * 64),
        ("asserted_terminal_status", "persisted_failure"),
    ],
)
def test_publication_claim_contract_rejects_invalid_fields(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)

    with pytest.raises(PublicationClaimContractError):
        replace(contract, **{field: value})


def test_publication_claim_contract_rejects_subclass_and_unknown_fields(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)

    with pytest.raises(PublicationClaimContractError):
        replace(contract, workflow_id=StringChild(contract.workflow_id))
    with pytest.raises(PublicationClaimContractError):
        PublicationClaimContractChild(
            schema_version=contract.schema_version,
            scope=contract.scope,
            workflow_id=contract.workflow_id,
            business_output_sha256=contract.business_output_sha256,
            post_terminal_facts_sha256=contract.post_terminal_facts_sha256,
            asserted_terminal_status=contract.asserted_terminal_status,
        )
    with pytest.raises(TypeError):
        PublicationClaimContract(**contract.__dict__, extra="not allowed")


def test_exact_publication_claim_contract_validates_against_output_and_facts(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)

    assert validate_publication_claim_contract(
        contract,
        facts,
        facts.final_output_sha256,
    ) is None


@pytest.mark.parametrize(
    ("field", "value", "classification"),
    [
        ("workflow_id", "other-workflow", "workflow_mismatch"),
        ("business_output_sha256", "a" * 64, "business_output_mismatch"),
        ("post_terminal_facts_sha256", "b" * 64, "post_terminal_facts_mismatch"),
    ],
)
def test_publication_claim_contract_mismatch_is_safe_and_deterministic(
    tmp_path: Path,
    field: str,
    value: str,
    classification: str,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = replace(claim_contract_for(facts), **{field: value})

    with pytest.raises(PublicationClaimContractError) as caught:
        validate_publication_claim_contract(
            contract,
            facts,
            facts.final_output_sha256,
        )

    assert caught.value.detail.classification == classification
    assert str(caught.value) == "post-terminal evidence is invalid"
    assert "FINAL 日本語 😀" not in str(caught.value)


def test_exact_verified_claim_contract_reaches_scope_limited_ready(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)

    assessment = assess_terminal_publication_readiness(
        facts,
        "FINAL 日本語 😀",
        claim_contract=contract,
    )

    assert assessment.readiness == "ready"
    assert assessment.reason_codes == ()
    assert assessment.claim_contract is contract
    assert assessment.claim_contract_sha256 == contract.digest
    canonical = serialize_publication_readiness_assessment_canonical(assessment)
    assert json.loads(canonical)["claim_contract"] == json.loads(
        serialize_publication_claim_contract_canonical(contract)
    )
    assert "FINAL 日本語 😀" not in canonical


def test_public_readiness_model_accepts_only_exact_bound_verified_contract(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)

    assessment = PublicationReadinessAssessment(
        schema_version="publication-readiness.v1",
        execution_status="workflow_complete",
        readiness="ready",
        reason_codes=(),
        post_terminal_facts=facts,
        business_output_sha256=facts.final_output_sha256,
        claim_contract_sha256=contract.digest,
        claim_contract=contract,
    )

    assert assessment.readiness == "ready"
    assert assessment.claim_contract is contract


def test_contract_mismatch_is_stale_or_inconsistent_without_field_disclosure(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = replace(claim_contract_for(facts), workflow_id="other-workflow")

    assessment = assess_terminal_publication_readiness(
        facts,
        "FINAL 日本語 😀",
        claim_contract=contract,
    )

    assert assessment.readiness == "stale_or_inconsistent"
    assert assessment.reason_codes == ("claim_contract_mismatch",)
    assert "other-workflow" not in serialize_publication_readiness_assessment_canonical(
        assessment
    )


def test_contract_does_not_override_output_mismatch_precedence(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)

    assessment = assess_terminal_publication_readiness(
        facts,
        "different output",
        claim_contract=contract,
    )

    assert assessment.readiness == "stale_or_inconsistent"
    assert assessment.reason_codes == ("final_output_mismatch",)


def test_four_step_dogfood_transition_requires_explicit_verified_contract(
    tmp_path: Path,
) -> None:
    output = "FINAL DOGFOOD ARTICLE"
    state, events = dogfood_success_history(output)
    targets = write_history(tmp_path, state, events)
    before = targets.state_path.read_bytes(), targets.events_path.read_bytes()
    snapshot = load_persisted_terminal_snapshot(dogfood_workflow(), targets)
    facts = build_post_terminal_facts(snapshot)
    contract = claim_contract_for(facts, output=output)

    without_contract = assess_terminal_publication_readiness(facts, output)
    ready = assess_terminal_publication_readiness(
        facts,
        output,
        claim_contract=contract,
    )
    different_output = assess_terminal_publication_readiness(
        facts,
        "FINAL DOGFOOD ARTICLE CHANGED",
        claim_contract=contract,
    )
    different_facts = assess_terminal_publication_readiness(
        replace(facts, state_sha256="c" * 64),
        output,
        claim_contract=contract,
    )
    different_workflow = assess_terminal_publication_readiness(
        replace(facts, workflow_id="other-workflow"),
        output,
        claim_contract=contract,
    )

    assert facts.terminal_step_index == 4
    assert facts.completed_step_ids == ("research", "draft", "review", "publish")
    assert without_contract.readiness == "insufficient_evidence"
    assert without_contract.reason_codes == ("claim_contract_missing",)
    assert ready.readiness == "ready"
    assert ready.claim_contract_sha256 == contract.digest
    assert different_output.readiness == "stale_or_inconsistent"
    assert different_output.reason_codes == ("final_output_mismatch",)
    assert different_facts.readiness == "stale_or_inconsistent"
    assert different_facts.reason_codes == ("claim_contract_mismatch",)
    assert different_workflow.readiness == "stale_or_inconsistent"
    assert different_workflow.reason_codes == ("claim_contract_mismatch",)
    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_ready_assessment_without_claim_contract_is_rejected_at_model_boundary(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)

    with pytest.raises(PublicationReadinessError):
        PublicationReadinessAssessment(
            schema_version="publication-readiness.v1",
            execution_status="workflow_complete",
            readiness="ready",
            reason_codes=("claim_contract_missing",),
            post_terminal_facts=facts,
            business_output_sha256=facts.final_output_sha256,
            claim_contract_sha256=None,
        )


def test_forged_ready_with_arbitrary_claim_digest_is_rejected(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)

    with pytest.raises(PublicationReadinessError):
        PublicationReadinessAssessment(
            schema_version="publication-readiness.v1",
            execution_status="workflow_complete",
            readiness="ready",
            reason_codes=(),
            post_terminal_facts=facts,
            business_output_sha256=facts.final_output_sha256,
            claim_contract_sha256="f" * 64,
        )


def test_forged_ready_with_mismatched_contract_is_rejected(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = replace(claim_contract_for(facts), workflow_id="other-workflow")

    with pytest.raises(PublicationReadinessError):
        PublicationReadinessAssessment(
            schema_version="publication-readiness.v1",
            execution_status="workflow_complete",
            readiness="ready",
            reason_codes=(),
            post_terminal_facts=facts,
            business_output_sha256=facts.final_output_sha256,
            claim_contract_sha256=contract.digest,
            claim_contract=contract,
        )


def test_supplied_claim_contract_cannot_upgrade_persisted_failure(
    tmp_path: Path,
) -> None:
    state, events = failed_history()
    targets = write_history(tmp_path, state, events)
    snapshot = load_persisted_terminal_snapshot(workflow(), targets)
    facts = build_post_terminal_facts(snapshot)
    contract = PublicationClaimContract(
        schema_version="publication-claims.v1",
        scope="post_terminal_runtime_consistency",
        workflow_id=facts.workflow_id,
        business_output_sha256="a" * 64,
        post_terminal_facts_sha256=facts.digest,
        asserted_terminal_status="workflow_complete",
    )

    with pytest.raises(PublicationClaimContractError) as caught:
        validate_publication_claim_contract(contract, facts, "a" * 64)
    assert caught.value.detail.classification == "terminal_status_mismatch"

    assessment = assess_terminal_publication_readiness(
        facts,
        "candidate",
        claim_contract=contract,
    )

    assert assessment.readiness == "insufficient_evidence"
    assert assessment.reason_codes == (
        "execution_not_workflow_complete",
        "final_output_missing",
    )
    assert assessment.claim_contract is None
    assert assessment.claim_contract_sha256 is None


def test_audit_record_builds_without_claim_and_omits_sensitive_values(
    tmp_path: Path,
) -> None:
    targets, _snapshot, facts = load_facts(tmp_path)

    record = build_publication_readiness_audit_record(facts, "FINAL 日本語 😀")
    canonical = serialize_publication_readiness_audit_canonical(record)
    value = json.loads(canonical)

    assert record.post_terminal_facts is facts
    assert record.evaluated_claim_contract is None
    assert record.assessment.readiness == "insufficient_evidence"
    assert record.assessment.reason_codes == ("claim_contract_missing",)
    assert value["evaluated_claim_contract"] is None
    assert value["evaluated_claim_contract_sha256"] is None
    assert value["post_terminal_facts_sha256"] == facts.digest
    assert value["assessment_sha256"] == record.assessment.digest
    assert set(value) == {
        "assessment",
        "assessment_sha256",
        "evaluated_claim_contract",
        "evaluated_claim_contract_sha256",
        "post_terminal_facts",
        "post_terminal_facts_sha256",
        "schema_version",
    }
    assert "FINAL 日本語 😀" not in canonical
    assert "response-terminal-secret-like" not in canonical
    assert "request-terminal-secret-like" not in canonical
    assert str(targets.state_path) not in canonical
    assert "approval" not in canonical
    assert "timestamp" not in canonical


def test_audit_builder_uses_no_filesystem_environment_clock_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("forbidden external dependency was called")

    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(time, "time_ns", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)

    record = build_publication_readiness_audit_record(facts, "FINAL 日本語 😀")

    assert record.assessment.reason_codes == ("claim_contract_missing",)


def test_ready_audit_record_binds_exact_contract_and_assessment(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)

    record = build_publication_readiness_audit_record(
        facts,
        "FINAL 日本語 😀",
        claim_contract=contract,
    )

    assert record.assessment.readiness == "ready"
    assert record.evaluated_claim_contract is contract
    assert record.assessment.claim_contract is contract
    assert record.evaluated_claim_contract_sha256 == contract.digest
    assert json.loads(serialize_publication_readiness_audit_canonical(record))[
        "assessment"
    ]["claim_contract"] == json.loads(
        serialize_publication_claim_contract_canonical(contract)
    )


def test_mismatching_contract_identity_is_retained_only_in_audit_metadata(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = replace(claim_contract_for(facts), workflow_id="other-workflow")

    record = build_publication_readiness_audit_record(
        facts,
        "FINAL 日本語 😀",
        claim_contract=contract,
    )
    canonical = serialize_publication_readiness_audit_canonical(record)
    value = json.loads(canonical)

    assert record.assessment.readiness == "stale_or_inconsistent"
    assert record.assessment.reason_codes == ("claim_contract_mismatch",)
    assert record.assessment.claim_contract is None
    assert record.evaluated_claim_contract is contract
    assert value["evaluated_claim_contract"] == json.loads(
        serialize_publication_claim_contract_canonical(contract)
    )
    assert value["evaluated_claim_contract_sha256"] == contract.digest
    assert "FINAL 日本語 😀" not in canonical

    other_contract = replace(contract, business_output_sha256="a" * 64)
    other_record = build_publication_readiness_audit_record(
        facts,
        "FINAL 日本語 😀",
        claim_contract=other_contract,
    )
    assert other_record.assessment.reason_codes == ("claim_contract_mismatch",)
    assert other_record.digest != record.digest


def test_output_mismatch_and_persisted_failure_retain_supplied_contract_safely(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)
    output_mismatch = build_publication_readiness_audit_record(
        facts,
        "different output",
        claim_contract=contract,
    )

    state, events = failed_history()
    failure_path = tmp_path / "failure"
    failure_path.mkdir()
    failure_targets = write_history(failure_path, state, events)
    failure_facts = build_post_terminal_facts(
        load_persisted_terminal_snapshot(workflow(), failure_targets)
    )
    failure_contract = PublicationClaimContract(
        schema_version="publication-claims.v1",
        scope="post_terminal_runtime_consistency",
        workflow_id=failure_facts.workflow_id,
        business_output_sha256="a" * 64,
        post_terminal_facts_sha256=failure_facts.digest,
        asserted_terminal_status="workflow_complete",
    )
    failure = build_publication_readiness_audit_record(
        failure_facts,
        "candidate",
        claim_contract=failure_contract,
    )

    assert output_mismatch.assessment.reason_codes == ("final_output_mismatch",)
    assert output_mismatch.evaluated_claim_contract is contract
    assert output_mismatch.assessment.claim_contract is None
    assert failure.assessment.readiness == "insufficient_evidence"
    assert failure.assessment.reason_codes == (
        "execution_not_workflow_complete",
        "final_output_missing",
    )
    assert failure.evaluated_claim_contract is failure_contract
    assert failure.assessment.claim_contract is None


def test_audit_record_rejects_forged_nested_facts_or_contract(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    contract = claim_contract_for(facts)
    record = build_publication_readiness_audit_record(
        facts,
        "FINAL 日本語 😀",
        claim_contract=contract,
    )

    with pytest.raises(PublicationReadinessAuditError):
        replace(
            record,
            post_terminal_facts=replace(facts, state_sha256="a" * 64),
        )
    with pytest.raises(PublicationReadinessAuditError):
        replace(
            record,
            evaluated_claim_contract=replace(contract, workflow_id="other-workflow"),
        )
    with pytest.raises(PublicationReadinessAuditError):
        PublicationReadinessAuditRecordChild(
            schema_version=record.schema_version,
            post_terminal_facts=record.post_terminal_facts,
            evaluated_claim_contract=record.evaluated_claim_contract,
            assessment=record.assessment,
        )
    with pytest.raises(TypeError):
        PublicationReadinessAuditRecord(
            **record.__dict__,
            extra="not allowed",
        )


def test_audit_canonical_fixture_is_deterministic_and_digest_bound(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    record = build_publication_readiness_audit_record(facts, "FINAL 日本語 😀")

    first = serialize_publication_readiness_audit_canonical(record)
    second = serialize_publication_readiness_audit_canonical(record)
    expected = (
        '{"assessment":{"business_output_sha256":"075d0db2e91e812458d6769792b43c2c0d64bcf77a3203fd94ba8c84a8f39790",'
        '"claim_contract_sha256":null,"execution_status":"workflow_complete",'
        '"post_terminal_facts":{"completed_step_ids":["research","publish"],'
        '"events_sha256":"eff3503f7ad7cc321aa5944d48a2475d5d8eeae3f78e3654fbd938f036136c33",'
        '"final_output_sha256":"075d0db2e91e812458d6769792b43c2c0d64bcf77a3203fd94ba8c84a8f39790",'
        '"schema_version":"post-terminal-facts.v1",'
        '"state_sha256":"ee261bce686e19f3bd421d1c78f31d609263eb990381077feca8c4757f7f61d4",'
        '"terminal_employee_id":"editor","terminal_provider":"terminal-provider",'
        '"terminal_reason":"last_step_succeeded","terminal_status":"workflow_complete",'
        '"terminal_step_id":"publish","terminal_step_index":2,"workflow_id":"phase261-workflow"},'
        '"readiness":"insufficient_evidence","reason_codes":["claim_contract_missing"],'
        '"schema_version":"publication-readiness.v1"},'
        '"assessment_sha256":"c0be7b28ea62357099e2c784ed4d43413752fcb6913677d4c0b445218914198d",'
        '"evaluated_claim_contract":null,"evaluated_claim_contract_sha256":null,'
        '"post_terminal_facts":{"completed_step_ids":["research","publish"],'
        '"events_sha256":"eff3503f7ad7cc321aa5944d48a2475d5d8eeae3f78e3654fbd938f036136c33",'
        '"final_output_sha256":"075d0db2e91e812458d6769792b43c2c0d64bcf77a3203fd94ba8c84a8f39790",'
        '"schema_version":"post-terminal-facts.v1",'
        '"state_sha256":"ee261bce686e19f3bd421d1c78f31d609263eb990381077feca8c4757f7f61d4",'
        '"terminal_employee_id":"editor","terminal_provider":"terminal-provider",'
        '"terminal_reason":"last_step_succeeded","terminal_status":"workflow_complete",'
        '"terminal_step_id":"publish","terminal_step_index":2,"workflow_id":"phase261-workflow"},'
        '"post_terminal_facts_sha256":"967c35093c4ac3ece19a06d8f1878c73b457ad9329a026359fd225a32dc49a13",'
        '"schema_version":"publication-readiness-audit.v1"}'
    )
    assert first == second
    assert first == expected
    assert publication_readiness_audit_canonical_bytes(record) == first.encode(
        "utf-8"
    )
    assert publication_readiness_audit_digest(record) == hashlib.sha256(
        first.encode("utf-8")
    ).hexdigest()
    assert publication_readiness_audit_digest(record) == (
        "47993de25ed109a11cc630b84f28afa3ceadba251d6902d0b92e13ce652bde1e"
    )
    assert record.digest == publication_readiness_audit_digest(record)
    changed_facts = replace(facts, state_sha256="a" * 64)
    changed_record = build_publication_readiness_audit_record(
        changed_facts,
        "FINAL 日本語 😀",
    )
    assert changed_record.digest != record.digest


def test_audit_sidecar_create_is_exact_and_identical_repersist_is_idempotent(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    record = build_publication_readiness_audit_record(facts, "FINAL 日本語 😀")
    path = tmp_path / "audit.json"
    expected = publication_readiness_audit_canonical_bytes(record)

    first = persist_publication_readiness_audit(path, record)
    before = path.read_bytes()
    second = persist_publication_readiness_audit(path, record)

    assert first.bytes_written == len(expected)
    assert first.idempotent is False
    assert second.bytes_written == len(expected)
    assert second.idempotent is True
    assert before == expected
    assert path.read_bytes() == expected


def test_audit_sidecar_conflict_never_overwrites_original_bytes(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    path = tmp_path / "audit.json"
    first_record = build_publication_readiness_audit_record(facts, "FINAL 日本語 😀")
    second_record = build_publication_readiness_audit_record(
        facts,
        "FINAL 日本語 😀",
        claim_contract=claim_contract_for(facts),
    )
    persist_publication_readiness_audit(path, first_record)
    before = path.read_bytes()

    with pytest.raises(PublicationReadinessAuditConflictError):
        persist_publication_readiness_audit(path, second_record)

    assert path.read_bytes() == before


def test_audit_sidecar_requires_explicit_path(tmp_path: Path) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    record = build_publication_readiness_audit_record(facts, "FINAL 日本語 😀")

    with pytest.raises(PublicationReadinessAuditError):
        persist_publication_readiness_audit(str(tmp_path / "audit.json"), record)  # type: ignore[arg-type]


def test_audit_sidecar_strict_reload_preserves_bytes_and_digest(
    tmp_path: Path,
) -> None:
    targets, _snapshot, facts = load_facts(tmp_path)
    record = build_publication_readiness_audit_record(
        facts,
        "FINAL 日本語 😀",
        claim_contract=claim_contract_for(facts),
    )
    path = tmp_path / "audit.json"
    before_state = targets.state_path.read_bytes()
    before_events = targets.events_path.read_bytes()
    persist_publication_readiness_audit(path, record)
    before = path.read_bytes()

    loaded = load_publication_readiness_audit(path)

    assert loaded == record
    assert loaded.digest == record.digest
    assert publication_readiness_audit_canonical_bytes(loaded) == before
    assert targets.state_path.read_bytes() == before_state
    assert targets.events_path.read_bytes() == before_events
    assert b"readiness" not in before_events
    assert not (tmp_path / "publication-readiness.json").exists()


def test_audit_sidecar_reload_is_deterministic_in_a_fresh_process(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    record = build_publication_readiness_audit_record(facts, "FINAL 日本語 😀")
    path = tmp_path / "audit.json"
    persist_publication_readiness_audit(path, record)

    source = """
import sys
from pathlib import Path

from ai_office.engine.post_terminal_facts import (
    load_publication_readiness_audit,
    publication_readiness_audit_canonical_bytes,
)

path = Path(sys.argv[1])
record = load_publication_readiness_audit(path)
print(record.digest)
print(publication_readiness_audit_canonical_bytes(record) == path.read_bytes())
"""
    result = subprocess.run(
        [sys.executable, "-c", source, str(path)],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [record.digest, "True"]


@pytest.mark.parametrize(
    "contents",
    [
        b"{",
        b"\xff",
    ],
)
def test_audit_sidecar_rejects_corrupt_json_or_invalid_utf8(
    tmp_path: Path,
    contents: bytes,
) -> None:
    path = tmp_path / "audit.json"
    path.write_bytes(contents)

    with pytest.raises(PublicationReadinessAuditLoadError):
        load_publication_readiness_audit(path)


def test_audit_sidecar_rejects_truncated_unknown_missing_noncanonical_and_duplicate(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    record = build_publication_readiness_audit_record(facts, "FINAL 日本語 😀")
    canonical = publication_readiness_audit_canonical_bytes(record)
    path = tmp_path / "audit.json"

    for contents in (
        canonical[:-1],
        json.dumps(
            {**json.loads(canonical), "unknown": "value"},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        json.dumps(
            {
                key: value
                for key, value in json.loads(canonical).items()
                if key != "assessment_sha256"
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        canonical + b"\n",
        b'{"schema_version":"publication-readiness-audit.v1",'
        b'"schema_version":"publication-readiness-audit.v1"}',
    ):
        path.write_bytes(contents)
        with pytest.raises(PublicationReadinessAuditLoadError):
            load_publication_readiness_audit(path)


def test_audit_sidecar_rejects_nested_digest_tampering_and_preserves_history(
    tmp_path: Path,
) -> None:
    targets, _snapshot, facts = load_facts(tmp_path)
    record = build_publication_readiness_audit_record(facts, "FINAL 日本語 😀")
    path = tmp_path / "audit.json"
    persist_publication_readiness_audit(path, record)
    before_state = targets.state_path.read_bytes()
    before_events = targets.events_path.read_bytes()
    value = json.loads(path.read_text(encoding="utf-8"))
    value["post_terminal_facts_sha256"] = "a" * 64
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(PublicationReadinessAuditLoadError):
        load_publication_readiness_audit(path)

    assert targets.state_path.read_bytes() == before_state
    assert targets.events_path.read_bytes() == before_events


def test_missing_or_mismatched_output_is_stale_or_inconsistent(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)

    missing = assess_terminal_publication_readiness(facts, None)
    mismatch = assess_terminal_publication_readiness(facts, "different output")

    assert missing.readiness == "stale_or_inconsistent"
    assert missing.reason_codes == ("final_output_missing",)
    assert mismatch.readiness == "stale_or_inconsistent"
    assert mismatch.reason_codes == ("final_output_mismatch",)


def test_terminal_failure_is_never_publication_ready(tmp_path: Path) -> None:
    state, events = failed_history()
    targets = write_history(tmp_path, state, events)
    snapshot = load_persisted_terminal_snapshot(workflow(), targets)
    facts = build_post_terminal_facts(snapshot)

    assessment = assess_terminal_publication_readiness(facts, "candidate")

    assert snapshot.terminal_status == "persisted_failure"
    assert facts.final_output_sha256 is None
    assert assessment.execution_status == "persisted_failure"
    assert assessment.readiness == "insufficient_evidence"
    assert assessment.reason_codes == (
        "execution_not_workflow_complete",
        "final_output_missing",
    )


@pytest.mark.parametrize("status", ["ready", "running"])
def test_nonterminal_history_is_rejected(tmp_path: Path, status: str) -> None:
    definition = workflow()
    state = WorkflowExecutionState(
        definition.id,
        status,  # type: ignore[arg-type]
        "research",
        1,
        "researcher",
        (),
        None,
    )
    targets = write_history(tmp_path, state, ())

    with pytest.raises(PersistedTerminalSnapshotError):
        load_persisted_terminal_snapshot(definition, targets)


def test_nonfinal_success_is_not_workflow_complete(tmp_path: Path) -> None:
    definition = workflow()
    state = WorkflowExecutionState(
        definition.id,
        "succeeded",
        "research",
        1,
        "researcher",
        ("research",),
        None,
    )
    event = RuntimeStepEvent(
        "step_succeeded",
        definition.id,
        "research",
        1,
        "researcher",
        "running",
        "succeeded",
        "provider",
        None,
        "response",
        "request",
        "intermediate",
        None,
    )
    targets = write_history(tmp_path, state, (event,))

    with pytest.raises(PersistedTerminalSnapshotError):
        load_persisted_terminal_snapshot(definition, targets)


def test_corrupt_history_is_rejected_without_mutation(tmp_path: Path) -> None:
    definition = workflow()
    state, events = success_history()
    targets = write_history(tmp_path, state, events)
    targets.state_path.write_bytes(b"not-json")
    before = targets.state_path.read_bytes(), targets.events_path.read_bytes()

    with pytest.raises(PersistedTerminalSnapshotError):
        load_persisted_terminal_snapshot(definition, targets)

    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before


def test_facts_and_assessment_canonicalization_is_deterministic(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)
    first_facts = serialize_post_terminal_facts_canonical(facts)
    second_facts = serialize_post_terminal_facts_canonical(replace(facts))
    assessment = assess_terminal_publication_readiness(facts, "FINAL 日本語 😀")
    first_assessment = serialize_publication_readiness_assessment_canonical(assessment)
    second_assessment = serialize_publication_readiness_assessment_canonical(
        replace(assessment)
    )

    assert first_facts == second_facts
    assert post_terminal_facts_digest(facts) == hashlib.sha256(
        first_facts.encode("utf-8")
    ).hexdigest()
    assert first_facts == (
        '{"completed_step_ids":["research","publish"],'
        '"events_sha256":"eff3503f7ad7cc321aa5944d48a2475d5d8eeae3f78e3654fbd938f036136c33",'
        '"final_output_sha256":"075d0db2e91e812458d6769792b43c2c0d64bcf77a3203fd94ba8c84a8f39790",'
        '"schema_version":"post-terminal-facts.v1",'
        '"state_sha256":"ee261bce686e19f3bd421d1c78f31d609263eb990381077feca8c4757f7f61d4",'
        '"terminal_employee_id":"editor","terminal_provider":"terminal-provider",'
        '"terminal_reason":"last_step_succeeded","terminal_status":"workflow_complete",'
        '"terminal_step_id":"publish","terminal_step_index":2,"workflow_id":"phase261-workflow"}'
    )
    assert post_terminal_facts_digest(facts) == (
        "967c35093c4ac3ece19a06d8f1878c73b457ad9329a026359fd225a32dc49a13"
    )
    assert first_assessment == second_assessment
    assert publication_readiness_assessment_digest(assessment) == hashlib.sha256(
        first_assessment.encode("utf-8")
    ).hexdigest()
    assert first_assessment == (
        '{"business_output_sha256":"075d0db2e91e812458d6769792b43c2c0d64bcf77a3203fd94ba8c84a8f39790",'
        '"claim_contract_sha256":null,"execution_status":"workflow_complete",'
        '"post_terminal_facts":{"completed_step_ids":["research","publish"],'
        '"events_sha256":"eff3503f7ad7cc321aa5944d48a2475d5d8eeae3f78e3654fbd938f036136c33",'
        '"final_output_sha256":"075d0db2e91e812458d6769792b43c2c0d64bcf77a3203fd94ba8c84a8f39790",'
        '"schema_version":"post-terminal-facts.v1",'
        '"state_sha256":"ee261bce686e19f3bd421d1c78f31d609263eb990381077feca8c4757f7f61d4",'
        '"terminal_employee_id":"editor","terminal_provider":"terminal-provider",'
        '"terminal_reason":"last_step_succeeded","terminal_status":"workflow_complete",'
        '"terminal_step_id":"publish","terminal_step_index":2,"workflow_id":"phase261-workflow"},'
        '"readiness":"insufficient_evidence","reason_codes":["claim_contract_missing"],'
        '"schema_version":"publication-readiness.v1"}'
    )
    assert publication_readiness_assessment_digest(assessment) == (
        "c0be7b28ea62357099e2c784ed4d43413752fcb6913677d4c0b445218914198d"
    )


def test_facts_digest_binds_terminal_identity_sources_and_raw_output() -> None:
    state = WorkflowExecutionState(
        "w",
        "succeeded",
        "step",
        1,
        "employee",
        ("step",),
        None,
    )
    event = RuntimeStepEvent(
        "step_succeeded",
        "w",
        "step",
        1,
        "employee",
        "running",
        "succeeded",
        "provider",
        None,
        "response",
        "request",
        "output",
        None,
    )
    from ai_office.storage import LoadedWorkflowExecutionHistory

    snapshot = PersistedTerminalSnapshot(
        LoadedWorkflowExecutionHistory(state, (event,)),
        "workflow_complete",
        "last_step_succeeded",
        "a" * 64,
        "b" * 64,
    )
    facts = build_post_terminal_facts(snapshot)
    variants = (
        replace(facts, state_sha256="c" * 64),
        replace(facts, events_sha256="c" * 64),
        replace(facts, final_output_sha256="c" * 64),
        replace(facts, terminal_provider="other-provider"),
        replace(facts, terminal_step_id="other-step"),
        replace(facts, completed_step_ids=("other-step",)),
    )

    assert len({facts.digest, *(variant.digest for variant in variants)}) == 7
    canonical_result_digest = hashlib.sha256(
        json.dumps(
            {
                "employee_id": "employee",
                "output_text": "output",
                "step_id": "step",
                "step_index": 1,
                "workflow_id": "w",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert facts.final_output_sha256 != canonical_result_digest


def test_snapshot_and_assessment_are_byte_for_byte_read_only(
    tmp_path: Path,
) -> None:
    targets, snapshot, facts = load_facts(tmp_path)
    before = targets.state_path.read_bytes(), targets.events_path.read_bytes()

    assessment = assess_terminal_publication_readiness(facts, "FINAL 日本語 😀")
    serialize_post_terminal_facts_canonical(facts)
    serialize_publication_readiness_assessment_canonical(assessment)

    assert (targets.state_path.read_bytes(), targets.events_path.read_bytes()) == before
    assert snapshot.history.events[-1].output_text == "FINAL 日本語 😀"
    assert not (tmp_path / "publication-readiness.json").exists()


def test_strict_models_are_frozen_and_reject_subclass_values(
    tmp_path: Path,
) -> None:
    _targets, _snapshot, facts = load_facts(tmp_path)

    with pytest.raises(FrozenInstanceError):
        facts.workflow_id = "changed"  # type: ignore[misc]
    with pytest.raises(PostTerminalFactsError):
        replace(facts, workflow_id=StringChild(facts.workflow_id))
    with pytest.raises(PublicationReadinessError):
        assess_terminal_publication_readiness(facts, StringChild("FINAL 日本語 😀"))
