"""Purpose-separated, Run-bound authorization evidence for one Job action.

This is an offline-first authorization contract, not an authenticated human
identity provider. The issuing process must be protected by deployment policy;
an ``approved_by`` value is audit metadata only. Normal Business/Execution
Approval evidence and historical schemas are not rewritten or synthesized.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)
from ai_office.execution_target import (
    ModelExecutionTarget,
    execution_target_fingerprint,
    validate_execution_target_for_provider,
)
from ai_office.invocation import (
    ModelInvocationRequest,
    build_model_invocation_execution_fingerprint,
)
from ai_office.runtime import WorkflowRunBinding
from ai_office.tools import ToolDefinition

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ENVELOPE_SCHEMA = "workflow-job-authorization-envelope.v1"
_GRANT_SCHEMA = "workflow-job-authorization-grant.v1"
_DISPATCH_SCHEMA = "workflow-job-dispatch-admission.v1"
_RESERVATION_SCHEMA = "workflow-job-attempt-reservation.v1"


class WorkflowJobAuthorizationError(ValueError):
    """A Job authority record is invalid, unavailable, or already consumed."""

    def __init__(self, classification: str = "authorization") -> None:
        super().__init__("workflow Job authorization is invalid")
        self.classification = classification


@dataclass(frozen=True)
class WorkflowJobAuthorizationEnvelope:
    """Immutable one-action envelope; no revision or job-loop authority."""

    job_id: str
    run_id: str
    manifest_digest: str
    workflow_id: str
    step_id: str
    step_index: int
    employee_id: str
    run_input_sha256: str
    provider: str
    execution_target_fingerprint: str
    model: str
    request_fingerprint: str
    max_provider_attempts: int = 1
    max_revisions: int = 0
    expires_at_ns: int = 0
    currency_cap_minor: int | None = None

    def __post_init__(self) -> None:
        if not all(
            _identifier(value)
            for value in (
                self.job_id,
                self.run_id,
                self.workflow_id,
                self.step_id,
                self.employee_id,
                self.provider,
                self.model,
            )
        ):
            _raise("envelope_identity")
        if not all(
            _digest(value)
            for value in (
                self.manifest_digest,
                self.run_input_sha256,
                self.execution_target_fingerprint,
                self.request_fingerprint,
            )
        ):
            _raise("envelope_digest")
        if (
            type(self.step_index) is not int
            or self.step_index < 1
            or self.max_provider_attempts != 1
            or self.max_revisions != 0
            or type(self.expires_at_ns) is not int
            or self.expires_at_ns <= 0
        ):
            _raise("envelope_limit")
        if self.currency_cap_minor is not None:
            _raise("unenforceable_currency_cap")

    @property
    def canonical(self) -> bytes:
        return _canonical(
            {
                "employee_id": self.employee_id,
                "execution_target_fingerprint": self.execution_target_fingerprint,
                "expires_at_ns": self.expires_at_ns,
                "job_id": self.job_id,
                "manifest_digest": self.manifest_digest,
                "max_provider_attempts": self.max_provider_attempts,
                "max_revisions": self.max_revisions,
                "model": self.model,
                "provider": self.provider,
                "request_fingerprint": self.request_fingerprint,
                "run_id": self.run_id,
                "run_input_sha256": self.run_input_sha256,
                "schema_version": _ENVELOPE_SCHEMA,
                "step_id": self.step_id,
                "step_index": self.step_index,
                "workflow_id": self.workflow_id,
            }
        )

    @property
    def digest(self) -> str:
        return _sha(self.canonical)


@dataclass(frozen=True)
class WorkflowJobGrant:
    """One affirmative, purpose-specific Job grant."""

    schema_version: Literal["workflow-job-authorization-grant.v1"]
    purpose: Literal["job_business", "job_execution"]
    approved: Literal[True]
    job_id: str
    envelope_sha256: str
    grant_id: str
    approved_by: str
    issued_at_ns: int

    def __post_init__(self) -> None:
        if (
            self.schema_version != _GRANT_SCHEMA
            or self.purpose not in {"job_business", "job_execution"}
            or self.approved is not True
            or not _identifier(self.job_id)
            or not _digest(self.envelope_sha256)
            or not _identifier(self.grant_id)
            or type(self.approved_by) is not str
            or not self.approved_by.strip()
            or type(self.issued_at_ns) is not int
            or self.issued_at_ns <= 0
        ):
            _raise("grant")

    @property
    def canonical(self) -> bytes:
        return _canonical(
            {
                "approved": self.approved,
                "approved_by": self.approved_by,
                "envelope_sha256": self.envelope_sha256,
                "grant_id": self.grant_id,
                "issued_at_ns": self.issued_at_ns,
                "job_id": self.job_id,
                "purpose": self.purpose,
                "schema_version": self.schema_version,
            }
        )

    @property
    def digest(self) -> str:
        return _sha(self.canonical)


@dataclass(frozen=True)
class WorkflowJobAuthorization:
    """The two real grant records required at the business/provider gates."""

    store_root: Path
    envelope: WorkflowJobAuthorizationEnvelope
    business_grant: WorkflowJobGrant
    execution_grant: WorkflowJobGrant


@dataclass(frozen=True)
class WorkflowJobAuthorizationInspection:
    """Read-only operator summary; never represents authenticated identity."""

    job_id: str
    run_id: str
    envelope_sha256: str
    business_grant_id: str
    execution_grant_id: str
    max_provider_attempts: int
    max_revisions: int
    attempts_reserved: int
    dispatch_admitted: bool
    provider_response_recorded: bool
    expires_at_ns: int
    revoked: bool
    lifecycle_order: str
    security_note: str = (
        "approved_by is an audit label, not authenticated human identity"
    )


def issue_workflow_job_authorization(
    store: WorkflowRunManifestStore,
    envelope: WorkflowJobAuthorizationEnvelope,
    *,
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    target: ModelExecutionTarget,
    business_approved_by: str,
    business_grant_id: str,
    execution_approved_by: str,
    execution_grant_id: str,
    now_ns: int | None = None,
) -> WorkflowJobAuthorization:
    """Persist separately identified purpose grants for one reviewed envelope.

    The caller is a trusted local operator entrypoint. This function records
    that asserted action but cannot independently authenticate the person.
    """
    if type(store) is not WorkflowRunManifestStore:
        _raise("store")
    manifest = load_workflow_run_manifest(store, envelope.run_id)
    if (
        manifest.digest != envelope.manifest_digest
        or manifest.workflow_id != envelope.workflow_id
    ):
        _raise("manifest")
    try:
        step = manifest.workflow_snapshot.steps[envelope.step_index - 1]
        employee = next(
            item
            for item in manifest.employee_snapshots
            if item.id == envelope.employee_id
        )
        target = validate_execution_target_for_provider(
            target, provider=envelope.provider
        )
        request_fingerprint = build_model_invocation_execution_fingerprint(
            request, resolved_tools, target
        )
    except Exception:
        _raise("envelope_action")
    if not (
        step.id == envelope.step_id
        and step.employee == envelope.employee_id
        and employee.model == envelope.model == request.model
        and employee.instructions == request.system_instructions
        and tuple(employee.allowed_tools) == request.allowed_tools
        and request.run_id == envelope.run_id
        and request.manifest_digest == envelope.manifest_digest
        and request.run_input == manifest.run_input
        and _sha(manifest.run_input.encode("utf-8")) == envelope.run_input_sha256
        and request_fingerprint == envelope.request_fingerprint
        and execution_target_fingerprint(target)
        == envelope.execution_target_fingerprint
    ):
        _raise("envelope_action")
    step_snapshot = manifest.workflow_snapshot.steps[envelope.step_index - 1]
    if not step_snapshot.business_approval_required:
        _raise("business_gate_disabled")
    _check_store_permissions(store.root)
    issued = time.time_ns() if now_ns is None else now_ns
    business = WorkflowJobGrant(
        _GRANT_SCHEMA,
        "job_business",
        True,
        envelope.job_id,
        envelope.digest,
        business_grant_id,
        business_approved_by,
        issued,
    )
    execution = WorkflowJobGrant(
        _GRANT_SCHEMA,
        "job_execution",
        True,
        envelope.job_id,
        envelope.digest,
        execution_grant_id,
        execution_approved_by,
        issued,
    )
    if business.grant_id == execution.grant_id:
        _raise("purpose_separation")
    _persist_immutable(
        _grant_path(store.root, envelope, "business"), business.canonical
    )
    _persist_immutable(
        _grant_path(store.root, envelope, "execution"), execution.canonical
    )
    _persist_immutable(_envelope_path(store.root, envelope), envelope.canonical)
    return WorkflowJobAuthorization(store.root, envelope, business, execution)


def load_workflow_job_authorization(
    store: WorkflowRunManifestStore, envelope: WorkflowJobAuthorizationEnvelope
) -> WorkflowJobAuthorization:
    """Strictly reload both grants and envelope from the Run namespace."""
    _check_store_permissions(store.root)
    loaded_envelope = _load_envelope(_envelope_path(store.root, envelope))
    business = _load_grant(_grant_path(store.root, envelope, "business"))
    execution = _load_grant(_grant_path(store.root, envelope, "execution"))
    if not (
        loaded_envelope == envelope
        and business.purpose == "job_business"
        and execution.purpose == "job_execution"
        and business.job_id == execution.job_id == envelope.job_id
        and business.envelope_sha256 == execution.envelope_sha256 == envelope.digest
        and business.grant_id != execution.grant_id
    ):
        _raise("grant_binding")
    manifest = load_workflow_run_manifest(store, envelope.run_id)
    if (
        manifest.digest != envelope.manifest_digest
        or manifest.workflow_id != envelope.workflow_id
    ):
        _raise("manifest")
    return WorkflowJobAuthorization(store.root, envelope, business, execution)


def load_workflow_job_authorization_by_identity(
    store: WorkflowRunManifestStore, run_id: str, job_id: str
) -> WorkflowJobAuthorization:
    """Reload persisted authorization using only its Run/Job identity."""
    if not _identifier(run_id) or not _identifier(job_id):
        _raise("identity")
    root = store.root
    path = root / f"{run_id}.job-envelope.{job_id}.json"
    envelope = _load_envelope(path)
    if envelope.run_id != run_id or envelope.job_id != job_id:
        _raise("identity")
    return load_workflow_job_authorization(store, envelope)


def validate_job_business_authority(
    authorization: WorkflowJobAuthorization,
    *,
    binding: WorkflowRunBinding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    run_input: str,
    now_ns: int | None = None,
) -> WorkflowJobAuthorization:
    """Validate the business grant against pinned Run and current input."""
    current = _reload_and_check_common(authorization, binding, now_ns)
    envelope = current.envelope
    if not (
        envelope.workflow_id == workflow_id
        and envelope.step_id == step_id
        and envelope.step_index == step_index
        and envelope.employee_id == employee_id
        and envelope.run_input_sha256 == _sha(run_input.encode("utf-8"))
    ):
        _raise("business_scope")
    return current


def validate_job_execution_authority(
    authorization: WorkflowJobAuthorization,
    *,
    binding: WorkflowRunBinding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    target: ModelExecutionTarget,
    now_ns: int | None = None,
) -> WorkflowJobAuthorization:
    """Validate exact request/target scope before a single shared dispatch."""
    current = validate_job_business_authority(
        authorization,
        binding=binding,
        workflow_id=workflow_id,
        step_id=step_id,
        step_index=step_index,
        employee_id=employee_id,
        run_input=request.run_input,
        now_ns=now_ns,
    )
    try:
        target = validate_execution_target_for_provider(
            target, provider=target.provider
        )
        request_fingerprint = build_model_invocation_execution_fingerprint(
            request, resolved_tools, target
        )
    except Exception:
        _raise("execution_scope")
    envelope = current.envelope
    if not (
        request.run_id == binding.run_id
        and request.manifest_digest == binding.manifest_digest
        and request.model == envelope.model
        and target.provider == envelope.provider
        and execution_target_fingerprint(target)
        == envelope.execution_target_fingerprint
        and request_fingerprint == envelope.request_fingerprint
    ):
        _raise("execution_scope")
    _validate_dispatch_not_consumed(current)
    return current


def reserve_job_attempt(
    authorization: WorkflowJobAuthorization,
    *,
    binding: WorkflowRunBinding,
    workflow_id: str,
    step_id: str,
    step_index: int,
    employee_id: str,
    request: ModelInvocationRequest,
    resolved_tools: tuple[ToolDefinition, ...],
    target: ModelExecutionTarget,
    request_body_sha256: str,
    request_body_length: int,
    now_ns: int | None = None,
) -> str:
    """Atomically consume this Job's single provider-attempt slot before claim."""
    if (
        not _digest(request_body_sha256)
        or type(request_body_length) is not int
        or request_body_length < 0
    ):
        _raise("reservation_request")
    current = validate_job_execution_authority(
        authorization,
        binding=binding,
        workflow_id=workflow_id,
        step_id=step_id,
        step_index=step_index,
        employee_id=employee_id,
        request=request,
        resolved_tools=resolved_tools,
        target=target,
        now_ns=now_ns,
    )
    reservation = _canonical(
        {
            "business_grant_sha256": current.business_grant.digest,
            "envelope_sha256": current.envelope.digest,
            "execution_grant_sha256": current.execution_grant.digest,
            "job_id": current.envelope.job_id,
            "request_body_length": request_body_length,
            "request_body_sha256": request_body_sha256,
            "schema_version": _RESERVATION_SCHEMA,
        }
    )
    path = _reservation_path(current.store_root, current.envelope)
    _persist_exclusive(path, reservation)
    return _sha(reservation)


