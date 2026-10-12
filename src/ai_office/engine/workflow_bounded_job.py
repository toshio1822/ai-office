"""Finite, fixed-action Job authority spanning immutable Workflow Runs.

This remains an authorization owner, not an execution engine. Each exact
Run/Step action is materialized only after its immutable Run manifest and any
predecessor Artifact have been reloaded and checked. Dispatch still goes
through the existing provider and Execution Evidence path.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from ai_office.engine.workflow_job_authorization import (
    WorkflowJobAuthorizationError,
    WorkflowJobGrant,
    _canonical,
    _check_store_permissions,
    _identifier,
    _persist_immutable,
    _read_regular,
)
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


@dataclass(frozen=True)
class BoundedJobActionSlot:
    """One exact position in an upfront, closed action graph."""

    ordinal: int
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    workflow_snapshot_sha256: str
    employee_snapshot_sha256: str
    provider: str
    execution_target_fingerprint: str
    model: str
    input_source: Literal[
        "assignment",
        "previous_artifact",
        "previous_step",
        "review_input",
        "revision_input",
        "re_review_input",
    ]

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise WorkflowJobAuthorizationError("job_action_ordinal")
        if type(self.step_index) is not int or self.step_index < 1:
            raise WorkflowJobAuthorizationError("job_action_step")
        if self.input_source not in {
            "assignment",
            "previous_artifact",
            "previous_step",
            "review_input",
            "revision_input",
            "re_review_input",
        }:
            raise WorkflowJobAuthorizationError("job_action_input")
        for value in (
            self.workflow_id,
            self.step_id,
            self.employee_id,
            self.provider,
            self.model,
        ):
            if not _identifier(value):
                raise WorkflowJobAuthorizationError("job_action_identity")
        for value in (
            self.workflow_snapshot_sha256,
            self.employee_snapshot_sha256,
            self.execution_target_fingerprint,
        ):
            if not _valid_digest(value):
                raise WorkflowJobAuthorizationError("job_action_digest")


@dataclass(frozen=True)
class BoundedWorkflowJobScope:
    """Immutable root scope and separately identified purpose grants."""

    job_id: str
    assignment_sha256: str
    initial_run_id: str
    initial_input_sha256: str
    actions: tuple[BoundedJobActionSlot, ...]
    max_provider_attempts: int
    max_revisions: int
    expires_at_ns: int
    business_grant: WorkflowJobGrant
    execution_grant: WorkflowJobGrant

    def __post_init__(self) -> None:
        if not _identifier(self.job_id):
            raise WorkflowJobAuthorizationError("job_scope_identity")
        if (
            not _valid_digest(self.assignment_sha256)
            or not _valid_digest(self.initial_input_sha256)
            or not _identifier(self.initial_run_id)
        ):
            raise WorkflowJobAuthorizationError("job_scope_digest")
        if (
            type(self.actions) is not tuple
            or len(self.actions) < 2
            or tuple(item.ordinal for item in self.actions)
            != tuple(range(len(self.actions)))
            or self.actions[0].input_source != "assignment"
            or any(
                item.input_source
                not in {
                    "previous_artifact",
                    "previous_step",
                    "review_input",
                    "revision_input",
                    "re_review_input",
                }
                for item in self.actions[1:]
            )
        ):
            raise WorkflowJobAuthorizationError("job_scope_actions")
        if (
            type(self.max_provider_attempts) is not int
            or not 1 <= self.max_provider_attempts <= len(self.actions)
            or type(self.max_revisions) is not int
            or self.max_revisions not in {0, 1}
            or type(self.expires_at_ns) is not int
            or self.expires_at_ns <= 0
            or self.business_grant.purpose != "job_business"
            or self.execution_grant.purpose != "job_execution"
            or self.business_grant.job_id != self.job_id
            or self.execution_grant.job_id != self.job_id
            or self.business_grant.envelope_sha256
            != _scope_intent_digest(
                self.job_id,
                self.assignment_sha256,
                self.initial_run_id,
                self.initial_input_sha256,
                self.actions,
                self.max_provider_attempts,
                self.max_revisions,
                self.expires_at_ns,
            )
            or self.execution_grant.envelope_sha256
            != self.business_grant.envelope_sha256
            or self.business_grant.grant_id == self.execution_grant.grant_id
        ):
            raise WorkflowJobAuthorizationError("job_scope_limit_or_grant")

    @property
    def canonical(self) -> bytes:
        return _canonical(
            {
                "actions": [_slot_dict(item) for item in self.actions],
                "assignment_sha256": self.assignment_sha256,
                "business_grant": json.loads(self.business_grant.canonical),
                "execution_grant": json.loads(self.execution_grant.canonical),
                "expires_at_ns": self.expires_at_ns,
                "initial_input_sha256": self.initial_input_sha256,
                "initial_run_id": self.initial_run_id,
                "job_id": self.job_id,
                "max_provider_attempts": self.max_provider_attempts,
                "max_revisions": self.max_revisions,
                "schema_version": "workflow-bounded-job-scope.v1",
            }
        )

    @property
    def digest(self) -> str:
        return _digest(self.canonical)


@dataclass(frozen=True)
class BoundedJobActionAuthorization:
    """System-derived exact action authorization; never a human child grant."""

    store_root: Path
    scope: BoundedWorkflowJobScope
    ordinal: int
    run_id: str
    manifest_digest: str
    run_input_sha256: str
    request_fingerprint: str
    predecessor_run_id: str | None
    predecessor_artifact_id: str | None
    predecessor_artifact_sha256: str | None
    action_sha256: str

    @property
    def job_id(self) -> str:
        return self.scope.job_id

    @property
    def authorization_source(self) -> str:
        return "job_action"

    @property
    def workflow_id(self) -> str:
        return self.scope.actions[self.ordinal].workflow_id

    @property
    def step_id(self) -> str:
        return self.scope.actions[self.ordinal].step_id

    @property
    def step_index(self) -> int:
        return self.scope.actions[self.ordinal].step_index

    @property
    def employee_id(self) -> str:
        return self.scope.actions[self.ordinal].employee_id

    @property
    def provider(self) -> str:
        return self.scope.actions[self.ordinal].provider

    @property
    def execution_target_fingerprint(self) -> str:
        return self.scope.actions[self.ordinal].execution_target_fingerprint

    @property
    def model(self) -> str:
        return self.scope.actions[self.ordinal].model

    @property
    def digest(self) -> str:
        return self.action_sha256

    @property
    def business_grant(self) -> WorkflowJobGrant:
        return self.scope.business_grant

    @property
    def execution_grant(self) -> WorkflowJobGrant:
        return self.scope.execution_grant

    @property
    def envelope(self):
        """Adapter needed by the shared evidence model for exact action fields."""
        return self


def issue_bounded_workflow_job_scope(
    store: WorkflowRunManifestStore,
    *,
    job_id: str,
    initial_run_id: str,
    assignment_sha256: str,
    initial_input_sha256: str,
    actions: tuple[BoundedJobActionSlot, ...],
    max_provider_attempts: int,
    max_revisions: int,
    expires_at_ns: int,
    business_approved_by: str,
    business_grant_id: str,
    execution_approved_by: str,
    execution_grant_id: str,
    now_ns: int | None = None,
) -> BoundedWorkflowJobScope:
    """Persist one immutable root grant; identity values are audit labels."""
    _check_store_permissions(store.root)
    issued = time.time_ns() if now_ns is None else now_ns
    root_digest = _scope_intent_digest(
        job_id,
        assignment_sha256,
        initial_run_id,
        initial_input_sha256,
        actions,
        max_provider_attempts,
        max_revisions,
        expires_at_ns,
    )
    business = WorkflowJobGrant(
        "workflow-job-authorization-grant.v1",
        "job_business",
        True,
        job_id,
        root_digest,
        business_grant_id,
        business_approved_by,
        issued,
    )
    execution = WorkflowJobGrant(
        "workflow-job-authorization-grant.v1",
        "job_execution",
        True,
        job_id,
        business.envelope_sha256,
        execution_grant_id,
        execution_approved_by,
        issued,
    )
    initial_manifest = load_workflow_run_manifest(store, initial_run_id)
    if (
        _digest(initial_manifest.run_input.encode("utf-8")) != initial_input_sha256
        or initial_input_sha256 != assignment_sha256
        or initial_manifest.workflow_id != actions[0].workflow_id
        or _digest(_canonical(_workflow_dict(initial_manifest.workflow_snapshot)))
        != actions[0].workflow_snapshot_sha256
    ):
        raise WorkflowJobAuthorizationError("job_initial_run")
    scope = BoundedWorkflowJobScope(
        job_id,
        assignment_sha256,
        initial_run_id,
        initial_input_sha256,
        actions,
        max_provider_attempts,
        max_revisions,
        expires_at_ns,
        business,
        execution,
    )
    # The v1 envelope digest is a compatibility field only for this distinct
    # root scope; v1 Run-bound records are not rewritten or reinterpreted.
    _persist_immutable(_scope_path(store.root, job_id), scope.canonical)
    return scope


def load_bounded_workflow_job_scope(
    store: WorkflowRunManifestStore, job_id: str
) -> BoundedWorkflowJobScope:
    if not _identifier(job_id):
        raise WorkflowJobAuthorizationError("job_scope_identity")
    payload = _read_regular(_scope_path(store.root, job_id))
    try:
        value = json.loads(payload)
        if value.pop("schema_version") != "workflow-bounded-job-scope.v1":
            raise ValueError
        business = WorkflowJobGrant(**value.pop("business_grant"))
        execution = WorkflowJobGrant(**value.pop("execution_grant"))
        actions = tuple(BoundedJobActionSlot(**item) for item in value.pop("actions"))
        scope = BoundedWorkflowJobScope(
            actions=actions, business_grant=business, execution_grant=execution, **value
        )
        if scope.canonical != payload:
            raise ValueError
        return scope
    except Exception:
        raise WorkflowJobAuthorizationError("job_scope_load") from None


def derive_bounded_job_action_authorization(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    *,
    ordinal: int,
    run_id: str,
    request_fingerprint: str,
    now_ns: int | None = None,
    predecessor: tuple[str, str, str] | None = None,
) -> BoundedJobActionAuthorization:
    """Derive exact action proof from the root scope and verified Run lineage."""
    current = load_bounded_workflow_job_scope(store, scope.job_id)
    now = time.time_ns() if now_ns is None else now_ns
    if (
        current != scope
        or now >= current.expires_at_ns
        or _revoked(store.root, current)
    ):
        raise WorkflowJobAuthorizationError("job_scope_expired_or_revoked")
    if type(ordinal) is not int or not 0 <= ordinal < len(current.actions):
        raise WorkflowJobAuthorizationError("job_action_out_of_scope")
    if ordinal >= 3 and current.max_revisions == 0:
        raise WorkflowJobAuthorizationError("document_job_revision_budget_exhausted")
    if not _identifier(run_id) or not _valid_digest(request_fingerprint):
        raise WorkflowJobAuthorizationError("job_action_identity")
    manifest = load_workflow_run_manifest(store, run_id)
    slot = current.actions[ordinal]
    workflow_snapshot = _canonical(_workflow_dict(manifest.workflow_snapshot))
    employee = next(
        (item for item in manifest.employee_snapshots if item.id == slot.employee_id),
        None,
    )
    if employee is None or not (
        manifest.workflow_id == slot.workflow_id
        and manifest.workflow_snapshot.steps[slot.step_index - 1].id == slot.step_id
        and manifest.workflow_snapshot.steps[slot.step_index - 1].employee
        == slot.employee_id
        and _digest(workflow_snapshot) == slot.workflow_snapshot_sha256
        and _digest(_canonical(_employee_dict(employee)))
        == slot.employee_snapshot_sha256
        and manifest.workflow_snapshot.steps[
            slot.step_index - 1
        ].business_approval_required
        and employee.allowed_tools == ()
    ):
        raise WorkflowJobAuthorizationError("job_action_definition")
    run_input_sha = _digest(manifest.run_input.encode("utf-8"))
    if ordinal == 0:
        if (
            predecessor is not None
            or run_id != current.initial_run_id
            or run_input_sha != current.initial_input_sha256
            or run_input_sha != current.assignment_sha256
        ):
            raise WorkflowJobAuthorizationError("job_action_input")
        pred_run = pred_artifact = pred_sha = None
    elif slot.input_source == "previous_step":
        if predecessor is not None or run_input_sha != current.initial_input_sha256:
            raise WorkflowJobAuthorizationError("job_action_predecessor")
        pred_run, pred_artifact, pred_sha = run_id, None, None
        prior_authorization = load_bounded_job_action_by_identity(
            store, current.job_id, ordinal - 1
        )
        prior_dispatch = json.loads(
            _read_regular(_dispatch_path(store.root, current.job_id, ordinal - 1))
        )
        if not (
            prior_authorization.run_id == run_id
            and prior_authorization.ordinal == ordinal - 1
            and prior_authorization.workflow_id == slot.workflow_id
            and prior_authorization.step_index + 1 == slot.step_index
            and _previous_step_is_verified(
                store, run_id, prior_authorization, prior_dispatch
            )
        ):
            raise WorkflowJobAuthorizationError("job_action_predecessor")
    else:
        if predecessor is None:
            raise WorkflowJobAuthorizationError("job_action_predecessor")
        pred_run, pred_artifact, pred_sha = predecessor
        from ai_office.engine.artifact import read_run_artifact

        try:
            artifact = read_run_artifact(store, pred_run, pred_artifact)
        except Exception:
            raise WorkflowJobAuthorizationError("job_action_handoff") from None
        if _digest(artifact.content) != pred_sha:
            raise WorkflowJobAuthorizationError("job_action_handoff")
        prior_authorization = load_bounded_job_action_by_identity(
            store, current.job_id, ordinal - 1
        )
        prior_dispatch = json.loads(
            _read_regular(_dispatch_path(store.root, current.job_id, ordinal - 1))
        )
        if (
            prior_authorization.run_id != pred_run
            or prior_authorization.ordinal != ordinal - 1
            or prior_dispatch.get("attempt_id") != artifact.execution_attempt_id
        ):
            raise WorkflowJobAuthorizationError("job_action_predecessor")
        expected_input = bounded_document_action_input(
            store, current.job_id, ordinal, predecessor
        )
        if (
            artifact.content.decode("utf-8") != manifest.run_input
            and slot.input_source == "previous_artifact"
        ):
            raise WorkflowJobAuthorizationError("job_action_handoff")
        if expected_input != manifest.run_input:
            raise WorkflowJobAuthorizationError("job_action_handoff")
    action_record = _canonical(
        {
            "action_ordinal": ordinal,
            "business_grant_sha256": current.business_grant.digest,
            "execution_grant_sha256": current.execution_grant.digest,
            "job_id": current.job_id,
            "manifest_digest": manifest.digest,
            "predecessor_artifact_id": pred_artifact,
            "predecessor_artifact_sha256": pred_sha,
            "predecessor_run_id": pred_run,
            "request_fingerprint": request_fingerprint,
            "run_id": run_id,
            "run_input_sha256": run_input_sha,
            "schema_version": "workflow-bounded-job-action.v1",
            "scope_sha256": current.digest,
            "workflow_id": slot.workflow_id,
            "step_id": slot.step_id,
            "step_index": slot.step_index,
            "employee_id": slot.employee_id,
            "provider": slot.provider,
            "execution_target_fingerprint": slot.execution_target_fingerprint,
            "model": slot.model,
        }
    )
    action_digest = _digest(action_record)
    _persist_immutable(_action_path(store.root, current.job_id, ordinal), action_record)
    return BoundedJobActionAuthorization(
        store.root,
        current,
        ordinal,
        run_id,
        manifest.digest,
        run_input_sha,
        request_fingerprint,
        pred_run,
        pred_artifact,
        pred_sha,
        action_digest,
    )


def reserve_bounded_job_attempt(
    authorization: BoundedJobActionAuthorization,
    *,
    request_body_sha256: str,
    request_body_length: int,
    now_ns: int | None = None,
) -> str:
    """Consume one Job-wide slot before shared Run/Step attempt claim."""
    if (
        not _valid_digest(request_body_sha256)
        or type(request_body_length) is not int
        or request_body_length < 0
    ):
        raise WorkflowJobAuthorizationError("job_reservation")
    with _job_lock(authorization.store_root, authorization.job_id):
        scope = load_bounded_workflow_job_scope(
            WorkflowRunManifestStore(authorization.store_root), authorization.job_id
        )
        now = time.time_ns() if now_ns is None else now_ns
        if (
            scope != authorization.scope
            or now >= scope.expires_at_ns
            or _revoked(authorization.store_root, scope)
        ):
            raise WorkflowJobAuthorizationError("job_scope_expired_or_revoked")
        records = _reservation_records(authorization.store_root, scope.job_id)
        if len(records) >= scope.max_provider_attempts:
            raise WorkflowJobAuthorizationError("attempt_allowance_depleted")
        if any(
            item.get("action_sha256") == authorization.action_sha256 for item in records
        ):
            raise WorkflowJobAuthorizationError("job_action_replayed")
        payload = _canonical(
            {
                "action_sha256": authorization.action_sha256,
                "ordinal": authorization.ordinal,
                "request_body_length": request_body_length,
                "request_body_sha256": request_body_sha256,
                "reservation_index": len(records),
                "run_id": authorization.run_id,
                "schema_version": "workflow-bounded-job-reservation.v1",
                "scope_sha256": scope.digest,
            }
        )
        _persist_immutable(
            _reservation_path(
                authorization.store_root, scope.job_id, authorization.ordinal
            ),
            payload,
        )
        return _digest(payload)


def admit_bounded_job_dispatch(
    authorization: BoundedJobActionAuthorization,
    *,
    attempt,
    reservation_sha256: str,
    now_ns: int | None = None,
) -> None:
    with _job_lock(authorization.store_root, authorization.job_id):
        scope = load_bounded_workflow_job_scope(
            WorkflowRunManifestStore(authorization.store_root), authorization.job_id
        )
        now = time.time_ns() if now_ns is None else now_ns
        if (
            scope != authorization.scope
            or now >= scope.expires_at_ns
            or _revoked(authorization.store_root, scope)
        ):
            raise WorkflowJobAuthorizationError("job_scope_expired_or_revoked")
        reservation = _read_regular(
            _reservation_path(
                authorization.store_root, scope.job_id, authorization.ordinal
            )
        )
        if _digest(reservation) != reservation_sha256:
            raise WorkflowJobAuthorizationError("job_reservation_binding")
        try:
            from ai_office.execution_evidence import (
                ExecutionAttemptEvidence,
                _attempt_dict,
                _canonical_json,
                execution_attempt_evidence_path,
            )

            if type(attempt) is not ExecutionAttemptEvidence:
                raise ValueError
            persisted_attempt = _read_owner_file(
                execution_attempt_evidence_path(
                    authorization.store_root, authorization.run_id, attempt.attempt_id
                )
            )
            if persisted_attempt != _canonical_json(_attempt_dict(attempt)):
                raise ValueError
        except Exception:
            raise WorkflowJobAuthorizationError("job_attempt_missing") from None
        if not (
            attempt.run_id == authorization.run_id
            and attempt.authorization_source == "job_action"
            and attempt.job_id == scope.job_id
            and attempt.job_action_sha256 == authorization.action_sha256
            and attempt.job_action_ordinal == authorization.ordinal
            and attempt.job_reservation_sha256 == reservation_sha256
        ):
            raise WorkflowJobAuthorizationError("job_attempt_binding")
        payload = _canonical(
            {
                "action_sha256": authorization.action_sha256,
                "admitted_at_ns": now,
                "attempt_evidence_sha256": attempt.digest,
                "attempt_id": attempt.attempt_id,
                "reservation_sha256": reservation_sha256,
                "schema_version": "workflow-bounded-job-dispatch.v1",
                "scope_sha256": scope.digest,
            }
        )
        _persist_immutable(
            _dispatch_path(
                authorization.store_root, scope.job_id, authorization.ordinal
            ),
            payload,
        )


def revoke_bounded_workflow_job_scope(
    store: WorkflowRunManifestStore, job_id: str, *, now_ns: int | None = None
) -> None:
    with _job_lock(store.root, job_id):
        scope = load_bounded_workflow_job_scope(store, job_id)
        _persist_immutable(
            _revocation_path(store.root, job_id),
            _canonical(
                {
                    "job_id": job_id,
                    "revoked_at_ns": time.time_ns() if now_ns is None else now_ns,
                    "schema_version": "workflow-bounded-job-revocation.v1",
                    "scope_sha256": scope.digest,
                }
            ),
        )


def validate_bounded_job_authority(
    authorization: BoundedJobActionAuthorization,
    *,
    binding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    request,
    target,
    resolved_tools: tuple,
    now_ns: int | None = None,
) -> None:
    scope = load_bounded_workflow_job_scope(
        WorkflowRunManifestStore(authorization.store_root), authorization.job_id
    )
    slot = scope.actions[authorization.ordinal]
    now = time.time_ns() if now_ns is None else now_ns
    if (
        scope != authorization.scope
        or now >= scope.expires_at_ns
        or _revoked(authorization.store_root, scope)
    ):
        raise WorkflowJobAuthorizationError("job_scope_expired_or_revoked")
    from ai_office.execution_target import execution_target_fingerprint
    from ai_office.invocation import build_model_invocation_execution_fingerprint

    fingerprint = build_model_invocation_execution_fingerprint(
        request, resolved_tools, target
    )
    manifest = load_workflow_run_manifest(
        WorkflowRunManifestStore(authorization.store_root), authorization.run_id
    )
    employee = next(
        (item for item in manifest.employee_snapshots if item.id == slot.employee_id),
        None,
    )
    step_snapshot = manifest.workflow_snapshot.steps[slot.step_index - 1]
    if not (
        binding.run_id == authorization.run_id
        and binding.manifest_digest == authorization.manifest_digest
        and manifest.digest == authorization.manifest_digest
        and employee is not None
        and request.system_instructions == employee.instructions
        and request.task_instructions == step_snapshot.instructions
        and request.allowed_tools == employee.allowed_tools
        and tuple(tool.name for tool in resolved_tools) == employee.allowed_tools
        and workflow_id == slot.workflow_id
        and step_id == slot.step_id
        and step_index == slot.step_index
        and employee_id == slot.employee_id
        and _digest(request.run_input.encode()) == authorization.run_input_sha256
        and fingerprint == authorization.request_fingerprint
        and target.provider == slot.provider
        and execution_target_fingerprint(target) == slot.execution_target_fingerprint
        and request.model == slot.model
    ):
        raise WorkflowJobAuthorizationError("job_action_scope")


def validate_bounded_job_business_authority(
    authorization: BoundedJobActionAuthorization,
    *,
    binding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    run_input: str,
    now_ns: int | None = None,
) -> None:
    scope = load_bounded_workflow_job_scope(
        WorkflowRunManifestStore(authorization.store_root), authorization.job_id
    )
    slot = scope.actions[authorization.ordinal]
    now = time.time_ns() if now_ns is None else now_ns
    if not (
        scope == authorization.scope
        and now < scope.expires_at_ns
        and not _revoked(authorization.store_root, scope)
        and binding.run_id == authorization.run_id
        and binding.manifest_digest == authorization.manifest_digest
        and workflow_id == slot.workflow_id
        and step_id == slot.step_id
        and step_index == slot.step_index
        and employee_id == slot.employee_id
        and _digest(run_input.encode("utf-8")) == authorization.run_input_sha256
    ):
        raise WorkflowJobAuthorizationError("job_business_scope")


def load_bounded_job_action_by_identity(
    store: WorkflowRunManifestStore,
    job_id: str,
    ordinal: int,
    *,
    verify_lineage: bool = True,
) -> BoundedJobActionAuthorization:
    scope = load_bounded_workflow_job_scope(store, job_id)
    try:
        payload = _read_regular(_action_path(store.root, job_id, ordinal))
        value = json.loads(payload)
        expected_keys = {
            "action_ordinal",
            "business_grant_sha256",
            "employee_id",
            "execution_grant_sha256",
            "execution_target_fingerprint",
            "job_id",
            "manifest_digest",
            "model",
            "predecessor_artifact_id",
            "predecessor_artifact_sha256",
            "predecessor_run_id",
            "provider",
            "request_fingerprint",
            "run_id",
            "run_input_sha256",
            "schema_version",
            "scope_sha256",
            "step_id",
            "step_index",
            "workflow_id",
        }
        if (
            type(value) is not dict
            or set(value) != expected_keys
            or value.pop("schema_version") != "workflow-bounded-job-action.v1"
            or value.get("scope_sha256") != scope.digest
            or value.get("action_ordinal") != ordinal
            or value.get("job_id") != job_id
        ):
            raise ValueError
        action_sha = _digest(payload)
        slot = scope.actions[ordinal]
        authorization = BoundedJobActionAuthorization(
            store.root,
            scope,
            ordinal,
            value["run_id"],
            value["manifest_digest"],
            value["run_input_sha256"],
            value["request_fingerprint"],
            value["predecessor_run_id"],
            value["predecessor_artifact_id"],
            value["predecessor_artifact_sha256"],
            action_sha,
        )
        if (
            _canonical({**value, "schema_version": "workflow-bounded-job-action.v1"})
            != payload
        ):
            raise ValueError
        manifest = load_workflow_run_manifest(store, authorization.run_id)
        if not (
            value["action_ordinal"] == ordinal
            and value["business_grant_sha256"] == scope.business_grant.digest
            and value["execution_grant_sha256"] == scope.execution_grant.digest
            and value["workflow_id"] == slot.workflow_id
            and value["step_id"] == slot.step_id
            and value["step_index"] == slot.step_index
            and value["employee_id"] == slot.employee_id
            and value["provider"] == slot.provider
            and value["execution_target_fingerprint"]
            == slot.execution_target_fingerprint
            and value["model"] == slot.model
        ):
            raise ValueError
        if (
            manifest.digest != authorization.manifest_digest
            or _digest(manifest.run_input.encode("utf-8"))
            != authorization.run_input_sha256
        ):
            raise ValueError
        slot = scope.actions[ordinal]
        if ordinal == 0:
            if (
                authorization.predecessor_run_id is not None
                or authorization.run_input_sha256 != scope.initial_input_sha256
                or authorization.run_input_sha256 != scope.assignment_sha256
            ):
                raise ValueError
        elif slot.input_source == "previous_step":
            prior = json.loads(
                _read_regular(_action_path(store.root, job_id, ordinal - 1))
            )
            prior_dispatch = json.loads(
                _read_regular(_dispatch_path(store.root, job_id, ordinal - 1))
            )
            if not (
                authorization.predecessor_run_id == authorization.run_id
                and authorization.predecessor_artifact_id is None
                and authorization.predecessor_artifact_sha256 is None
                and prior.get("run_id") == authorization.run_id
                and prior.get("workflow_id") == slot.workflow_id
                and prior.get("step_index", 0) + 1 == slot.step_index
                and _previous_step_is_verified(
                    store, authorization.run_id, prior, prior_dispatch
                )
                and authorization.run_input_sha256 == scope.initial_input_sha256
            ):
                raise ValueError
        else:
            from ai_office.engine.artifact import _parse_artifact, read_run_artifact

            if not (
                authorization.predecessor_run_id
                and authorization.predecessor_artifact_id
                and authorization.predecessor_artifact_sha256
            ):
                raise ValueError
            if verify_lineage:
                artifact = read_run_artifact(
                    store,
                    authorization.predecessor_run_id,
                    authorization.predecessor_artifact_id,
                )
            else:
                from ai_office.engine.artifact import workflow_artifact_path

                artifact = _parse_artifact(
                    json.loads(
                        _read_regular(
                            workflow_artifact_path(
                                store,
                                authorization.predecessor_run_id,
                                authorization.predecessor_artifact_id,
                            )
                        )
                    )
                )
            predecessor = (
                authorization.predecessor_run_id,
                authorization.predecessor_artifact_id,
                authorization.predecessor_artifact_sha256,
            )
            if _digest(artifact.content) != authorization.predecessor_artifact_sha256:
                raise ValueError
            if (
                slot.input_source == "previous_artifact"
                and artifact.content.decode("utf-8") != manifest.run_input
            ) or bounded_document_action_input(
                store, job_id, ordinal, predecessor, verify_lineage=verify_lineage
            ) != manifest.run_input:
                raise ValueError
            prior = json.loads(
                _read_regular(_action_path(store.root, job_id, ordinal - 1))
            )
            prior_dispatch = json.loads(
                _read_regular(_dispatch_path(store.root, job_id, ordinal - 1))
            )
            if (
                prior.get("run_id") != authorization.predecessor_run_id
                or prior_dispatch.get("attempt_id") is None
                or artifact.run_id != authorization.predecessor_run_id
                or artifact.artifact_id != authorization.predecessor_artifact_id
                or (
                    verify_lineage
                    and not _artifact_belongs_to_run(
                        store,
                        authorization.predecessor_run_id,
                        authorization.predecessor_artifact_id,
                    )
                )
            ):
                raise ValueError
        return authorization
    except Exception:
        raise WorkflowJobAuthorizationError("job_action_load") from None


def validate_bounded_job_attempt_authority(
    store: WorkflowRunManifestStore,
    *,
    job_id: str,
    ordinal: int,
    action_sha256: str,
    reservation_sha256: str,
    request_body_sha256: str,
    request_body_length: int,
    attempt_id: str,
    attempt_evidence_sha256: str,
    verify_lineage: bool = True,
) -> None:
    authorization = load_bounded_job_action_by_identity(
        store, job_id, ordinal, verify_lineage=verify_lineage
    )
    if authorization.action_sha256 != action_sha256:
        raise WorkflowJobAuthorizationError("job_action_binding")
    reservation = _read_regular(_reservation_path(store.root, job_id, ordinal))
    reservation_value = json.loads(reservation)
    if not (
        type(reservation_value) is dict
        and reservation_value.get("schema_version")
        == "workflow-bounded-job-reservation.v1"
        and reservation_value.get("scope_sha256") == authorization.scope.digest
        and _digest(reservation) == reservation_sha256
        and reservation == _canonical(reservation_value)
        and reservation_value.get("action_sha256") == action_sha256
        and reservation_value.get("run_id") == authorization.run_id
        and reservation_value.get("request_body_sha256") == request_body_sha256
        and reservation_value.get("request_body_length") == request_body_length
    ):
        raise WorkflowJobAuthorizationError("job_reservation_binding")
    dispatch = _read_regular(_dispatch_path(store.root, job_id, ordinal))
    dispatch_value = json.loads(dispatch)
    if not (
        type(dispatch_value) is dict
        and dispatch_value.get("schema_version") == "workflow-bounded-job-dispatch.v1"
        and type(dispatch_value.get("admitted_at_ns")) is int
        and dispatch_value["admitted_at_ns"] < authorization.scope.expires_at_ns
        and dispatch == _canonical(dispatch_value)
        and dispatch_value.get("action_sha256") == action_sha256
        and dispatch_value.get("attempt_id") == attempt_id
        and dispatch_value.get("attempt_evidence_sha256") == attempt_evidence_sha256
        and dispatch_value.get("reservation_sha256") == reservation_sha256
        and dispatch_value.get("scope_sha256") == authorization.scope.digest
    ):
        raise WorkflowJobAuthorizationError("job_dispatch_binding")


def bounded_document_action_input(
    store: WorkflowRunManifestStore,
    job_id: str,
    ordinal: int,
    predecessor: tuple[str, str, str] | None,
    *,
    verify_lineage: bool = True,
) -> str:
    """Materialize only the fixed review/revision handoffs from saved Artifacts."""
    scope = load_bounded_workflow_job_scope(store, job_id)
    if type(ordinal) is not int or not 0 <= ordinal < len(scope.actions):
        raise WorkflowJobAuthorizationError("job_action_out_of_scope")
    slot = scope.actions[ordinal]
    if slot.input_source == "previous_step":
        raise WorkflowJobAuthorizationError("job_action_input")
    if slot.input_source in {"assignment", "previous_artifact"}:
        if slot.input_source == "assignment":
            if ordinal != 0:
                raise WorkflowJobAuthorizationError("job_action_input")
            action = load_bounded_job_action_by_identity(store, job_id, 0)
            manifest = load_workflow_run_manifest(store, action.run_id)
            if _digest(manifest.run_input.encode("utf-8")) != scope.assignment_sha256:
                raise WorkflowJobAuthorizationError("job_assignment_binding")
            return manifest.run_input
        if predecessor is None:
            raise WorkflowJobAuthorizationError("job_action_predecessor")
        return _read_predecessor_artifact(
            store, job_id, ordinal, predecessor, verify_lineage=verify_lineage
        ).content.decode("utf-8")
    if slot.input_source not in {"review_input", "revision_input", "re_review_input"}:
        raise WorkflowJobAuthorizationError("job_action_input")
    if predecessor is None:
        raise WorkflowJobAuthorizationError("job_action_predecessor")
    handoff_artifact = _read_predecessor_artifact(
        store, job_id, ordinal, predecessor, verify_lineage=verify_lineage
    )
    action0 = load_bounded_job_action_by_identity(store, job_id, 0)
    assignment_manifest = load_workflow_run_manifest(store, action0.run_id)
    if (
        _digest(assignment_manifest.run_input.encode("utf-8"))
        != scope.assignment_sha256
    ):
        raise WorkflowJobAuthorizationError("job_assignment_binding")
    report = _artifact_for_action(store, job_id, 1, verify_lineage=verify_lineage)
    if ordinal == 2:
        if handoff_artifact.artifact_id != report.artifact_id:
            raise WorkflowJobAuthorizationError("job_action_handoff")
        values = {
            "assignment": assignment_manifest.run_input,
            "source_artifact": _artifact_payload(report),
        }
    elif ordinal == 3:
        review = _artifact_for_action(store, job_id, 2, verify_lineage=verify_lineage)
        if handoff_artifact.artifact_id != review.artifact_id:
            raise WorkflowJobAuthorizationError("job_action_handoff")
        values = {
            "assignment": assignment_manifest.run_input,
            "source_artifact": _artifact_payload(report),
            "review_artifact": _artifact_payload(review),
        }
    elif ordinal == 4:
        review = _artifact_for_action(store, job_id, 2, verify_lineage=verify_lineage)
        revision = _artifact_for_action(store, job_id, 3, verify_lineage=verify_lineage)
        if handoff_artifact.artifact_id != revision.artifact_id:
            raise WorkflowJobAuthorizationError("job_action_handoff")
        values = {
            "assignment": assignment_manifest.run_input,
            "source_artifact": _artifact_payload(report),
            "review_artifact": _artifact_payload(review),
            "revision_artifact": _artifact_payload(revision),
        }
    else:
        raise WorkflowJobAuthorizationError("job_action_input")
    return _canonical(values).decode("utf-8")


def _read_predecessor_artifact(
    store,
    job_id: str,
    ordinal: int,
    predecessor: tuple[str, str, str],
    *,
    verify_lineage: bool = True,
):
    from ai_office.engine.artifact import (
        _parse_artifact,
        read_run_artifact,
        workflow_artifact_path,
    )

    run_id, artifact_id, content_sha = predecessor
    try:
        if verify_lineage:
            artifact = read_run_artifact(store, run_id, artifact_id)
        else:
            payload = _read_regular(workflow_artifact_path(store, run_id, artifact_id))
            artifact = _parse_artifact(json.loads(payload))
    except Exception:
        raise WorkflowJobAuthorizationError("job_action_handoff") from None
    if _digest(artifact.content) != content_sha:
        raise WorkflowJobAuthorizationError("job_action_handoff")
    if verify_lineage:
        prior = load_bounded_job_action_by_identity(store, job_id, ordinal - 1)
        if prior.run_id != run_id:
            raise WorkflowJobAuthorizationError("job_action_predecessor")
    else:
        # During attempt-history validation, recursively loading the complete
        # predecessor authorization would re-enter that same history. The
        # caller has already strictly validated the Run/Artifact at derivation
        # time; here bind the canonical immediate action and its immutable
        # dispatch to the bytes being handed off, without walking history again.
        prior_payload = _read_regular(_action_path(store.root, job_id, ordinal - 1))
        prior = json.loads(prior_payload)
        prior_dispatch = json.loads(
            _read_regular(_dispatch_path(store.root, job_id, ordinal - 1))
        )
        if not (
            type(prior) is dict
            and prior_payload == _canonical(prior)
            and prior.get("action_ordinal") == ordinal - 1
            and prior.get("job_id") == job_id
            and prior.get("run_id") == run_id
            and prior_dispatch.get("action_sha256") == _digest(prior_payload)
            and prior_dispatch.get("attempt_id") == artifact.execution_attempt_id
        ):
            raise WorkflowJobAuthorizationError("job_action_predecessor")
    return artifact


def _artifact_for_action(
    store: WorkflowRunManifestStore,
    job_id: str,
    ordinal: int,
    *,
    verify_lineage: bool = True,
):
    from ai_office.engine.artifact import (
        _parse_artifact,
        list_run_artifacts,
    )

    if verify_lineage:
        run_id = load_bounded_job_action_by_identity(store, job_id, ordinal).run_id
    else:
        payload = _read_regular(_action_path(store.root, job_id, ordinal))
        action_value = json.loads(payload)
        scope = load_bounded_workflow_job_scope(store, job_id)
        if not (
            type(action_value) is dict
            and payload == _canonical(action_value)
            and action_value.get("action_ordinal") == ordinal
            and action_value.get("job_id") == job_id
            and action_value.get("scope_sha256") == scope.digest
        ):
            raise WorkflowJobAuthorizationError("job_action_load")
        run_id = action_value.get("run_id")
        if type(run_id) is not str:
            raise WorkflowJobAuthorizationError("job_action_load")
    dispatch = json.loads(_read_regular(_dispatch_path(store.root, job_id, ordinal)))
    if verify_lineage:
        artifacts = list_run_artifacts(store, run_id)
    else:
        artifacts = tuple(
            _parse_artifact(json.loads(_read_regular(path)))
            for path in store.root.glob(f"{run_id}.artifact.*.json")
        )
    candidates = [
        item
        for item in artifacts
        if item.execution_attempt_id == dispatch.get("attempt_id")
    ]
    if len(candidates) != 1:
        raise WorkflowJobAuthorizationError("job_action_artifact")
    return candidates[0]


def _artifact_payload(artifact) -> dict[str, object]:
    return {
        "artifact_id": artifact.artifact_id,
        "content": artifact.content.decode("utf-8"),
        "content_length": artifact.content_length,
        "content_sha256": artifact.content_sha256,
        "run_id": artifact.run_id,
    }


def validate_bounded_document_job_scope(scope: BoundedWorkflowJobScope) -> None:
    """Require the single supported creator/review/revision action plan."""
    expected = (
        ("research-and-summarize", "research", 1, "general-researcher", "assignment"),
        (
            "research-and-summarize",
            "summarize",
            2,
            "japanese-document-writer",
            "previous_step",
        ),
        ("review-supplied-artifact", "review", 1, "general-researcher", "review_input"),
        (
            "revise-reviewed-artifact",
            "revise",
            1,
            "japanese-document-writer",
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
    actual = tuple(
        (
            item.workflow_id,
            item.step_id,
            item.step_index,
            item.employee_id,
            item.input_source,
        )
        for item in scope.actions
    )
    if (
        actual != expected
        or scope.max_revisions not in {0, 1}
        or scope.max_provider_attempts != 3 + (2 * scope.max_revisions)
    ):
        raise WorkflowJobAuthorizationError("document_job_scope")


def _review_verdict(content: str, *, review_input: str | None = None) -> str:
    lines = content.splitlines()
    if not lines or lines[0] not in {
        "Verdict: PASS",
        "Verdict: CHANGES_REQUIRED",
        "Verdict: BLOCKED",
    }:
        raise WorkflowJobAuthorizationError("document_job_invalid_verdict")
    verdict = lines[0].removeprefix("Verdict: ")
    if verdict == "CHANGES_REQUIRED":
        required = (
            "Requirement",
            "Artifact location",
            "Evidence source/reference",
            "Impact",
            "Requested correction",
        )
        findings: dict[str, str] = {}
        for line in lines[1:]:
            label, separator, value = line.partition(":")
            label = label.strip().lstrip("-* ").strip().strip("*_")
            if label in required:
                if not separator or not value.strip() or label in findings:
                    raise WorkflowJobAuthorizationError(
                        "document_job_nonactionable_review"
                    )
                findings[label] = value.strip()
        if set(findings) != set(required):
            raise WorkflowJobAuthorizationError("document_job_nonactionable_review")
        evidence_reference = findings["Evidence source/reference"].casefold()
        if review_input is None or evidence_reference not in review_input.casefold():
            raise WorkflowJobAuthorizationError("document_job_unverified_finding")
    return verdict


@dataclass(frozen=True)
class BoundedDocumentJobOutcome:
    """Safe terminal result for the fixed bounded document workflow."""

    status: Literal["candidate", "reviewed", "stopped"]
    final_artifact_run_id: str | None
    final_artifact_id: str | None
    review_artifact_run_id: str | None
    review_artifact_id: str | None
    revisions_used: int
    stop_reason: str | None = None


def run_bounded_document_job(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    *,
    initial_run_id: str,
    assignment: str,
    workflows: dict,
    employees: dict,
    api_key,
    transport,
    execution_target,
) -> BoundedDocumentJobOutcome:
    """Run the fixed job once and leave an inspectable immutable terminal record."""
    current = load_bounded_workflow_job_scope(store, scope.job_id)
    if current != scope or initial_run_id != scope.initial_run_id:
        raise WorkflowJobAuthorizationError("document_job_binding")
    prior_status = _read_bounded_document_job_status(store, scope)
    if prior_status is not None:
        return _outcome_from_status(prior_status)
    with _job_lock(store.root, scope.job_id):
        prior_status = _read_bounded_document_job_status(store, scope)
        if prior_status is not None:
            return _outcome_from_status(prior_status)
        if _job_start_path(store.root, scope.job_id).exists():
            return BoundedDocumentJobOutcome(
                "stopped", None, None, None, None, 0, "document_job_already_started"
            )
        _persist_immutable(
            _job_start_path(store.root, scope.job_id),
            _canonical(
                {
                    "job_id": scope.job_id,
                    "scope_sha256": scope.digest,
                    "started_at_ns": time.time_ns(),
                    "schema_version": "workflow-bounded-document-job-start.v1",
                }
            ),
        )
    try:
        outcome = _execute_bounded_document_job(
            store,
            scope,
            initial_run_id=initial_run_id,
            assignment=assignment,
            workflows=workflows,
            employees=employees,
            api_key=api_key,
            transport=transport,
            execution_target=execution_target,
        )
    except WorkflowJobAuthorizationError as error:
        outcome = _stopped_document_job_outcome(store, scope, error.classification)
    except Exception:
        # Transport/normalization errors can have ambiguous side effects. Do not
        # retry or infer a provider outcome; make the partial stop inspectable.
        outcome = _stopped_document_job_outcome(
            store, scope, "document_job_execution_uncertain"
        )
    _persist_bounded_document_job_status(store, scope, outcome)
    return outcome


def inspect_bounded_document_job(
    store: WorkflowRunManifestStore, job_id: str
) -> dict[str, object]:
    """Read-only owner view of assignment, bounded quota, action and stop record."""
    scope = load_bounded_workflow_job_scope(store, job_id)
    manifest = load_workflow_run_manifest(store, scope.initial_run_id)
    if (
        _digest(manifest.run_input.encode("utf-8")) != scope.initial_input_sha256
        or scope.initial_input_sha256 != scope.assignment_sha256
    ):
        raise WorkflowJobAuthorizationError("document_job_assignment")
    reservations = _reservation_records(store.root, job_id)
    if len(reservations) > scope.max_provider_attempts:
        raise WorkflowJobAuthorizationError("attempt_allowance_depleted")
    dispatch_order = []
    for ordinal in range(len(scope.actions)):
        action_path = _action_path(store.root, job_id, ordinal)
        if not action_path.exists():
            continue
        authorization = load_bounded_job_action_by_identity(
            store, job_id, ordinal, verify_lineage=False
        )
        dispatch_path = _dispatch_path(store.root, job_id, ordinal)
        if dispatch_path.exists():
            dispatch_payload = _read_regular(dispatch_path)
            dispatch = json.loads(dispatch_payload)
            reservation = _read_regular(_reservation_path(store.root, job_id, ordinal))
            reservation_value = json.loads(reservation)
            if not (
                dispatch_payload == _canonical(dispatch)
                and dispatch.get("action_sha256") == authorization.action_sha256
                and dispatch.get("reservation_sha256") == _digest(reservation)
                and reservation == _canonical(reservation_value)
                and reservation_value.get("action_sha256")
                == authorization.action_sha256
                and reservation_value.get("ordinal") == ordinal
            ):
                raise WorkflowJobAuthorizationError("document_job_dispatch")
            dispatch_order.append(
                {
                    "action_ordinal": ordinal,
                    "admitted_at_ns": dispatch["admitted_at_ns"],
                    "attempt_id": dispatch["attempt_id"],
                    "run_id": authorization.run_id,
                }
            )
    dispatch_order.sort(
        key=lambda item: (item["admitted_at_ns"], item["action_ordinal"])
    )
    status = _read_bounded_document_job_status(store, scope)
    if status is not None and (
        status["consumed_provider_attempts"] != len(reservations)
        or status["revisions_used"]
        != sum(1 for item in reservations if item.get("ordinal") == 3)
    ):
        raise WorkflowJobAuthorizationError("document_job_status_accounting")
    return {
        "assignment": manifest.run_input,
        "assignment_sha256": scope.assignment_sha256,
        "consumed_provider_attempts": len(reservations),
        "dispatch_order": dispatch_order,
        "job_id": job_id,
        "max_provider_attempts": scope.max_provider_attempts,
        "max_revisions": scope.max_revisions,
        "remaining_provider_attempts": scope.max_provider_attempts - len(reservations),
        "revision_attempts_consumed": sum(
            1 for item in reservations if item.get("ordinal") == 3
        ),
        "status": status.get("status", "in_progress")
        if status
        else "interrupted_or_in_progress"
        if _job_start_path(store.root, job_id).exists()
        else "not_started",
        "stop_reason": status.get("stop_reason")
        if status
        else "document_job_terminal_record_missing"
        if _job_start_path(store.root, job_id).exists()
        else None,
    }


def _execute_bounded_document_job(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    *,
    initial_run_id: str,
    assignment: str,
    workflows: dict,
    employees: dict,
    api_key,
    transport,
    execution_target,
) -> BoundedDocumentJobOutcome:
    """Execute only the approved fixed creator/review/one-revision sequence.

    Every provider call is delegated to the existing fresh-start or
    continuation owner. This function only materializes pinned inputs,
    derives one exact action at a time, and selects the documented stop path.
    """
    from ai_office.engine.approved_workflow_continuation_cycle import (
        route_approved_workflow_continuation_cycle,
    )
    from ai_office.engine.approved_workflow_fresh_start import (
        ApprovedWorkflowBootstrapContext,
        InitialStepPreparationApproval,
        route_approved_workflow_fresh_start,
    )
    from ai_office.engine.next_step_preparation import (
        NextStepPreparationApproval,
        _prepare_approved_next_workflow_step,
    )
    from ai_office.engine.persisted_execution_outcome_reentry import (
        validate_loaded_persisted_execution_history,
    )
    from ai_office.engine.persisted_execution_outcome_routing_reentry import (
        route_persisted_execution_outcome_reentry,
    )
    from ai_office.engine.prepared_step_execution_start import (
        prepare_prepared_step_execution_start,
    )
    from ai_office.engine.workflow_progression import WorkflowProgressionDecision
    from ai_office.engine.workflow_run_manifest import (
        create_workflow_run_manifest,
        load_workflow_run_manifest,
    )
    from ai_office.execution_target import execution_target_fingerprint
    from ai_office.invocation import (
        ModelInvocationRequest,
        build_model_invocation_execution_fingerprint,
    )
    from ai_office.runtime.run_binding import WorkflowRunBinding
    from ai_office.storage.workflow_execution_history import (
        load_workflow_execution_history_with_source_digests,
    )
    from ai_office.storage.workflow_execution_persistence import (
        WorkflowExecutionPersistenceTargets,
    )

    validate_bounded_document_job_scope(scope)
    current_scope = load_bounded_workflow_job_scope(store, scope.job_id)
    if (
        current_scope != scope
        or initial_run_id != scope.initial_run_id
        or _digest(assignment.encode("utf-8")) != scope.assignment_sha256
        or _digest(assignment.encode("utf-8")) != scope.initial_input_sha256
        or execution_target_fingerprint(execution_target)
        != scope.actions[0].execution_target_fingerprint
    ):
        raise WorkflowJobAuthorizationError("document_job_binding")
    if any(
        _action_path(store.root, scope.job_id, ordinal).exists()
        or _reservation_path(store.root, scope.job_id, ordinal).exists()
        or _dispatch_path(store.root, scope.job_id, ordinal).exists()
        for ordinal in range(len(scope.actions))
    ):
        raise WorkflowJobAuthorizationError("document_job_already_started")

    for slot in scope.actions:
        loaded_workflow = workflows.get(slot.workflow_id)
        loaded_employee = employees.get(slot.employee_id)
        if loaded_workflow is None or loaded_employee is None:
            raise WorkflowJobAuthorizationError("document_job_definition_missing")
        if (
            loaded_workflow.definition.id != slot.workflow_id
            or loaded_employee.definition.id != slot.employee_id
            or loaded_employee.definition.allowed_tools != []
        ):
            raise WorkflowJobAuthorizationError("document_job_definition_changed")

    initial_manifest = load_workflow_run_manifest(store, initial_run_id)
    if (
        initial_manifest.workflow_id != "research-and-summarize"
        or initial_manifest.run_input != assignment
        or _digest(_canonical(_workflow_dict(initial_manifest.workflow_snapshot)))
        != scope.actions[0].workflow_snapshot_sha256
    ):
        raise WorkflowJobAuthorizationError("document_job_initial_run")

    def run_fresh(
        ordinal: int,
        run_id: str,
        run_input: str,
        predecessor: tuple[str, str, str] | None = None,
    ):
        slot = scope.actions[ordinal]
        loaded_workflow = workflows[slot.workflow_id]
        workflow = loaded_workflow.definition
        manifest = (
            load_workflow_run_manifest(store, run_id)
            if run_id == initial_run_id
            else create_workflow_run_manifest(
                store,
                run_id,
                run_input,
                loaded_workflow,
                tuple(
                    employees[step.employee]
                    for step in workflow.steps
                    if step.employee in employees
                ),
            )
        )
        if manifest.run_input != run_input or manifest.workflow_id != workflow.id:
            raise WorkflowJobAuthorizationError("document_job_manifest")
        employee = employees[slot.employee_id].definition
        step = workflow.steps[slot.step_index - 1]
        binding = WorkflowRunBinding(run_id=run_id, manifest_digest=manifest.digest)
        request = ModelInvocationRequest(
            model=employee.model,
            system_instructions=employee.instructions,
            task_instructions=step.instructions,
            allowed_tools=(),
            run_id=run_id,
            manifest_digest=manifest.digest,
            run_input=manifest.run_input,
        )
        authorization = derive_bounded_job_action_authorization(
            store,
            scope,
            ordinal=ordinal,
            run_id=run_id,
            request_fingerprint=build_model_invocation_execution_fingerprint(
                request, (), execution_target
            ),
            predecessor=predecessor,
        )
        state_path, events_path = store.execution_paths(run_id)
        result = route_approved_workflow_fresh_start(
            workflow,
            state_path,
            events_path,
            ApprovedWorkflowBootstrapContext(
                preparation_approval=InitialStepPreparationApproval(
                    False, workflow.id, step.id, slot.step_index, employee.id
                ),
                employee=employee,
                resolved_tools=(),
                api_key=api_key,
                execution_approval=None,
                transport=transport,
                execution_target=execution_target,
                binding=binding,
                manifest_store=store,
                job_authorization=authorization,
            ),
        )
        persisted_result = route_persisted_execution_outcome_reentry(
            workflow, state_path, events_path, allow_artifact_completion=True
        )
        if persisted_result != result:
            raise WorkflowJobAuthorizationError("document_job_outcome_mismatch")
        return result, workflow, manifest, authorization

    creator = workflows["research-and-summarize"].definition
    first_result, _, creator_manifest, _ = run_fresh(0, initial_run_id, assignment)
    if (
        type(first_result) is not WorkflowProgressionDecision
        or first_result.decision != "prepare_next_step"
    ):
        raise WorkflowJobAuthorizationError("document_job_creator_stopped")
    history, state_sha, _ = load_workflow_execution_history_with_source_digests(
        WorkflowExecutionPersistenceTargets(
            *store.execution_paths(initial_run_id),
            binding=WorkflowRunBinding(initial_run_id, creator_manifest.digest),
        )
    )
    validate_loaded_persisted_execution_history(
        creator, history, require_execution_provenance=True
    )
    second_slot = scope.actions[1]
    second_employee = employees[second_slot.employee_id].definition
    second_step = creator.steps[second_slot.step_index - 1]
    second_preparation = NextStepPreparationApproval(
        True,
        creator.id,
        creator.steps[0].id,
        1,
        second_step.id,
        second_slot.step_index,
        second_employee.id,
    )
    prepared = _prepare_approved_next_workflow_step(
        creator, history, first_result, second_preparation, second_employee
    )
    prepared_start = prepare_prepared_step_execution_start(
        prepared,
        history,
        state_source_sha256=state_sha,
        run_input=creator_manifest.run_input,
    )
    second_authorization = derive_bounded_job_action_authorization(
        store,
        scope,
        ordinal=1,
        run_id=initial_run_id,
        request_fingerprint=build_model_invocation_execution_fingerprint(
            prepared_start.request, (), execution_target
        ),
        now_ns=time.time_ns(),
    )
    second_result = route_approved_workflow_continuation_cycle(
        first_result,
        creator,
        second_preparation,
        second_employee,
        *store.execution_paths(initial_run_id),
        (),
        api_key,
        None,
        transport,
        job_authorization=second_authorization,
        execution_target=execution_target,
    )
    if (
        type(second_result) is not WorkflowProgressionDecision
        or second_result.decision != "workflow_complete"
    ):
        raise WorkflowJobAuthorizationError("document_job_creator_stopped")
    persisted_creator = route_persisted_execution_outcome_reentry(
        creator,
        *store.execution_paths(initial_run_id),
        allow_artifact_completion=True,
    )
    if persisted_creator != second_result:
        raise WorkflowJobAuthorizationError("document_job_outcome_mismatch")
    source_artifact = _artifact_for_action(store, scope.job_id, 1)

    def materialize(ordinal: int, predecessor):
        run_input = bounded_document_action_input(
            store, scope.job_id, ordinal, predecessor
        )
        run_id = (
            "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + f"-{ordinal}"
        )
        return run_id, run_input

    review_run_id, review_input = materialize(
        2,
        (
            source_artifact.run_id,
            source_artifact.artifact_id,
            source_artifact.content_sha256,
        ),
    )
    review_result, _, _, _ = run_fresh(
        2,
        review_run_id,
        review_input,
        (
            source_artifact.run_id,
            source_artifact.artifact_id,
            source_artifact.content_sha256,
        ),
    )
    if (
        type(review_result) is not WorkflowProgressionDecision
        or review_result.decision != "workflow_complete"
    ):
        raise WorkflowJobAuthorizationError("document_job_review_stopped")
    first_review = _artifact_for_action(store, scope.job_id, 2)
    first_verdict = _review_verdict(
        first_review.content.decode("utf-8"), review_input=review_input
    )
    if first_verdict == "PASS":
        return BoundedDocumentJobOutcome(
            "candidate",
            source_artifact.run_id,
            source_artifact.artifact_id,
            first_review.run_id,
            first_review.artifact_id,
            0,
        )
    if first_verdict != "CHANGES_REQUIRED":
        raise WorkflowJobAuthorizationError("document_job_review_blocked")
    if scope.max_revisions < 1:
        raise WorkflowJobAuthorizationError("document_job_revision_budget_exhausted")
    revision_run_id, revision_input = materialize(
        3, (first_review.run_id, first_review.artifact_id, first_review.content_sha256)
    )
    revision_result, _, _, _ = run_fresh(
        3,
        revision_run_id,
        revision_input,
        (first_review.run_id, first_review.artifact_id, first_review.content_sha256),
    )
    if (
        type(revision_result) is not WorkflowProgressionDecision
        or revision_result.decision != "workflow_complete"
    ):
        raise WorkflowJobAuthorizationError("document_job_revision_stopped")
    revised_artifact = _artifact_for_action(store, scope.job_id, 3)
    final_review_run_id, final_review_input = materialize(
        4,
        (
            revised_artifact.run_id,
            revised_artifact.artifact_id,
            revised_artifact.content_sha256,
        ),
    )
    final_review_result, _, _, _ = run_fresh(
        4,
        final_review_run_id,
        final_review_input,
        (
            revised_artifact.run_id,
            revised_artifact.artifact_id,
            revised_artifact.content_sha256,
        ),
    )
    if (
        type(final_review_result) is not WorkflowProgressionDecision
        or final_review_result.decision != "workflow_complete"
    ):
        raise WorkflowJobAuthorizationError("document_job_final_review_stopped")
    final_review = _artifact_for_action(store, scope.job_id, 4)
    if (
        _review_verdict(
            final_review.content.decode("utf-8"), review_input=final_review_input
        )
        != "PASS"
    ):
        raise WorkflowJobAuthorizationError("document_job_final_review_not_pass")
    return BoundedDocumentJobOutcome(
        "reviewed",
        revised_artifact.run_id,
        revised_artifact.artifact_id,
        final_review.run_id,
        final_review.artifact_id,
        1,
    )


def _persist_bounded_document_job_status(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    outcome: BoundedDocumentJobOutcome,
) -> None:
    reservations = _reservation_records(store.root, scope.job_id)
    payload = _canonical(
        {
            "assignment_sha256": scope.assignment_sha256,
            "consumed_provider_attempts": len(reservations),
            "final_artifact_id": outcome.final_artifact_id,
            "final_artifact_run_id": outcome.final_artifact_run_id,
            "job_id": scope.job_id,
            "max_provider_attempts": scope.max_provider_attempts,
            "max_revisions": scope.max_revisions,
            "remaining_provider_attempts": scope.max_provider_attempts
            - len(reservations),
            "review_artifact_id": outcome.review_artifact_id,
            "review_artifact_run_id": outcome.review_artifact_run_id,
            "revisions_used": outcome.revisions_used,
            "schema_version": "workflow-bounded-document-job-status.v1",
            "scope_sha256": scope.digest,
            "status": outcome.status,
            "stop_reason": outcome.stop_reason,
        }
    )
    _persist_immutable(_job_status_path(store.root, scope.job_id), payload)


def _read_bounded_document_job_status(
    store: WorkflowRunManifestStore, scope: BoundedWorkflowJobScope
) -> dict[str, object] | None:
    path = _job_status_path(store.root, scope.job_id)
    if not path.exists() and not path.is_symlink():
        return None
    payload = _read_regular(path)
    try:
        value = json.loads(payload)
    except Exception:
        raise WorkflowJobAuthorizationError("document_job_status") from None
    expected = {
        "assignment_sha256",
        "consumed_provider_attempts",
        "final_artifact_id",
        "final_artifact_run_id",
        "job_id",
        "max_provider_attempts",
        "max_revisions",
        "remaining_provider_attempts",
        "review_artifact_id",
        "review_artifact_run_id",
        "revisions_used",
        "schema_version",
        "scope_sha256",
        "status",
        "stop_reason",
    }
    if not (
        type(value) is dict
        and set(value) == expected
        and payload == _canonical(value)
        and value.get("schema_version") == "workflow-bounded-document-job-status.v1"
        and value.get("job_id") == scope.job_id
        and value.get("scope_sha256") == scope.digest
        and value.get("assignment_sha256") == scope.assignment_sha256
        and value.get("max_provider_attempts") == scope.max_provider_attempts
        and value.get("max_revisions") == scope.max_revisions
        and value.get("status") in {"candidate", "reviewed", "stopped"}
        and type(value.get("consumed_provider_attempts")) is int
        and 0 <= value["consumed_provider_attempts"] <= scope.max_provider_attempts
        and value.get("remaining_provider_attempts")
        == scope.max_provider_attempts - value["consumed_provider_attempts"]
        and type(value.get("revisions_used")) is int
        and 0 <= value["revisions_used"] <= scope.max_revisions
        and (
            value.get("stop_reason") is None
            if value.get("status") != "stopped"
            else type(value.get("stop_reason")) is str and bool(value["stop_reason"])
        )
    ):
        raise WorkflowJobAuthorizationError("document_job_status")
    return value


def _outcome_from_status(value: dict[str, object]) -> BoundedDocumentJobOutcome:
    return BoundedDocumentJobOutcome(
        value["status"],
        value["final_artifact_run_id"],
        value["final_artifact_id"],
        value["review_artifact_run_id"],
        value["review_artifact_id"],
        value["revisions_used"],
        value["stop_reason"],
    )


def _stopped_document_job_outcome(
    store: WorkflowRunManifestStore, scope: BoundedWorkflowJobScope, reason: str
) -> BoundedDocumentJobOutcome:
    artifacts = {}
    for ordinal in (1, 2, 3, 4):
        try:
            artifacts[ordinal] = _artifact_for_action(
                store, scope.job_id, ordinal, verify_lineage=False
            )
        except Exception:
            pass
    final = artifacts.get(3) or artifacts.get(1)
    review = artifacts.get(4) or artifacts.get(2)
    reservations = _reservation_records(store.root, scope.job_id)
    return BoundedDocumentJobOutcome(
        "stopped",
        final.run_id if final else None,
        final.artifact_id if final else None,
        review.run_id if review else None,
        review.artifact_id if review else None,
        sum(1 for item in reservations if item.get("ordinal") == 3),
        reason,
    )


def _slot_dict(item: BoundedJobActionSlot) -> dict[str, object]:
    return {key: getattr(item, key) for key in item.__dataclass_fields__}


def _scope_intent_digest(
    job_id: str,
    assignment_sha256: str,
    initial_run_id: str,
    initial_input_sha256: str,
    actions: tuple[BoundedJobActionSlot, ...],
    max_provider_attempts: int,
    max_revisions: int,
    expires_at_ns: int,
) -> str:
    return _digest(
        _canonical(
            {
                "actions": [_slot_dict(item) for item in actions],
                "assignment_sha256": assignment_sha256,
                "expires_at_ns": expires_at_ns,
                "initial_input_sha256": initial_input_sha256,
                "initial_run_id": initial_run_id,
                "job_id": job_id,
                "max_provider_attempts": max_provider_attempts,
                "max_revisions": max_revisions,
                "schema_version": "workflow-bounded-job-scope-intent.v1",
            }
        )
    )


def _artifact_belongs_to_run(store, run_id: str, artifact_id: str) -> bool:
    from ai_office.engine.artifact import list_run_artifacts

    return any(
        item.artifact_id == artifact_id for item in list_run_artifacts(store, run_id)
    )


def _previous_step_is_verified(store, run_id: str, prior: dict, dispatch: dict) -> bool:
    """Check a prior successful step without recursively loading current history."""
    from ai_office.execution_evidence import execution_attempt_evidence_path
    from ai_office.runtime.run_binding import WorkflowRunBinding
    from ai_office.storage.workflow_execution_history import (
        load_workflow_execution_state,
    )

    manifest = load_workflow_run_manifest(store, run_id)
    binding = WorkflowRunBinding(run_id=run_id, manifest_digest=manifest.digest)
    state = load_workflow_execution_state(store.state_path(run_id), binding=binding)
    attempt_id = dispatch.get("attempt_id")
    if type(attempt_id) is not str:
        return False
    # Full attempt loading re-enters Job authority validation. For a
    # previous-step link that would recursively validate the same action while
    # an outer history/artifact read is already validating it. The immutable
    # dispatch record was written only after strict attempt validation; bind
    # that record to the exact canonical attempt bytes here without re-entering
    # authority validation.
    attempt_payload = _read_owner_file(
        execution_attempt_evidence_path(store.root, run_id, attempt_id)
    )
    try:
        attempt = json.loads(attempt_payload)
    except Exception:
        return False
    if type(attempt) is not dict or attempt_payload != _canonical(attempt):
        return False
    field = (
        (lambda name: prior.get(name))
        if type(prior) is dict
        else (lambda name: getattr(prior, name))
    )
    prior_action_sha = (
        _digest(_canonical(prior)) if type(prior) is dict else field("action_sha256")
    )
    return bool(
        field("run_id") == run_id
        and field("manifest_digest") == manifest.digest
        and field("step_id") in state.completed_step_ids
        and attempt.get("schema_version") == "workflow-execution-attempt.v4"
        and attempt.get("attempt_id") == attempt_id
        and attempt.get("run_id") == run_id
        and attempt.get("manifest_digest") == manifest.digest
        and attempt.get("step_id") == field("step_id")
        and attempt.get("step_index") == field("step_index")
        and attempt.get("job_action_sha256") == prior_action_sha
        and attempt.get("authorization_source") == "job_action"
        and dispatch.get("attempt_evidence_sha256") == _digest(attempt_payload)
    )


def _workflow_dict(value) -> dict[str, object]:
    return asdict(value)


def _employee_dict(value) -> dict[str, object]:
    return asdict(value)


def _valid_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _read_owner_file(path: Path) -> bytes:
    """Read durable evidence without requiring a file mode the legacy writer lacks."""
    if path.is_symlink():
        raise WorkflowJobAuthorizationError("job_attempt_path")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise WorkflowJobAuthorizationError("job_attempt_path")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            return stream.read()
    except WorkflowJobAuthorizationError:
        raise
    except Exception:
        raise WorkflowJobAuthorizationError("job_attempt_path") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _scope_path(root: Path, job_id: str) -> Path:
    return root / f"workflow-bounded-job.{job_id}.json"


def _action_path(root: Path, job_id: str, ordinal: int) -> Path:
    return root / f"workflow-bounded-job.{job_id}.action.{ordinal}.json"


def _reservation_path(root: Path, job_id: str, ordinal: int) -> Path:
    return root / f"workflow-bounded-job.{job_id}.reservation.{ordinal}.json"


def _dispatch_path(root: Path, job_id: str, ordinal: int) -> Path:
    return root / f"workflow-bounded-job.{job_id}.dispatch.{ordinal}.json"


def _revocation_path(root: Path, job_id: str) -> Path:
    return root / f"workflow-bounded-job.{job_id}.revoked.json"


def _job_start_path(root: Path, job_id: str) -> Path:
    return root / f"workflow-bounded-job.{job_id}.start.json"


def _job_status_path(root: Path, job_id: str) -> Path:
    return root / f"workflow-bounded-job.{job_id}.status.json"


def _revoked(root: Path, scope: BoundedWorkflowJobScope) -> bool:
    path = _revocation_path(root, scope.job_id)
    if not path.exists() and not path.is_symlink():
        return False
    value = json.loads(_read_regular(path))
    if value != {
        "job_id": scope.job_id,
        "revoked_at_ns": value.get("revoked_at_ns"),
        "schema_version": "workflow-bounded-job-revocation.v1",
        "scope_sha256": scope.digest,
    }:
        raise WorkflowJobAuthorizationError("job_revocation_evidence")
    return True


def _reservation_records(root: Path, job_id: str) -> list[dict[str, object]]:
    records = []
    for path in sorted(root.glob(f"workflow-bounded-job.{job_id}.reservation.*.json")):
        value = json.loads(_read_regular(path))
        if (
            type(value) is not dict
            or value.get("schema_version") != "workflow-bounded-job-reservation.v1"
        ):
            raise WorkflowJobAuthorizationError("job_reservation_evidence")
        records.append(value)
    return records


@contextmanager
def _job_lock(root: Path, job_id: str):
    _check_store_permissions(root)
    path = root / f"workflow-bounded-job.{job_id}.lock"
    if path.is_symlink():
        raise WorkflowJobAuthorizationError("job_lock")
    descriptor = os.open(
        path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise WorkflowJobAuthorizationError("job_lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except WorkflowJobAuthorizationError:
        raise
    except Exception:
        raise WorkflowJobAuthorizationError("job_lock") from None
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