def admit_job_dispatch(
    authorization: WorkflowJobAuthorization,
    *,
    attempt_id: str,
    attempt_evidence_sha256: str,
    reservation_sha256: str,
    now_ns: int | None = None,
) -> None:
    """Write the final expiry/revocation decision after attempt claim.

    A crash after this record is a consumed, ambiguous action. The ordinary
    Run attempt claim prevents replay; this record does not promise provider
    exactly-once or that an admitted request can be cancelled.
    """
    if (
        not _identifier(attempt_id)
        or not _digest(attempt_evidence_sha256)
        or not _digest(reservation_sha256)
    ):
        _raise("dispatch")
    with _job_lifecycle_lock(authorization.store_root, authorization.envelope):
        # This locked re-read is the shared linearization boundary with revoke.
        # Expiry is checked here, not at the earlier reservation or Run claim.
        current = _reload_and_check_common(
            authorization, authorization_binding(authorization), now_ns
        )
        _validate_dispatch_not_consumed(current)
        try:
            from ai_office.execution_evidence import load_execution_attempt_evidence

            attempt = load_execution_attempt_evidence(
                current.store_root, current.envelope.run_id, attempt_id
            )
        except Exception:
            _raise("attempt_missing")
        if not (
            attempt.digest == attempt_evidence_sha256
            and attempt.authorization_source == "job_grant"
            and attempt.job_id == current.envelope.job_id
            and attempt.job_envelope_sha256 == current.envelope.digest
            and attempt.job_business_grant_sha256 == current.business_grant.digest
            and attempt.job_execution_grant_sha256 == current.execution_grant.digest
            and attempt.invocation_fingerprint == current.envelope.request_fingerprint
            and attempt.job_reservation_sha256 == reservation_sha256
            and _read_regular(_reservation_path(current.store_root, current.envelope))
            == _reservation_payload(
                current, attempt.request_body_sha256, attempt.request_body_length
            )
        ):
            _raise("attempt_binding")
        # Re-read the clock and authority after the evidence work above. This
        # is the expiry decision recorded as the durable admission event.
        admitted_at_ns = time.time_ns() if now_ns is None else now_ns
        current = _reload_and_check_common(
            authorization,
            authorization_binding(authorization),
            admitted_at_ns,
        )
        path = _dispatch_path(current.store_root, current.envelope)
        payload = _dispatch_payload(
            current,
            attempt_id=attempt_id,
            attempt_evidence_sha256=attempt_evidence_sha256,
            reservation_sha256=reservation_sha256,
            admitted_at_ns=admitted_at_ns,
        )
        _persist_immutable(path, payload)


def validate_dispatch_admission(
    authorization: WorkflowJobAuthorization,
    *,
    attempt_id: str,
    attempt_evidence_sha256: str,
    reservation_sha256: str,
) -> None:
    current = load_workflow_job_authorization(
        WorkflowRunManifestStore(authorization.store_root), authorization.envelope
    )
    path = _dispatch_path(current.store_root, current.envelope)
    actual = _read_regular(path)
    try:
        value = json.loads(actual.decode("utf-8"))
        admitted_at_ns = value["admitted_at_ns"]
    except Exception:
        _raise("dispatch_evidence")
    expected = _dispatch_payload(
        current,
        attempt_id=attempt_id,
        attempt_evidence_sha256=attempt_evidence_sha256,
        reservation_sha256=reservation_sha256,
        admitted_at_ns=admitted_at_ns,
    )
    if actual != expected:
        _raise("dispatch_evidence")


def validate_job_reservation(
    authorization: WorkflowJobAuthorization,
    *,
    reservation_sha256: str,
    request_body_sha256: str,
    request_body_length: int,
) -> None:
    """Strictly rejoin a consumed reservation to grant and exact wire body."""
    current = load_workflow_job_authorization(
        WorkflowRunManifestStore(authorization.store_root), authorization.envelope
    )
    expected = _reservation_payload(current, request_body_sha256, request_body_length)
    actual = _read_regular(_reservation_path(current.store_root, current.envelope))
    if actual != expected or _sha(actual) != reservation_sha256:
        _raise("reservation_binding")


def job_dispatch_is_admitted(
    store: WorkflowRunManifestStore,
    run_id: str,
    job_id: str,
    attempt_id: str,
    attempt_evidence_sha256: str,
) -> bool:
    """Read-only check for the consumed reservation/dispatch-admission link."""
    authorization = load_workflow_job_authorization_by_identity(store, run_id, job_id)
    validate_dispatch_admission(
        authorization,
        attempt_id=attempt_id,
        attempt_evidence_sha256=attempt_evidence_sha256,
        reservation_sha256=_sha(
            _read_regular(
                _reservation_path(authorization.store_root, authorization.envelope)
            )
        ),
    )
    return True


def revoke_workflow_job_authorization(
    authorization: WorkflowJobAuthorization, *, now_ns: int | None = None
) -> None:
    with _job_lifecycle_lock(authorization.store_root, authorization.envelope):
        current = load_workflow_job_authorization(
            WorkflowRunManifestStore(authorization.store_root), authorization.envelope
        )
        dispatch_path = _dispatch_path(current.store_root, current.envelope)
        dispatch_digest: str | None = None
        lifecycle_sequence = 1
        if _path_exists_or_symlink(dispatch_path):
            dispatch_value = _read_regular(dispatch_path)
            _validate_dispatch_record(current, dispatch_value)
            dispatch_digest = _sha(dispatch_value)
            lifecycle_sequence = 2
        payload = _canonical(
            {
                "dispatch_admission_sha256": dispatch_digest,
                "envelope_sha256": current.envelope.digest,
                "job_id": current.envelope.job_id,
                "lifecycle_sequence": lifecycle_sequence,
                "revoked_at_ns": time.time_ns() if now_ns is None else now_ns,
                "schema_version": "workflow-job-authorization-revocation.v1",
            }
        )
        _persist_immutable(
            _revocation_path(current.store_root, current.envelope), payload
        )


def inspect_workflow_job_authorization(
    authorization: WorkflowJobAuthorization,
) -> WorkflowJobAuthorizationInspection:
    """Return a concise read-only allowance/dispatch status."""
    current = load_workflow_job_authorization(
        WorkflowRunManifestStore(authorization.store_root), authorization.envelope
    )
    reservation = _reservation_path(current.store_root, current.envelope)
    dispatch = _dispatch_path(current.store_root, current.envelope)
    reserved = reservation.exists() or reservation.is_symlink()
    if reservation.is_symlink():
        _raise("reservation_path")
    if reserved:
        try:
            value = json.loads(_read_regular(reservation).decode("utf-8"))
            expected = _reservation_payload(
                current,
                value["request_body_sha256"],
                value["request_body_length"],
            )
        except WorkflowJobAuthorizationError:
            raise
        except Exception:
            _raise("reservation_evidence")
        if _read_regular(reservation) != expected:
            _raise("reservation_evidence")
    dispatch_admitted = False
    response_recorded = False
    dispatch_present = dispatch.exists() or dispatch.is_symlink()
    if dispatch.is_symlink():
        _raise("dispatch_path")
    if dispatch_present:
        attempt_id = _attempt_id_for_job(current)
        if attempt_id == "unclaimed":
            _raise("dispatch_attempt_missing")
        from ai_office.execution_evidence import (
            load_execution_attempt_evidence,
            load_raw_response_evidence,
        )

        attempt = load_execution_attempt_evidence(
            current.store_root, current.envelope.run_id, attempt_id
        )
        dispatch_admitted_bytes = _read_regular(dispatch)
        validate_dispatch_admission(
            current,
            attempt_id=attempt_id,
            attempt_evidence_sha256=attempt.digest,
            reservation_sha256=_sha(_read_regular(reservation)),
        )
        _validate_dispatch_record(current, dispatch_admitted_bytes)
        dispatch_admitted = True
        raw_path = current.store_root / (
            f"{current.envelope.run_id}.execution-raw-response.{attempt_id}.json"
        )
        if _path_exists_or_symlink(raw_path):
            load_raw_response_evidence(
                current.store_root, current.envelope.run_id, attempt_id
            )
            response_recorded = True
    revocation = _revocation_path(current.store_root, current.envelope)
    revoked = _path_exists_or_symlink(revocation)
    lifecycle_order = "dispatch_admitted" if dispatch_admitted else "not_dispatched"
    if revoked:
        if revocation.is_symlink():
            _raise("revocation_path")
        revocation_bytes = _read_regular(revocation)
        revocation_value = _load_revocation_record(current, revocation_bytes)
        if dispatch_admitted:
            if not (
                revocation_value["lifecycle_sequence"] == 2
                and revocation_value["dispatch_admission_sha256"]
                == _sha(_read_regular(dispatch))
            ):
                _raise("revocation_order")
            lifecycle_order = "dispatch_before_revocation"
        else:
            if not (
                revocation_value["lifecycle_sequence"] == 1
                and revocation_value["dispatch_admission_sha256"] is None
            ):
                _raise("revocation_order")
            lifecycle_order = "revocation_before_dispatch"
    return WorkflowJobAuthorizationInspection(
        job_id=current.envelope.job_id,
        run_id=current.envelope.run_id,
        envelope_sha256=current.envelope.digest,
        business_grant_id=current.business_grant.grant_id,
        execution_grant_id=current.execution_grant.grant_id,
        max_provider_attempts=current.envelope.max_provider_attempts,
        max_revisions=current.envelope.max_revisions,
        attempts_reserved=1 if reserved else 0,
        dispatch_admitted=dispatch_admitted,
        provider_response_recorded=response_recorded,
        expires_at_ns=current.envelope.expires_at_ns,
        revoked=revoked,
        lifecycle_order=lifecycle_order,
    )


def _attempt_id_for_job(authorization: WorkflowJobAuthorization) -> str:
    reservation_path = _reservation_path(
        authorization.store_root, authorization.envelope
    )
    try:
        value = json.loads(_read_regular(reservation_path).decode("utf-8"))
        from ai_office.execution_evidence import list_run_execution_evidence

        attempts = list_run_execution_evidence(
            authorization.store_root, authorization.envelope.run_id
        )
        for attempt in attempts:
            if (
                attempt.authorization_source == "job_grant"
                and attempt.job_id == authorization.envelope.job_id
                and attempt.request_body_sha256 == value.get("request_body_sha256")
            ):
                return attempt.attempt_id
    except Exception:
        return "unclaimed"
    return "unclaimed"


def _reload_and_check_common(
    authorization: WorkflowJobAuthorization,
    binding: WorkflowRunBinding,
    now_ns: int | None,
) -> WorkflowJobAuthorization:
    if type(authorization) is not WorkflowJobAuthorization:
        _raise("authorization")
    current = load_workflow_job_authorization(
        WorkflowRunManifestStore(authorization.store_root), authorization.envelope
    )
    envelope = current.envelope
    now = time.time_ns() if now_ns is None else now_ns
    if not (
        envelope.run_id == binding.run_id
        and envelope.manifest_digest == binding.manifest_digest
        and now < envelope.expires_at_ns
        and not _path_exists_or_symlink(_revocation_path(current.store_root, envelope))
    ):
        _raise("expired_revoked_or_binding")
    return current


def _validate_dispatch_not_consumed(authorization: WorkflowJobAuthorization) -> None:
    if _path_exists_or_symlink(
        _dispatch_path(authorization.store_root, authorization.envelope)
    ):
        _raise("already_consumed")


def _path_exists_or_symlink(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def authorization_binding(
    authorization: WorkflowJobAuthorization,
) -> WorkflowRunBinding:
    return WorkflowRunBinding(
        authorization.envelope.run_id, authorization.envelope.manifest_digest
    )


def _grant_path(
    root: Path, envelope: WorkflowJobAuthorizationEnvelope, purpose: str
) -> Path:
    return root / f"{envelope.run_id}.job-{purpose}.{envelope.job_id}.json"


def _envelope_path(root: Path, envelope: WorkflowJobAuthorizationEnvelope) -> Path:
    return root / f"{envelope.run_id}.job-envelope.{envelope.job_id}.json"


def _dispatch_path(root: Path, envelope: WorkflowJobAuthorizationEnvelope) -> Path:
    return root / f"{envelope.run_id}.job-dispatch.{envelope.job_id}.json"


def _reservation_path(root: Path, envelope: WorkflowJobAuthorizationEnvelope) -> Path:
    return root / f"{envelope.run_id}.job-reservation.{envelope.job_id}.json"


def _reservation_payload(
    authorization: WorkflowJobAuthorization,
    request_body_sha256: str,
    request_body_length: int,
) -> bytes:
    return _canonical(
        {
            "business_grant_sha256": authorization.business_grant.digest,
            "envelope_sha256": authorization.envelope.digest,
            "execution_grant_sha256": authorization.execution_grant.digest,
            "job_id": authorization.envelope.job_id,
            "request_body_length": request_body_length,
            "request_body_sha256": request_body_sha256,
            "schema_version": _RESERVATION_SCHEMA,
        }
    )


def _dispatch_payload(
    authorization: WorkflowJobAuthorization,
    *,
    attempt_id: str,
    attempt_evidence_sha256: str,
    reservation_sha256: str,
    admitted_at_ns: int,
) -> bytes:
    if (
        type(admitted_at_ns) is not int
        or admitted_at_ns >= authorization.envelope.expires_at_ns
    ):
        _raise("dispatch_expired")
    return _canonical(
        {
            "admitted_at_ns": admitted_at_ns,
            "attempt_evidence_sha256": attempt_evidence_sha256,
            "attempt_id": attempt_id,
            "business_grant_sha256": authorization.business_grant.digest,
            "envelope_sha256": authorization.envelope.digest,
            "execution_grant_sha256": authorization.execution_grant.digest,
            "job_id": authorization.envelope.job_id,
            "lifecycle_sequence": 1,
            "reservation_sha256": reservation_sha256,
            "schema_version": _DISPATCH_SCHEMA,
        }
    )


def _validate_dispatch_record(
    authorization: WorkflowJobAuthorization, payload: bytes
) -> None:
    try:
        value = json.loads(payload.decode("utf-8"))
        if not (
            type(value) is dict
            and value.get("schema_version") == _DISPATCH_SCHEMA
            and type(value.get("lifecycle_sequence")) is int
            and value.get("lifecycle_sequence") == 1
            and type(value.get("admitted_at_ns")) is int
            and value["admitted_at_ns"] < authorization.envelope.expires_at_ns
        ):
            _raise("dispatch_evidence")
        expected = _dispatch_payload(
            authorization,
            attempt_id=value["attempt_id"],
            attempt_evidence_sha256=value["attempt_evidence_sha256"],
            reservation_sha256=value["reservation_sha256"],
            admitted_at_ns=value["admitted_at_ns"],
        )
        if payload != expected:
            _raise("dispatch_evidence")
    except WorkflowJobAuthorizationError:
        raise
    except Exception:
        _raise("dispatch_evidence")


def _load_revocation_record(
    authorization: WorkflowJobAuthorization, payload: bytes
) -> dict[str, object]:
    try:
        value = json.loads(payload.decode("utf-8"))
        if (
            type(value) is not dict
            or value.get("schema_version") != "workflow-job-authorization-revocation.v1"
            or value.get("job_id") != authorization.envelope.job_id
            or value.get("envelope_sha256") != authorization.envelope.digest
            or type(value.get("lifecycle_sequence")) is not int
            or value.get("lifecycle_sequence") not in {1, 2}
        ):
            _raise("revocation_evidence")
        dispatch_sha = value.get("dispatch_admission_sha256")
        if (value["lifecycle_sequence"] == 1 and dispatch_sha is not None) or (
            value["lifecycle_sequence"] == 2 and not _digest(dispatch_sha)
        ):
            _raise("revocation_evidence")
        if type(value.get("revoked_at_ns")) is not int:
            _raise("revocation_evidence")
        if payload != _canonical(value):
            _raise("revocation_evidence")
        return value
    except WorkflowJobAuthorizationError:
        raise
    except Exception:
        _raise("revocation_evidence")


@contextmanager
def _job_lifecycle_lock(
    store_root: Path, envelope: WorkflowJobAuthorizationEnvelope
) -> Iterator[None]:
    """Serialize local revocation and dispatch decisions for one Job.

    This relies on POSIX advisory locks on a local filesystem. Unsupported or
    unsafe lock files fail closed; network/multi-host filesystems are outside
    this offline authorization slice.
    """
    _check_store_permissions(store_root)
    path = store_root / f"{envelope.run_id}.job-lifecycle-lock.{envelope.job_id}"
    if path.is_symlink():
        _raise("lifecycle_lock")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            _raise("lifecycle_lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except WorkflowJobAuthorizationError:
        raise
    except Exception:
        _raise("lifecycle_lock")
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _revocation_path(root: Path, envelope: WorkflowJobAuthorizationEnvelope) -> Path:
    return root / f"{envelope.run_id}.job-revoked.{envelope.job_id}.json"


def _persist_immutable(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        _raise("evidence_path")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        try:
            if _read_regular(path) != payload:
                _raise("evidence_conflict")
        except WorkflowJobAuthorizationError:
            raise
        except Exception:
            _raise("evidence_conflict")
    except Exception:
        _raise("evidence_persistence")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _persist_exclusive(path: Path, payload: bytes) -> None:
    """Reservation is a one-use atomic claim; even identical retries fail."""
    if path.is_symlink():
        _raise("reservation_path")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        _raise("attempt_allowance_depleted")
    except Exception:
        _raise("reservation_persistence")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_envelope(path: Path) -> WorkflowJobAuthorizationEnvelope:
    try:
        value = json.loads(_read_regular(path).decode("utf-8"))
        if value.pop("schema_version") != _ENVELOPE_SCHEMA:
            _raise("envelope_schema")
        record = WorkflowJobAuthorizationEnvelope(**value)
        if record.canonical != _read_regular(path):
            _raise("envelope_canonical")
        return record
    except WorkflowJobAuthorizationError:
        raise
    except Exception:
        _raise("envelope_load")


def _load_grant(path: Path) -> WorkflowJobGrant:
    try:
        value = json.loads(_read_regular(path).decode("utf-8"))
        record = WorkflowJobGrant(**value)
        if record.canonical != _read_regular(path):
            _raise("grant_canonical")
        return record
    except WorkflowJobAuthorizationError:
        raise
    except Exception:
        _raise("grant_load")


def _read_regular(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        _raise("evidence_missing")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid():
        _raise("evidence_permissions")
    return path.read_bytes()


def _check_store_permissions(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        _raise("store")
    info = root.stat()
    if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid():
        _raise("store_permissions")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return sha256(value).hexdigest()


def _identifier(value: object) -> bool:
    return type(value) is str and bool(_IDENTIFIER.fullmatch(value))


def _digest(value: object) -> bool:
    return type(value) is str and bool(_DIGEST.fullmatch(value))


def _raise(classification: str) -> None:
    raise WorkflowJobAuthorizationError(classification)


__all__ = [
    "WorkflowJobAuthorization",
    "WorkflowJobAuthorizationEnvelope",
    "WorkflowJobAuthorizationError",
    "WorkflowJobAuthorizationInspection",
    "WorkflowJobGrant",
    "admit_job_dispatch",
    "issue_workflow_job_authorization",
    "inspect_workflow_job_authorization",
    "load_workflow_job_authorization",
    "revoke_workflow_job_authorization",
    "reserve_job_attempt",
    "validate_dispatch_admission",
    "validate_job_reservation",
    "validate_job_business_authority",
    "validate_job_execution_authority",
]
