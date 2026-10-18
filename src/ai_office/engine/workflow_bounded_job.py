"""Finite, configuration-bound Job authority spanning immutable Workflow Runs.

This remains an authorization owner, not an execution engine. Each exact
Run/Step action is materialized from the approved action slots only after its
immutable Run manifest and any predecessor Artifact have been reloaded and
checked. Dispatch still goes through the existing provider and Execution
Evidence path.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from ai_office.definitions.workflow import LoadedWorkflow, WorkflowDefinition
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


def bounded_review_action_plan(
    workflow: LoadedWorkflow, max_revisions: int
) -> tuple[tuple[str, int, str], ...]:
    """Derive the finite plan only from the pinned business definition.

    The reviewer count and order come entirely from the declared
    ``review_steps`` list, so the shared engine never fixes a reviewer count.
    """
    definition = workflow.definition
    flow = definition.bounded_review
    if flow is None or not 0 <= max_revisions <= flow.max_revisions:
        raise WorkflowJobAuthorizationError("document_job_flow")
    plan = [(flow.draft_step, 1, "assignment")]
    plan.extend((review, 1, "review_input") for review in flow.review_steps)
    for _ in range(max_revisions):
        plan.append((flow.revision_step, 1, "revision_input"))
        plan.extend((review, 1, "re_review_input") for review in flow.review_steps)
    return tuple(plan)


_PRODUCER_INPUT_SOURCES = frozenset(
    {"assignment", "previous_step", "previous_artifact", "revision_input"}
)
_REVIEW_INPUT_SOURCES = frozenset({"review_input", "re_review_input"})


def _producer_ordinal(
    actions: tuple[BoundedJobActionSlot, ...], ordinal: int
) -> int:
    """Return the ordinal of the Artifact-producing action for ``ordinal``.

    Reviewers in the same round share one predecessor Artifact, so the source
    is the most recent producer, not necessarily ``ordinal - 1``.
    """
    for index in range(ordinal - 1, -1, -1):
        if actions[index].input_source in _PRODUCER_INPUT_SOURCES:
            return index
    raise WorkflowJobAuthorizationError("job_action_predecessor")


def _round_review_ordinals(
    actions: tuple[BoundedJobActionSlot, ...], ordinal: int
) -> tuple[int, ...]:
    """Return every reviewer ordinal in the round ending before ``ordinal``."""
    source = _producer_ordinal(actions, ordinal)
    reviewers = tuple(range(source + 1, ordinal))
    if not reviewers or any(
        actions[index].input_source not in _REVIEW_INPUT_SOURCES
        for index in reviewers
    ):
        raise WorkflowJobAuthorizationError("job_action_predecessor")
    return reviewers


def bounded_job_execution_view(
    workflow: LoadedWorkflow, step_id: str
) -> LoadedWorkflow:
    """Job-private one-step view; never a selectable or stored source definition."""
    source = workflow.definition
    flow = source.bounded_review
    if flow is None or step_id not in {
        flow.draft_step,
        *flow.review_steps,
        flow.revision_step,
    }:
        raise WorkflowJobAuthorizationError("document_job_view")
    step = next(step for step in source.steps if step.id == step_id)
    return LoadedWorkflow(
        source_path=workflow.source_path,
        definition=WorkflowDefinition(
            id=source.id,
            name=source.name,
            description=source.description,
            steps=[step],
        ),
    )


def _validate_derived_review_plan(
    initial_manifest, actions, max_revisions: int
) -> None:
    """Bind every execution view to the immutable source Run snapshot."""
    from ai_office.engine.workflow_run_manifest import (
        build_workflow_run_manifest,
        loaded_employees_from_run_manifest,
        loaded_workflow_from_run_manifest,
    )

    source = loaded_workflow_from_run_manifest(initial_manifest)
    if source.definition.bounded_review is None:
        return  # Existing generic Job scopes retain their own validation.
    expected = bounded_review_action_plan(source, max_revisions)
    if len(actions) != len(expected):
        raise WorkflowJobAuthorizationError("document_job_source_plan")
    employees = loaded_employees_from_run_manifest(initial_manifest)
    for ordinal, (step_id, step_index, input_source) in enumerate(expected):
        slot = actions[ordinal]
        selected = next(step for step in source.definition.steps if step.id == step_id)
        view = bounded_job_execution_view(source, step_id)
        snapshot = build_workflow_run_manifest(
            f"derived-{ordinal}",
            initial_manifest.run_input,
            view,
            employees,
            execution_model=slot.model,
            execution_target=initial_manifest.execution_destination.target,
        )
        employee = next(
            item for item in snapshot.employee_snapshots if item.id == selected.employee
        )
        if not (
            slot.workflow_id == source.definition.id
            and slot.step_id == step_id
            and slot.step_index == step_index
            and slot.input_source == input_source
            and slot.employee_id == selected.employee
            and slot.workflow_snapshot_sha256
            == _digest(_canonical(_workflow_dict(snapshot.workflow_snapshot)))
            and slot.employee_snapshot_sha256
            == _digest(_canonical(_employee_dict(employee)))
            and slot.execution_target_fingerprint
            == initial_manifest.execution_destination.fingerprint
            and slot.provider == initial_manifest.execution_destination.provider
        ):
            raise WorkflowJobAuthorizationError("document_job_source_plan")


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
    max_retries: int = 0
    retry_interval_seconds: int = 30
    source_manifest_run_id: str | None = None
    source_manifest_digest: str | None = None
    schema_version: str = "workflow-bounded-job-scope.v2"

    def __post_init__(self) -> None:
        if not _identifier(self.job_id):
            raise WorkflowJobAuthorizationError("job_scope_identity")
        first_review = next(
            (
                index
                for index, item in enumerate(self.actions)
                if item.input_source == "review_input"
            ),
            None,
        )
        reviewer_count = sum(
            1 for item in self.actions if item.input_source == "review_input"
        )
        # Normal actions = creator steps + N first-round reviews + R rounds of
        # one revision plus N re-reviews.  For the shipped shape with a single
        # creator step this is the declared (R + 1) * (N + 1) send budget.
        normal_action_limit = (
            min(
                len(self.actions),
                first_review
                + reviewer_count * (self.max_revisions + 1)
                + self.max_revisions,
            )
            if first_review is not None and type(self.max_revisions) is int
            else len(self.actions)
        )
        if (
            not _valid_digest(self.assignment_sha256)
            or not _valid_digest(self.initial_input_sha256)
            or not _identifier(self.initial_run_id)
        ):
            raise WorkflowJobAuthorizationError("job_scope_digest")
        if (self.source_manifest_run_id is None) != (
            self.source_manifest_digest is None
        ):
            raise WorkflowJobAuthorizationError("job_scope_source")
        if self.source_manifest_run_id is not None and (
            not _identifier(self.source_manifest_run_id)
            or not _valid_digest(self.source_manifest_digest)
        ):
            raise WorkflowJobAuthorizationError("job_scope_source")
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
            or type(self.max_revisions) is not int
            or self.max_revisions < 0
            or self.max_revisions * (reviewer_count + 1) > len(self.actions)
            or not min(len(self.actions), normal_action_limit)
            <= self.max_provider_attempts
            <= normal_action_limit + 2
            or type(self.max_retries) is not int
            or not 0 <= self.max_retries <= 2
            or type(self.retry_interval_seconds) is not int
            or not 1 <= self.retry_interval_seconds <= 3600
            or self.max_provider_attempts != normal_action_limit + self.max_retries
            or self.schema_version != "workflow-bounded-job-scope.v2"
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
                self.max_retries,
                self.retry_interval_seconds,
                self.source_manifest_run_id,
                self.source_manifest_digest,
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
                **(
                    {"retryable_http_statuses": [429, 503]}
                    if self.schema_version == "workflow-bounded-job-scope.v2"
                    else {}
                ),
                **(
                    {
                        "max_retries": self.max_retries,
                        "retry_interval_seconds": self.retry_interval_seconds,
                    }
                    if self.schema_version == "workflow-bounded-job-scope.v2"
                    else {}
                ),
                "schema_version": self.schema_version,
                **(
                    {
                        "source_manifest_run_id": self.source_manifest_run_id,
                        "source_manifest_digest": self.source_manifest_digest,
                    }
                    if self.source_manifest_run_id is not None
                    else {}
                ),
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
    max_retries: int = 0,
    retry_interval_seconds: int = 30,
    source_manifest_run_id: str | None = None,
    source_manifest_digest: str | None = None,
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
        max_retries,
        retry_interval_seconds,
        source_manifest_run_id,
        source_manifest_digest,
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
    if source_manifest_run_id is not None:
        source_manifest = load_workflow_run_manifest(store, source_manifest_run_id)
        if (
            source_manifest.digest != source_manifest_digest
            or source_manifest.run_input != initial_manifest.run_input
            or source_manifest.execution_destination
            != initial_manifest.execution_destination
            or source_manifest.workflow_id != initial_manifest.workflow_id
            or store.state_path(source_manifest_run_id).exists()
            or store.events_path(source_manifest_run_id).exists()
        ):
            raise WorkflowJobAuthorizationError("job_source_manifest")
        _validate_derived_review_plan(source_manifest, actions, max_revisions)
    initial_employee = next(
        (
            employee
            for employee in initial_manifest.employee_snapshots
            if employee.id == actions[0].employee_id
        ),
        None,
    )
    if (
        initial_manifest.execution_destination.fingerprint
        != actions[0].execution_target_fingerprint
        or initial_manifest.execution_destination.provider != actions[0].provider
        or initial_employee is None
        or initial_employee.model != actions[0].model
    ):
        raise WorkflowJobAuthorizationError("job_initial_manifest_authorization")
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
        max_retries,
        retry_interval_seconds,
        source_manifest_run_id,
        source_manifest_digest,
    )
    # This root-scope digest is distinct from Run-bound authorization records.
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
        schema_version = value.pop("schema_version")
        if schema_version != "workflow-bounded-job-scope.v2":
            raise ValueError
        if value.pop("retryable_http_statuses") != [429, 503]:
            raise ValueError
        business = WorkflowJobGrant(**value.pop("business_grant"))
        execution = WorkflowJobGrant(**value.pop("execution_grant"))
        actions = tuple(BoundedJobActionSlot(**item) for item in value.pop("actions"))
        scope = BoundedWorkflowJobScope(
            actions=actions,
            business_grant=business,
            execution_grant=execution,
            schema_version=schema_version,
            **value,
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
    if (
        current.actions[ordinal].input_source in {"revision_input", "re_review_input"}
        and current.max_revisions == 0
    ):
        raise WorkflowJobAuthorizationError("document_job_revision_budget_exhausted")
    if not _identifier(run_id) or not _valid_digest(request_fingerprint):
        raise WorkflowJobAuthorizationError("job_action_identity")
    manifest = load_workflow_run_manifest(store, run_id)
    slot = current.actions[ordinal]
    if current.source_manifest_run_id is not None:
        source_manifest = load_workflow_run_manifest(
            store, current.source_manifest_run_id
        )
        if (
            source_manifest.digest != current.source_manifest_digest
            or source_manifest.run_input
            != load_workflow_run_manifest(store, current.initial_run_id).run_input
        ):
            raise WorkflowJobAuthorizationError("job_source_manifest")
        _validate_derived_review_plan(
            source_manifest, current.actions, current.max_revisions
        )
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
        prior_dispatch = _latest_dispatch_for_action(store, current.job_id, ordinal - 1)
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
        source_ordinal = _producer_ordinal(current.actions, ordinal)
        prior_authorization = load_bounded_job_action_by_identity(
            store, current.job_id, source_ordinal
        )
        prior_dispatch = _latest_dispatch_for_action(
            store, current.job_id, source_ordinal
        )
        if (
            prior_authorization.run_id != pred_run
            or prior_authorization.ordinal != source_ordinal
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
    retry_index: int = 0,
    now_ns: int | None = None,
) -> str:
    """Consume one Job-wide slot before shared Run/Step attempt claim."""
    if (
        not _valid_digest(request_body_sha256)
        or type(request_body_length) is not int
        or request_body_length < 0
        or type(retry_index) is not int
        or retry_index < 0
    ):
        raise WorkflowJobAuthorizationError("job_reservation")
    with _job_lock(authorization.store_root, authorization.job_id):
        _require_persisted_action_authorization(authorization)
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
        action_records = [
            item
            for item in records
            if item.get("action_sha256") == authorization.action_sha256
        ]
        if len(action_records) != retry_index:
            raise WorkflowJobAuthorizationError("job_action_replayed")
        if retry_index:
            if (
                scope.schema_version != "workflow-bounded-job-scope.v2"
                or retry_index > scope.max_retries
                or sum(1 for item in records if item.get("retry_index", 0) > 0)
                >= scope.max_retries
                or not _prior_job_attempts_are_retryable(
                    authorization,
                    expected_count=retry_index,
                    request_body_sha256=request_body_sha256,
                )
            ):
                raise WorkflowJobAuthorizationError("job_retry_not_authorized")
        reservation_index = len(records)
        payload = _canonical(
            {
                "action_sha256": authorization.action_sha256,
                "ordinal": authorization.ordinal,
                "request_body_length": request_body_length,
                "request_body_sha256": request_body_sha256,
                "reservation_index": reservation_index,
                "retry_index": retry_index,
                "run_id": authorization.run_id,
                "schema_version": "workflow-bounded-job-reservation.v2",
                "scope_sha256": scope.digest,
            }
        )
        _persist_immutable(
            _reservation_path(
                authorization.store_root,
                scope.job_id,
                authorization.ordinal,
                retry_index,
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
        _require_persisted_action_authorization(authorization)
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
        reservation = next(
            (
                _read_regular(path)
                for path in _reservation_paths(authorization.store_root, scope.job_id)
                if _digest(_read_regular(path)) == reservation_sha256
            ),
            None,
        )
        if reservation is None:
            raise WorkflowJobAuthorizationError("job_reservation_binding")
        reservation_value = json.loads(reservation)
        retry_index = reservation_value.get("retry_index", 0)
        if (
            reservation_value.get("ordinal") != authorization.ordinal
            or reservation_value.get("action_sha256") != authorization.action_sha256
            or reservation_value.get("scope_sha256") != scope.digest
            or reservation_value.get("run_id") != authorization.run_id
        ):
            raise WorkflowJobAuthorizationError("job_reservation_binding")
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
                "ordinal": authorization.ordinal,
                "retry_index": retry_index,
                "reservation_sha256": reservation_sha256,
                "schema_version": "workflow-bounded-job-dispatch.v2",
                "scope_sha256": scope.digest,
            }
        )
        _persist_immutable(
            _dispatch_path(
                authorization.store_root,
                scope.job_id,
                authorization.ordinal,
                retry_index,
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
    authorization = _require_persisted_action_authorization(authorization)
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
    authorization = _require_persisted_action_authorization(authorization)
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
            prior_dispatch = _latest_dispatch_for_action(store, job_id, ordinal - 1)
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
                _read_regular(
                    _action_path(
                        store.root, job_id, _producer_ordinal(scope.actions, ordinal)
                    )
                )
            )
            prior_dispatch = _latest_dispatch_for_action(
                store, job_id, _producer_ordinal(scope.actions, ordinal)
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


def _require_persisted_action_authorization(
    authorization: BoundedJobActionAuthorization,
) -> BoundedJobActionAuthorization:
    """Rejoin caller-held action data to its immutable, fully derived record."""
    persisted = load_bounded_job_action_by_identity(
        WorkflowRunManifestStore(authorization.store_root),
        authorization.job_id,
        authorization.ordinal,
    )
    if persisted != authorization:
        raise WorkflowJobAuthorizationError("job_action_binding")
    return persisted


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
    reservation_path = next(
        (
            path
            for path in _reservation_paths(store.root, job_id)
            if _digest(_read_regular(path)) == reservation_sha256
        ),
        None,
    )
    if reservation_path is None:
        raise WorkflowJobAuthorizationError("job_reservation_binding")
    reservation = _read_regular(reservation_path)
    reservation_value = json.loads(reservation)
    if not (
        type(reservation_value) is dict
        and reservation_value.get("schema_version")
        == "workflow-bounded-job-reservation.v2"
        and reservation_value.get("scope_sha256") == authorization.scope.digest
        and _digest(reservation) == reservation_sha256
        and reservation == _canonical(reservation_value)
        and reservation_value.get("action_sha256") == action_sha256
        and reservation_value.get("run_id") == authorization.run_id
        and reservation_value.get("request_body_sha256") == request_body_sha256
        and reservation_value.get("request_body_length") == request_body_length
    ):
        raise WorkflowJobAuthorizationError("job_reservation_binding")
    dispatch_path = next(
        (
            path
            for path in sorted(
                store.root.glob(f"workflow-bounded-job.{job_id}.dispatch.*.json")
            )
            if json.loads(_read_regular(path)).get("attempt_id") == attempt_id
        ),
        None,
    )
    if dispatch_path is None:
        raise WorkflowJobAuthorizationError("job_dispatch_binding")
    dispatch = _read_regular(dispatch_path)
    dispatch_value = json.loads(dispatch)
    if not (
        type(dispatch_value) is dict
        and dispatch_value.get("schema_version")
        in {
            "workflow-bounded-job-dispatch.v1",
            "workflow-bounded-job-dispatch.v2",
        }
        and type(dispatch_value.get("admitted_at_ns")) is int
        and dispatch_value["admitted_at_ns"] < authorization.scope.expires_at_ns
        and dispatch == _canonical(dispatch_value)
        and dispatch_value.get("action_sha256") == action_sha256
        and dispatch_value.get("attempt_id") == attempt_id
        and dispatch_value.get("attempt_evidence_sha256") == attempt_evidence_sha256
        and dispatch_value.get("reservation_sha256") == reservation_sha256
        and dispatch_value.get("ordinal", ordinal) == ordinal
        and dispatch_value.get("retry_index", 0)
        == reservation_value.get("retry_index", 0)
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
    if handoff_artifact.artifact_id != predecessor[1]:
        raise WorkflowJobAuthorizationError("job_action_handoff")
    source_ordinal = _producer_ordinal(scope.actions, ordinal)
    source_artifact = handoff_artifact
    values = {
        "assignment": assignment_manifest.run_input,
        "source_artifact": _artifact_payload(source_artifact),
    }
    if slot.input_source == "revision_input":
        # Aggregate every reviewer's outcome from this round. A PASS never
        # cancels another reviewer's grounded findings, and at least one
        # CHANGES_REQUIRED is required to authorize a revision at all.
        reviewers = _round_review_ordinals(scope.actions, ordinal)
        review_payloads = []
        changes_requested = False
        for review_ordinal in reviewers:
            review = _artifact_for_action(
                store, job_id, review_ordinal, verify_lineage=verify_lineage
            )
            review_manifest = load_workflow_run_manifest(store, review.run_id)
            verdict = _review_verdict(
                review.content.decode("utf-8"),
                review_input=review_manifest.run_input,
            )
            if verdict == "CHANGES_REQUIRED":
                changes_requested = True
            review_payloads.append(_artifact_payload(review))
        if not changes_requested:
            raise WorkflowJobAuthorizationError("document_job_revision_not_authorized")
        values["review_artifacts"] = review_payloads
    elif slot.input_source == "re_review_input":
        if source_ordinal == 0:
            raise WorkflowJobAuthorizationError("job_action_predecessor")
        prior_reviewers = _round_review_ordinals(scope.actions, source_ordinal)
        values["prior_review_artifacts"] = [
            _artifact_payload(
                _artifact_for_action(
                    store, job_id, index, verify_lineage=verify_lineage
                )
            )
            for index in prior_reviewers
        ]
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
    source_ordinal = _producer_ordinal(
        load_bounded_workflow_job_scope(store, job_id).actions, ordinal
    )
    if verify_lineage:
        prior = load_bounded_job_action_by_identity(store, job_id, source_ordinal)
        if prior.run_id != run_id:
            raise WorkflowJobAuthorizationError("job_action_predecessor")
    else:
        # During attempt-history validation, recursively loading the complete
        # predecessor authorization would re-enter that same history. The
        # caller has already strictly validated the Run/Artifact at derivation
        # time; here bind the canonical producer action and its immutable
        # dispatch to the bytes being handed off, without walking history again.
        prior_payload = _read_regular(_action_path(store.root, job_id, source_ordinal))
        prior = json.loads(prior_payload)
        prior_dispatch = _latest_dispatch_for_action(store, job_id, source_ordinal)
        if not (
            type(prior) is dict
            and prior_payload == _canonical(prior)
            and prior.get("action_ordinal") == source_ordinal
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
    dispatches = _dispatches_for_action(store, job_id, ordinal)
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
        if any(
            item.execution_attempt_id == dispatch.get("attempt_id")
            for dispatch in dispatches
        )
    ]
    if len(candidates) != 1:
        raise WorkflowJobAuthorizationError("job_action_artifact")
    return candidates[0]


def _dispatches_for_action(
    store: WorkflowRunManifestStore, job_id: str, ordinal: int
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    prefix = f"workflow-bounded-job.{job_id}.dispatch.{ordinal}"
    for path in sorted(store.root.glob(prefix + "*.json")):
        payload = _read_regular(path)
        value = json.loads(payload)
        retry_index = value.get("retry_index", 0)
        if (
            type(value) is not dict
            or payload != _canonical(value)
            or value.get("schema_version")
            not in {
                "workflow-bounded-job-dispatch.v1",
                "workflow-bounded-job-dispatch.v2",
            }
            or value.get("ordinal", ordinal) != ordinal
            or type(retry_index) is not int
            or retry_index < 0
        ):
            raise WorkflowJobAuthorizationError("job_dispatch_evidence")
        records.append(value)
    records.sort(key=lambda item: item.get("retry_index", 0))
    return records


def _latest_dispatch_for_action(
    store: WorkflowRunManifestStore, job_id: str, ordinal: int
) -> dict[str, object]:
    records = _dispatches_for_action(store, job_id, ordinal)
    if not records:
        raise WorkflowJobAuthorizationError("job_dispatch_missing")
    return records[-1]


def _artifact_payload(artifact) -> dict[str, object]:
    return {
        "artifact_id": artifact.artifact_id,
        "content": artifact.content.decode("utf-8"),
        "content_length": artifact.content_length,
        "content_sha256": artifact.content_sha256,
        "run_id": artifact.run_id,
    }


def validate_bounded_document_job_scope(scope: BoundedWorkflowJobScope) -> None:
    """Require one finite configured create/review plan and declared bounds.

    The reviewer count comes only from the declared action set, never from a
    Python constant, so any finite positive number of reviewers is valid.
    """
    actions = scope.actions
    review_index = next(
        (
            index
            for index, item in enumerate(actions)
            if item.input_source == "review_input"
        ),
        None,
    )
    if review_index is None or review_index < 1:
        raise WorkflowJobAuthorizationError("document_job_scope")
    reviewer_count = sum(1 for item in actions if item.input_source == "review_input")
    creator = actions[:review_index]
    review_round = actions[review_index : review_index + reviewer_count]
    suffix = actions[review_index + reviewer_count :]
    creator_workflow = creator[0].workflow_id
    creator_is_sequential = (
        creator[0].input_source == "assignment"
        and creator[0].step_index == 1
        and all(
            item.workflow_id == creator_workflow
            and item.input_source == "previous_step"
            and item.step_index == previous.step_index + 1
            for previous, item in zip(creator, creator[1:])
        )
    )
    reviews_are_declared = all(
        item.input_source == "review_input" and item.step_index == 1
        for item in review_round
    )
    round_size = 1 + reviewer_count
    revision_rounds = len(suffix) == round_size * scope.max_revisions and all(
        suffix[index * round_size].input_source == "revision_input"
        and suffix[index * round_size].step_index == 1
        and all(
            suffix[index * round_size + 1 + offset].input_source == "re_review_input"
            and suffix[index * round_size + 1 + offset].step_index == 1
            for offset in range(reviewer_count)
        )
        for index in range(scope.max_revisions)
    )
    normal_action_limit = min(
        len(actions),
        review_index
        + reviewer_count * (scope.max_revisions + 1)
        + scope.max_revisions,
    )
    if (
        not creator_is_sequential
        or not reviews_are_declared
        or not revision_rounds
        or scope.max_provider_attempts != normal_action_limit + scope.max_retries
    ):
        raise WorkflowJobAuthorizationError("document_job_scope")


def _review_verdict(
    content: str,
    *,
    review_input: str | None = None,
    require_actionable_findings: bool = True,
) -> str:
    lines = content.splitlines()
    allowed_verdicts = {"PASS", "CHANGES_REQUIRED", "BLOCKED"}
    declarations: list[tuple[int, str]] = []
    fenced_block: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        indentation = len(line) - len(line.lstrip(" "))
        if indentation >= 4 or line.startswith("\t"):
            continue
        candidate = line.strip()
        fence = re.match(r"(`{3,}|~{3,})(.*)$", line[indentation:])
        if fenced_block is not None:
            if (
                fence is not None
                and fence.group(1)[0] == fenced_block[0]
                and len(fence.group(1)) >= fenced_block[1]
                and not fence.group(2).strip()
            ):
                fenced_block = None
            continue
        if fence is not None and (
            fence.group(1)[0] != "`" or "`" not in fence.group(2)
        ):
            fenced_block = (fence.group(1)[0], len(fence.group(1)))
            continue
        if candidate.startswith(">"):
            continue
        declaration = candidate
        for marker in ("- ", "* ", "+ "):
            if declaration.startswith(marker):
                declaration = declaration[len(marker) :].strip()
                break
        if declaration.startswith("#"):
            declaration = declaration.lstrip("#").strip()
        is_english = declaration.startswith("Verdict:")
        is_japanese = declaration.startswith(("判定：", "判定:"))
        if not is_english and not is_japanese:
            continue
        if declaration != candidate:
            raise WorkflowJobAuthorizationError("document_job_invalid_verdict")
        if is_english:
            if declaration not in {f"Verdict: {value}" for value in allowed_verdicts}:
                raise WorkflowJobAuthorizationError("document_job_invalid_verdict")
            verdict = declaration.removeprefix("Verdict: ")
        else:
            separator = "判定：" if declaration.startswith("判定：") else "判定:"
            value = declaration[len(separator) :].strip()
            if value.startswith("`") or value.endswith("`"):
                if not (value.startswith("`") and value.endswith("`")):
                    raise WorkflowJobAuthorizationError("document_job_invalid_verdict")
                value = value[1:-1]
            if value not in allowed_verdicts:
                raise WorkflowJobAuthorizationError("document_job_invalid_verdict")
            verdict = value
        declarations.append((index, verdict))
    if len(declarations) != 1:
        raise WorkflowJobAuthorizationError("document_job_invalid_verdict")
    verdict_index, verdict = declarations[0]
    if verdict == "PASS":
        if review_input is None:
            raise WorkflowJobAuthorizationError("document_job_nonactionable_review")
        required = (
            "Requirement",
            "Artifact location",
            "Evidence source/reference",
            "Assessment",
        )
        labels = {
            "requirement": "Requirement",
            "artifact location": "Artifact location",
            "evidence source/reference": "Evidence source/reference",
            "assessment": "Assessment",
        }
        checks: list[dict[str, str]] = []
        check: dict[str, str] = {}
        check_numbers: list[int] = []
        unresolved_count = 0
        unresolved_is_none = False
        fenced: tuple[str, int] | None = None
        for line in lines[verdict_index + 1 :]:
            indentation = len(line) - len(line.lstrip(" "))
            if indentation >= 4 or line.startswith("\t"):
                continue
            fence = re.match(r"(`{3,}|~{3,})(.*)$", line[indentation:])
            if fenced is not None:
                if (
                    fence is not None
                    and fence.group(1)[0] == fenced[0]
                    and len(fence.group(1)) >= fenced[1]
                    and not fence.group(2).strip()
                ):
                    fenced = None
                continue
            if fence is not None:
                fenced = (fence.group(1)[0], len(fence.group(1)))
                continue
            if line[indentation:].startswith(">"):
                continue
            heading = re.fullmatch(r"#{1,6}\s+Check\s+([1-9][0-9]*)\s*", line.strip())
            if heading is not None:
                if check:
                    checks.append(check)
                    check = {}
                check_numbers.append(int(heading.group(1)))
                continue
            label, separator, value = line.partition(":")
            normalized_label = label.strip().casefold()
            if normalized_label == "unresolved material issues":
                unresolved_count += 1
                unresolved_is_none = (
                    separator != "" and value.strip().casefold() == "none"
                )
                continue
            canonical_label = labels.get(normalized_label)
            if canonical_label is not None:
                if not separator or not value.strip() or canonical_label in check:
                    raise WorkflowJobAuthorizationError(
                        "document_job_nonactionable_review"
                    )
                check[canonical_label] = value.strip()
        if check:
            checks.append(check)
        if (
            not checks
            or check_numbers != list(range(1, len(check_numbers) + 1))
            or len(checks) != len(check_numbers)
            or any(set(item) != set(required) for item in checks)
            or unresolved_count != 1
            or not unresolved_is_none
        ):
            raise WorkflowJobAuthorizationError("document_job_nonactionable_review")

        try:
            input_value = json.loads(review_input)
            assignment_text = input_value["assignment"]
            source_artifact = input_value.get("source_artifact") or input_value.get(
                "revision_artifact"
            )
            source_content = source_artifact["content"]
            source_bytes = source_content.encode("utf-8")
            source_is_bound = (
                type(source_artifact) is dict
                and type(assignment_text) is str
                and type(source_content) is str
                and source_artifact.get("content_length") == len(source_bytes)
                and source_artifact.get("content_sha256") == _digest(source_bytes)
            )
        except Exception:
            assignment_text = ""
            source_content = ""
            source_is_bound = False
        if not source_is_bound:
            raise WorkflowJobAuthorizationError("document_job_unverified_finding")
        for item in checks:
            evidence_reference = item["Evidence source/reference"]
            if not _assignment_line_reference_matches(
                evidence_reference, assignment_text
            ):
                raise WorkflowJobAuthorizationError("document_job_unverified_finding")
            location_quotes = re.findall(r"`([^`]+)`", item["Artifact location"])
            if not location_quotes or any(
                quote not in source_content for quote in location_quotes
            ):
                raise WorkflowJobAuthorizationError("document_job_unverified_finding")
        return verdict
    if verdict == "CHANGES_REQUIRED":
        if not require_actionable_findings:
            return verdict
        required = (
            "Requirement",
            "Artifact location",
            "Evidence source/reference",
            "Impact",
            "Requested correction",
        )
        labels = {
            "requirement": "Requirement",
            "artifact location": "Artifact location",
            "evidence source/reference": "Evidence source/reference",
            "impact": "Impact",
            "requested correction": "Requested correction",
            "要件": "Requirement",
            "artifact位置": "Artifact location",
            "証拠": "Evidence source/reference",
            "影響": "Impact",
            "修正要求": "Requested correction",
        }
        findings: list[dict[str, str]] = []
        finding: dict[str, str] = {}
        finding_header_seen = False
        for line in lines[verdict_index + 1 :]:
            heading = line.strip()
            if heading.startswith("#"):
                heading = heading.lstrip("#").strip()
            elif heading.startswith(("- ", "* ")):
                heading = heading[2:].strip()
            else:
                heading = ""
            name, _, heading_detail = heading.partition(" ")
            name = name.rstrip(":")
            finding_header = (
                name.casefold() == "finding"
                and bool(heading_detail)
                and heading_detail.strip().split(maxsplit=1)[0].rstrip(":").isdigit()
            ) or (len(name) > 1 and name[0].casefold() == "f" and name[1:].isdigit())
            if finding_header:
                if finding_header_seen:
                    findings.append(finding)
                finding = {}
                finding_header_seen = True
                continue
            label, separator, value = line.partition(":")
            label = label.strip().lstrip("-* ").strip().strip("*_")
            canonical_label = labels.get(label.casefold())
            if canonical_label is not None:
                if not separator or not value.strip() or canonical_label in finding:
                    raise WorkflowJobAuthorizationError(
                        "document_job_nonactionable_review"
                    )
                finding[canonical_label] = value.strip()
        if finding_header_seen:
            findings.append(finding)
        elif finding:
            findings.append(finding)
        if not findings or any(set(item) != set(required) for item in findings):
            raise WorkflowJobAuthorizationError("document_job_nonactionable_review")
        if review_input is None:
            raise WorkflowJobAuthorizationError("document_job_unverified_finding")
        assignment_references = _explicit_assignment_evidence_references(review_input)
        assignment_text = json.loads(review_input)["assignment"]
        assignment_reference_ids = _reference_ids(assignment_text, line_start_only=True)
        try:
            input_value = json.loads(review_input)
            source_artifact = input_value.get("source_artifact") or input_value.get(
                "revision_artifact"
            )
            source_content = source_artifact.get("content")
            source_bytes = source_content.encode("utf-8")
            source_is_bound = (
                type(source_artifact) is dict
                and type(source_content) is str
                and source_artifact.get("content_length") == len(source_bytes)
                and source_artifact.get("content_sha256") == _digest(source_bytes)
            )
        except Exception:
            source_content = ""
            source_is_bound = False
        for item in findings:
            evidence_reference = _normalized_reference(
                item["Evidence source/reference"]
            )
            raw_evidence_reference = item["Evidence source/reference"]
            if _contains_assignment_line_reference(raw_evidence_reference):
                if not _assignment_line_reference_matches(
                    raw_evidence_reference, assignment_text
                ):
                    raise WorkflowJobAuthorizationError(
                        "document_job_unverified_finding"
                    )
                location_quotes = re.findall(r"`([^`]+)`", item["Artifact location"])
                if (
                    source_is_bound
                    and location_quotes
                    and all(quote in source_content for quote in location_quotes)
                ):
                    continue
                raise WorkflowJobAuthorizationError("document_job_unverified_finding")
            if evidence_reference in assignment_references:
                continue
            cited_reference_ids = _reference_ids(item["Evidence source/reference"])
            if cited_reference_ids and cited_reference_ids <= assignment_reference_ids:
                continue
            location_quotes = re.findall(r"`([^`]+)`", item["Artifact location"])
            if (
                any(
                    label in evidence_reference
                    for label in ("artifact", "deliverable", "成果物")
                )
                and source_is_bound
                and location_quotes
                and all(quote in source_content for quote in location_quotes)
            ):
                continue
            raise WorkflowJobAuthorizationError("document_job_unverified_finding")
    return verdict


def _assignment_line_reference_matches(reference: str, assignment: str) -> bool:
    """Verify a line citation against the exact immutable assignment text."""
    match = re.fullmatch(r"assignment:L([1-9][0-9]*)\s+`([^`\n]+)`", reference)
    if match is None:
        return False
    line_number = int(match.group(1))
    lines = assignment.splitlines()
    return line_number <= len(lines) and match.group(2) in lines[line_number - 1]


def _contains_assignment_line_reference(reference: str) -> bool:
    """Identify line-reference syntax to prevent legacy fallback."""
    return (
        re.search(r"(?<![a-z0-9_])assignment\s*:\s*L", reference, re.IGNORECASE)
        is not None
    )


def _normalized_reference(value: str) -> str:
    return " ".join(value.split()).casefold()


def _reference_ids(
    value: str, *, line_start_only: bool = False
) -> frozenset[tuple[str, str]]:
    """Return explicitly typed IDs; incidental words are not declarations."""
    identifiers: set[tuple[str, str]] = set()
    pattern = re.compile(
        r"(?<![a-z0-9_])(?:"
        r"(?P<kind>evidence|criterion|requirement|source|reference|fragment)"
        r"(?![a-z0-9_])|(?P<kind_ja>証拠|断片))"
        r"(?:\s+|\s*[:#]\s*|(?<=証拠)|(?<=断片))"
        r"(?P<first>[a-z0-9][a-z0-9_-]*)"
        r"(?:\s*[-–—]\s*(?P<last>[a-z0-9][a-z0-9_-]*))?"
        r"(?![a-z0-9_])",
        re.IGNORECASE,
    )
    matches = (
        (
            match
            for line in value.splitlines()
            if (match := pattern.match(line.lstrip(" \t#>*_`-+")))
        )
        if line_start_only
        else pattern.finditer(value)
    )
    for match in matches:
        kind = (match.group("kind") or match.group("kind_ja")).casefold()
        if kind in {"fragment", "断片"}:
            kind = "evidence"
        first = match.group("first").casefold()
        last = (match.group("last") or first).casefold()
        if first in {
            "criterion",
            "evidence",
            "fragment",
            "identifier",
            "id",
            "only",
            "package",
            "provided",
            "reference",
            "requirement",
            "source",
            "supplied",
        }:
            continue
        identifiers.add((kind, first))
        if len(first) == len(last) == 1 and first.isalpha() and last.isalpha():
            start, end = sorted((ord(first), ord(last)))
            if end - start <= 25:
                identifiers.update((kind, chr(code)) for code in range(start, end + 1))
    return frozenset(identifiers)


def _explicit_assignment_evidence_references(review_input: str) -> frozenset[str]:
    """Extract exact, explicitly labeled criteria/source references from input.

    Subject Artifact text and the JSON envelope are deliberately excluded. This
    is a scope/provenance check, not an automatic semantic-truth verifier. The
    accepted labels are generic and do not require a domain-specific evidence
    naming convention.
    """
    try:
        value = json.loads(review_input)
    except Exception:
        raise WorkflowJobAuthorizationError("document_job_unverified_finding") from None
    assignment = value.get("assignment") if type(value) is dict else None
    if type(assignment) is not str:
        raise WorkflowJobAuthorizationError("document_job_unverified_finding")
    allowed_labels = {
        "evidence",
        "evidence id",
        "evidence reference",
        "acceptance evidence",
        "criterion",
        "criterion id",
        "requirement",
        "requirement id",
        "reference",
        "source id",
        "source reference",
    }
    generic = {
        "acceptance evidence",
        "assignment",
        "artifact",
        "evidence",
        "input",
        "report",
        "result",
        "source",
    }
    references = set()
    for line in assignment.splitlines():
        heading = line.strip().strip("*_").strip()
        reference_label = re.match(
            r"(?:evidence|criterion|requirement|source)\s+([a-z0-9][a-z0-9_-]*)\b",
            heading,
            re.IGNORECASE,
        )
        if reference_label:
            references.add(
                _normalized_reference(
                    f"{heading.split()[0]} {reference_label.group(1)}"
                )
            )
        label, separator, raw_reference = line.partition(":")
        if not separator or label.strip().casefold() not in allowed_labels:
            continue
        reference = _normalized_reference(raw_reference.strip().strip("`*_\"'"))
        if reference and reference not in generic:
            references.add(reference)
    return frozenset(references)


@dataclass(frozen=True)
class BoundedDocumentJobOutcome:
    """Safe terminal result for a configured finite create/review Job."""

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
    execution_destinations: Path | None = None,
) -> BoundedDocumentJobOutcome:
    """Run the configured finite Job once and persist its terminal result."""
    current = load_bounded_workflow_job_scope(store, scope.job_id)
    if current != scope or initial_run_id != scope.initial_run_id:
        raise WorkflowJobAuthorizationError("document_job_binding")
    prior_status = _read_bounded_document_job_status(store, scope)
    if prior_status is not None:
        return _outcome_from_status(store, scope, prior_status)
    with _job_lock(store.root, scope.job_id):
        prior_status = _read_bounded_document_job_status(store, scope)
        if prior_status is not None:
            return _outcome_from_status(store, scope, prior_status)
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
            execution_destinations=execution_destinations,
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
    consumed_revision_ordinals = _consumed_revision_action_ordinals(scope, reservations)
    dispatch_order = []
    reservation_by_digest = {
        _digest(_read_regular(path)): (path, _read_regular(path))
        for path in _reservation_paths(store.root, job_id)
    }
    for dispatch_path in sorted(
        store.root.glob(f"workflow-bounded-job.{job_id}.dispatch.*.json")
    ):
        dispatch_payload = _read_regular(dispatch_path)
        dispatch = json.loads(dispatch_payload)
        ordinal = dispatch.get("ordinal")
        if ordinal is None:
            # v1 had exactly one dispatch file per action; retain read-only
            # compatibility for already persisted non-retry Jobs.
            try:
                ordinal = int(
                    dispatch_path.name.split(".dispatch.", 1)[1].split(".", 1)[0]
                )
            except (IndexError, ValueError):
                raise WorkflowJobAuthorizationError("document_job_dispatch") from None
        if type(ordinal) is not int or not 0 <= ordinal < len(scope.actions):
            raise WorkflowJobAuthorizationError("document_job_dispatch")
        authorization = load_bounded_job_action_by_identity(
            store, job_id, ordinal, verify_lineage=False
        )
        reservation_pair = reservation_by_digest.get(dispatch.get("reservation_sha256"))
        if reservation_pair is None:
            raise WorkflowJobAuthorizationError("document_job_dispatch")
        _, reservation = reservation_pair
        reservation_value = json.loads(reservation)
        if not (
            dispatch_payload == _canonical(dispatch)
            and dispatch.get("action_sha256") == authorization.action_sha256
            and reservation == _canonical(reservation_value)
            and reservation_value.get("action_sha256") == authorization.action_sha256
            and reservation_value.get("ordinal") == ordinal
            and dispatch.get("reservation_sha256") == _digest(reservation)
            and dispatch.get("attempt_id")
        ):
            raise WorkflowJobAuthorizationError("document_job_dispatch")
        dispatch_order.append(
            {
                "action_ordinal": ordinal,
                "admitted_at_ns": dispatch["admitted_at_ns"],
                "attempt_id": dispatch["attempt_id"],
                "http_status": None,
                "result_category": None,
                "retry_index": dispatch.get("retry_index", 0),
                "run_id": authorization.run_id,
            }
        )
        from ai_office.execution_evidence import (
            execution_normalized_result_evidence_path,
            execution_raw_response_evidence_path,
            load_normalized_result_evidence,
            load_raw_response_evidence,
        )

        raw_path = execution_raw_response_evidence_path(
            store.root, authorization.run_id, dispatch["attempt_id"]
        )
        if raw_path.exists() or raw_path.is_symlink():
            raw = load_raw_response_evidence(
                store.root, authorization.run_id, dispatch["attempt_id"]
            )
            dispatch_order[-1]["http_status"] = raw.status_code
        result_path = execution_normalized_result_evidence_path(
            store.root, authorization.run_id, dispatch["attempt_id"]
        )
        if result_path.exists() or result_path.is_symlink():
            normalized = load_normalized_result_evidence(
                store.root, authorization.run_id, dispatch["attempt_id"]
            )
            dispatch_order[-1]["result_category"] = getattr(
                normalized.result, "category", "success"
            )
    dispatch_order.sort(
        key=lambda item: (item["admitted_at_ns"], item["action_ordinal"])
    )
    status = _read_bounded_document_job_status(store, scope)
    terminal_outcome = (
        _verify_positive_terminal_status(store, scope, status) if status else None
    )
    review_index = next(
        index
        for index, item in enumerate(scope.actions)
        if item.input_source == "review_input"
    )
    reviewer_count = sum(
        1 for item in scope.actions if item.input_source == "review_input"
    )
    normal_action_limit = (
        review_index + reviewer_count * (scope.max_revisions + 1) + scope.max_revisions
    )
    return {
        "assignment": manifest.run_input,
        "assignment_sha256": scope.assignment_sha256,
        "consumed_provider_attempts": len(reservations),
        "dispatch_order": dispatch_order,
        "job_id": job_id,
        "max_provider_attempts": scope.max_provider_attempts,
        "normal_action_limit": min(len(scope.actions), normal_action_limit),
        "normal_actions_consumed": len({item.get("ordinal") for item in reservations}),
        "reviewer_count": reviewer_count,
        "retry_limit": scope.max_retries,
        "retry_interval_seconds": scope.retry_interval_seconds,
        "retryable_http_statuses": [429, 503]
        if scope.schema_version == "workflow-bounded-job-scope.v2"
        else [],
        "retry_attempts_consumed": sum(
            1 for item in reservations if item.get("retry_index", 0) > 0
        ),
        "total_physical_attempt_limit": scope.max_provider_attempts,
        "max_revisions": scope.max_revisions,
        "remaining_provider_attempts": scope.max_provider_attempts - len(reservations),
        "revision_attempts_consumed": len(consumed_revision_ordinals),
        "revisions_used": terminal_outcome.revisions_used
        if terminal_outcome
        else len(consumed_revision_ordinals),
        "revision_retry_attempts_consumed": sum(
            1
            for item in reservations
            if item.get("ordinal") in consumed_revision_ordinals
            and item.get("retry_index", 0) > 0
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


def _verify_positive_terminal_status(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    status: dict[str, object],
) -> BoundedDocumentJobOutcome:
    """Rebuild the terminal result from immutable Artifact/run evidence."""
    if status["status"] == "stopped":
        return _stopped_document_job_outcome(store, scope, str(status["stop_reason"]))
    if status["status"] not in {"candidate", "reviewed"}:
        raise WorkflowJobAuthorizationError("document_job_status")
    reservations = _reservation_records(store.root, scope.job_id)
    completed_ordinals = {item.get("ordinal") for item in reservations}
    final_review_ordinal = next(
        (
            index
            for index in reversed(range(len(scope.actions)))
            if index in completed_ordinals
            and scope.actions[index].input_source in {"review_input", "re_review_input"}
        ),
        None,
    )
    if final_review_ordinal is None:
        raise WorkflowJobAuthorizationError("document_job_status_accounting")
    final_ordinal = _producer_ordinal(scope.actions, final_review_ordinal)
    expected_revisions = sum(
        1
        for item in scope.actions[:final_review_ordinal]
        if item.input_source == "revision_input"
    )
    expected_calls = final_review_ordinal + 1
    if (status["status"] == "candidate") != (expected_revisions == 0):
        raise WorkflowJobAuthorizationError("document_job_status_accounting")
    expected_ordinals = set(range(expected_calls))
    retries_consumed = sum(1 for item in reservations if item.get("retry_index", 0) > 0)
    if (
        len(reservations) != expected_calls + retries_consumed
        or len(reservations) > scope.max_provider_attempts
        or retries_consumed > scope.max_retries
        or {item.get("ordinal") for item in reservations} != expected_ordinals
        or sum(
            1
            for index in expected_ordinals
            if scope.actions[index].input_source == "revision_input"
        )
        != expected_revisions
    ):
        raise WorkflowJobAuthorizationError("document_job_status_accounting")
    for ordinal in range(expected_calls):
        action_records = [
            item for item in reservations if item.get("ordinal") == ordinal
        ]
        action_records.sort(key=lambda item: item.get("retry_index", 0))
        expected_retry_count = len(action_records) - 1
        if not action_records or [
            item.get("retry_index", 0) for item in action_records
        ] != list(range(len(action_records))):
            raise WorkflowJobAuthorizationError("document_job_status_accounting")
        if expected_retry_count:
            action_authorization = load_bounded_job_action_by_identity(
                store, scope.job_id, ordinal, verify_lineage=False
            )
            for retry_index in range(1, expected_retry_count + 1):
                if not _prior_job_attempts_are_retryable(
                    action_authorization,
                    expected_count=retry_index,
                    request_body_sha256=action_records[0]["request_body_sha256"],
                ):
                    raise WorkflowJobAuthorizationError("document_job_retry_evidence")
    try:
        review_artifact = None
        reviewer_count = sum(
            1 for item in scope.actions if item.input_source == "review_input"
        )
        first_review_ordinal = next(
            index
            for index, item in enumerate(scope.actions)
            if item.input_source == "review_input"
        )
        # Rebuild each declared round. The final round must be an all-PASS
        # round; every earlier round must have requested at least one change,
        # or the shared loop would have returned a candidate earlier.
        round_start = first_review_ordinal
        while round_start <= final_review_ordinal:
            round_verdicts = []
            for offset in range(reviewer_count):
                ordinal = round_start + offset
                item = _artifact_for_action(store, scope.job_id, ordinal)
                item_manifest = load_workflow_run_manifest(store, item.run_id)
                round_verdicts.append(
                    _review_verdict(
                        item.content.decode("utf-8"),
                        review_input=item_manifest.run_input,
                    )
                )
                review_artifact = item
            round_is_final = (
                round_start + reviewer_count - 1 == final_review_ordinal
            )
            if round_is_final:
                if any(verdict != "PASS" for verdict in round_verdicts):
                    raise WorkflowJobAuthorizationError(
                        "document_job_terminal_evidence"
                    )
            elif all(verdict == "PASS" for verdict in round_verdicts):
                raise WorkflowJobAuthorizationError("document_job_terminal_evidence")
            round_start += reviewer_count + 1
        if review_artifact is None:
            raise WorkflowJobAuthorizationError("document_job_terminal_evidence")
        final_artifact = _artifact_for_action(store, scope.job_id, final_ordinal)
    except WorkflowJobAuthorizationError:
        raise
    except Exception:
        raise WorkflowJobAuthorizationError("document_job_terminal_evidence") from None
    return BoundedDocumentJobOutcome(
        str(status["status"]),
        final_artifact.run_id,
        final_artifact.artifact_id,
        review_artifact.run_id,
        review_artifact.artifact_id,
        expected_revisions,
    )


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
    execution_destinations: Path | None,
) -> BoundedDocumentJobOutcome:
    """Execute only the approved finite creator/review/action plan.

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
        workflow_definition_from_run_manifest,
    )
    from ai_office.execution_destination import (
        ExecutionDestinationError,
        load_execution_destination_registry,
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
    initial_manifest = load_workflow_run_manifest(store, initial_run_id)
    if scope.source_manifest_run_id is not None:
        source_manifest = load_workflow_run_manifest(
            store, scope.source_manifest_run_id
        )
        if (
            source_manifest.digest != scope.source_manifest_digest
            or source_manifest.run_input != initial_manifest.run_input
            or source_manifest.execution_destination
            != initial_manifest.execution_destination
        ):
            raise WorkflowJobAuthorizationError("job_source_manifest")
        _validate_derived_review_plan(
            source_manifest, scope.actions, scope.max_revisions
        )
    execution_target = initial_manifest.execution_destination.target
    if (
        current_scope != scope
        or initial_run_id != scope.initial_run_id
        or _digest(assignment.encode("utf-8")) != scope.assignment_sha256
        or _digest(assignment.encode("utf-8")) != scope.initial_input_sha256
        or execution_target_fingerprint(execution_target)
        != scope.actions[0].execution_target_fingerprint
        or execution_target.provider != scope.actions[0].provider
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
    if scope.source_manifest_run_id is not None and workflows[
        scope.actions[0].workflow_id
    ].definition != workflow_definition_from_run_manifest(source_manifest):
        raise WorkflowJobAuthorizationError("document_job_definition_changed")

    if (
        initial_manifest.workflow_id != scope.actions[0].workflow_id
        or initial_manifest.run_input != assignment
        or _digest(_canonical(_workflow_dict(initial_manifest.workflow_snapshot)))
        != scope.actions[0].workflow_snapshot_sha256
    ):
        raise WorkflowJobAuthorizationError("document_job_initial_run")

    def verify_destination(slot: BoundedJobActionSlot) -> None:
        if slot.model != employees[slot.employee_id].definition.model:
            raise WorkflowJobAuthorizationError("document_job_model_binding")
        if execution_destinations is None:
            return
        try:
            destination = load_execution_destination_registry(
                execution_destinations
            ).resolve(execution_target.provider)
            if (
                destination.target != execution_target
                or destination.select_model(slot.model) != slot.model
            ):
                raise WorkflowJobAuthorizationError("document_job_destination_changed")
        except (ExecutionDestinationError, OSError):
            raise WorkflowJobAuthorizationError(
                "document_job_destination_changed"
            ) from None

    def verify_run_manifest(manifest, slot: BoundedJobActionSlot) -> None:
        if not any(
            employee.id == slot.employee_id and employee.model == slot.model
            for employee in manifest.employee_snapshots
        ):
            raise WorkflowJobAuthorizationError("document_job_manifest_destination")

    def run_fresh(
        ordinal: int,
        run_id: str,
        run_input: str,
        predecessor: tuple[str, str, str] | None = None,
    ):
        slot = scope.actions[ordinal]
        verify_destination(slot)
        loaded_workflow = workflows[slot.workflow_id]
        if scope.source_manifest_run_id is not None:
            loaded_workflow = bounded_job_execution_view(loaded_workflow, slot.step_id)
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
                execution_model=slot.model,
                execution_target=execution_target,
            )
        )
        if manifest.run_input != run_input or manifest.workflow_id != workflow.id:
            raise WorkflowJobAuthorizationError("document_job_manifest")
        verify_run_manifest(manifest, slot)
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

    creator_action_count = next(
        index
        for index, item in enumerate(scope.actions)
        if item.input_source == "review_input"
    )
    creator_loaded = workflows[scope.actions[0].workflow_id]
    creator = (
        bounded_job_execution_view(creator_loaded, scope.actions[0].step_id).definition
        if scope.source_manifest_run_id is not None
        else creator_loaded.definition
    )
    creator_manifest = initial_manifest
    progression, _, _, _ = run_fresh(0, initial_run_id, assignment)
    for ordinal in range(1, creator_action_count):
        if (
            type(progression) is not WorkflowProgressionDecision
            or progression.decision != "prepare_next_step"
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
        slot = scope.actions[ordinal]
        verify_destination(slot)
        verify_run_manifest(creator_manifest, slot)
        employee = employees[slot.employee_id].definition
        step = creator.steps[slot.step_index - 1]
        previous = creator.steps[slot.step_index - 2]
        preparation = NextStepPreparationApproval(
            True,
            creator.id,
            previous.id,
            slot.step_index - 1,
            step.id,
            slot.step_index,
            employee.id,
        )
        prepared = _prepare_approved_next_workflow_step(
            creator, history, progression, preparation, employee
        )
        prepared_start = prepare_prepared_step_execution_start(
            prepared,
            history,
            state_source_sha256=state_sha,
            run_input=creator_manifest.run_input,
        )
        authorization = derive_bounded_job_action_authorization(
            store,
            scope,
            ordinal=ordinal,
            run_id=initial_run_id,
            request_fingerprint=build_model_invocation_execution_fingerprint(
                prepared_start.request, (), execution_target
            ),
            now_ns=time.time_ns(),
        )
        progression = route_approved_workflow_continuation_cycle(
            progression,
            creator,
            preparation,
            employee,
            *store.execution_paths(initial_run_id),
            (),
            api_key,
            None,
            transport,
            job_authorization=authorization,
            execution_target=execution_target,
        )
    if (
        type(progression) is not WorkflowProgressionDecision
        or progression.decision != "workflow_complete"
    ):
        raise WorkflowJobAuthorizationError("document_job_creator_stopped")
    persisted_creator = route_persisted_execution_outcome_reentry(
        creator,
        *store.execution_paths(initial_run_id),
        allow_artifact_completion=True,
    )
    if persisted_creator != progression:
        raise WorkflowJobAuthorizationError("document_job_outcome_mismatch")
    source_artifact = _artifact_for_action(
        store, scope.job_id, creator_action_count - 1
    )

    def materialize(ordinal: int, predecessor):
        # Each predecessor is selected from an Artifact that was just loaded
        # through _artifact_for_action (full lineage validation). Rebind it to
        # the adjacent immutable dispatch here without recursively replaying
        # the entire earlier action history for every loop iteration.
        run_input = bounded_document_action_input(
            store, scope.job_id, ordinal, predecessor, verify_lineage=False
        )
        run_id = (
            "bounded-" + sha256(scope.job_id.encode()).hexdigest()[:16] + f"-{ordinal}"
        )
        return run_id, run_input

    current_artifact = source_artifact
    ordinal = creator_action_count
    reviewer_count = sum(
        1 for item in scope.actions if item.input_source == "review_input"
    )
    revisions_used = 0
    while ordinal < len(scope.actions):
        # One round: every declared reviewer independently assesses the same
        # immutable Artifact version supplied as the shared predecessor.
        round_reviews = []
        verdicts = []
        has_revision_slot = ordinal + reviewer_count < len(scope.actions)
        for offset in range(reviewer_count):
            review_ordinal = ordinal + offset
            predecessor = (
                current_artifact.run_id,
                current_artifact.artifact_id,
                current_artifact.content_sha256,
            )
            review_run_id, review_input = materialize(review_ordinal, predecessor)
            review_result, _, _, _ = run_fresh(
                review_ordinal, review_run_id, review_input, predecessor
            )
            if (
                type(review_result) is not WorkflowProgressionDecision
                or review_result.decision != "workflow_complete"
            ):
                raise WorkflowJobAuthorizationError("document_job_review_stopped")
            review_artifact = _artifact_for_action(
                store, scope.job_id, review_ordinal
            )
            verdict = _review_verdict(
                review_artifact.content.decode("utf-8"),
                review_input=review_input,
                require_actionable_findings=has_revision_slot,
            )
            # BLOCKED, malformed, or ungrounded verdicts raise here, so the
            # round fails closed and its remaining sends are never attempted.
            if verdict not in {"PASS", "CHANGES_REQUIRED"}:
                raise WorkflowJobAuthorizationError("document_job_review_blocked")
            verdicts.append(verdict)
            round_reviews.append(review_artifact)
        if all(verdict == "PASS" for verdict in verdicts):
            # Only a complete all-reviewer PASS round yields a candidate; a
            # PASS never cancels another reviewer's grounded findings.
            final_review = round_reviews[-1]
            return BoundedDocumentJobOutcome(
                "candidate" if revisions_used == 0 else "reviewed",
                current_artifact.run_id,
                current_artifact.artifact_id,
                final_review.run_id,
                final_review.artifact_id,
                revisions_used,
            )
        if not has_revision_slot:
            raise WorkflowJobAuthorizationError(
                "document_job_revision_budget_exhausted"
            )

        revision_ordinal = ordinal + reviewer_count
        revision_predecessor = (
            current_artifact.run_id,
            current_artifact.artifact_id,
            current_artifact.content_sha256,
        )
        revision_run_id, revision_input = materialize(
            revision_ordinal, revision_predecessor
        )
        revision_result, _, _, _ = run_fresh(
            revision_ordinal,
            revision_run_id,
            revision_input,
            revision_predecessor,
        )
        if (
            type(revision_result) is not WorkflowProgressionDecision
            or revision_result.decision != "workflow_complete"
        ):
            raise WorkflowJobAuthorizationError("document_job_revision_stopped")
        current_artifact = _artifact_for_action(store, scope.job_id, revision_ordinal)
        revisions_used += 1
        ordinal = revision_ordinal + 1
    raise WorkflowJobAuthorizationError("document_job_review_missing")


def _persist_bounded_document_job_status(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    outcome: BoundedDocumentJobOutcome,
) -> None:
    payload = _canonical(
        {
            "job_id": scope.job_id,
            "schema_version": "workflow-bounded-document-job-status.v2",
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
        "job_id",
        "schema_version",
        "scope_sha256",
        "status",
        "stop_reason",
    }
    if not (
        type(value) is dict
        and set(value) == expected
        and payload == _canonical(value)
        and value.get("schema_version") == "workflow-bounded-document-job-status.v2"
        and value.get("job_id") == scope.job_id
        and value.get("scope_sha256") == scope.digest
        and value.get("status") in {"candidate", "reviewed", "stopped"}
        and (
            value.get("stop_reason") is None
            if value.get("status") != "stopped"
            else type(value.get("stop_reason")) is str and bool(value["stop_reason"])
        )
    ):
        raise WorkflowJobAuthorizationError("document_job_status")
    return value


def _outcome_from_status(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    value: dict[str, object],
) -> BoundedDocumentJobOutcome:
    return _verify_positive_terminal_status(store, scope, value)


def _consumed_revision_action_ordinals(
    scope: BoundedWorkflowJobScope, reservations: list[dict[str, object]]
) -> set[int]:
    return {
        ordinal
        for item in reservations
        if type(ordinal := item.get("ordinal")) is int
        and 0 <= ordinal < len(scope.actions)
        and scope.actions[ordinal].input_source == "revision_input"
    }


def _stopped_document_job_outcome(
    store: WorkflowRunManifestStore, scope: BoundedWorkflowJobScope, reason: str
) -> BoundedDocumentJobOutcome:
    artifacts = {}
    for ordinal in range(len(scope.actions)):
        try:
            artifacts[ordinal] = _artifact_for_action(
                store, scope.job_id, ordinal, verify_lineage=False
            )
        except Exception:
            pass
    final = next(
        (
            artifacts[index]
            for index in reversed(range(len(scope.actions)))
            if scope.actions[index].input_source
            in {"assignment", "previous_step", "previous_artifact", "revision_input"}
            and index in artifacts
        ),
        None,
    )
    review = next(
        (
            artifacts[index]
            for index in reversed(range(len(scope.actions)))
            if scope.actions[index].input_source in {"review_input", "re_review_input"}
            and index in artifacts
        ),
        None,
    )
    reservations = _reservation_records(store.root, scope.job_id)
    revisions_used = len(_consumed_revision_action_ordinals(scope, reservations))
    return BoundedDocumentJobOutcome(
        "stopped",
        final.run_id if final else None,
        final.artifact_id if final else None,
        review.run_id if review else None,
        review.artifact_id if review else None,
        revisions_used,
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
    max_retries: int = 0,
    retry_interval_seconds: int = 30,
    source_manifest_run_id: str | None = None,
    source_manifest_digest: str | None = None,
) -> str:
    value = {
        "actions": [_slot_dict(item) for item in actions],
        "assignment_sha256": assignment_sha256,
        "expires_at_ns": expires_at_ns,
        "initial_input_sha256": initial_input_sha256,
        "initial_run_id": initial_run_id,
        "job_id": job_id,
        "max_provider_attempts": max_provider_attempts,
        "max_revisions": max_revisions,
        "max_retries": max_retries,
        "retry_interval_seconds": retry_interval_seconds,
        "retryable_http_statuses": [429, 503],
        "schema_version": "workflow-bounded-job-scope-intent.v2",
        **(
            {
                "source_manifest_run_id": source_manifest_run_id,
                "source_manifest_digest": source_manifest_digest,
            }
            if source_manifest_run_id is not None
            else {}
        ),
    }
    return _digest(_canonical(value))


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


def _reservation_path(
    root: Path, job_id: str, ordinal: int, retry_index: int = 0
) -> Path:
    suffix = str(ordinal) if retry_index == 0 else f"{ordinal}.retry.{retry_index}"
    return root / f"workflow-bounded-job.{job_id}.reservation.{suffix}.json"


def _dispatch_path(root: Path, job_id: str, ordinal: int, retry_index: int = 0) -> Path:
    suffix = str(ordinal) if retry_index == 0 else f"{ordinal}.retry.{retry_index}"
    return root / f"workflow-bounded-job.{job_id}.dispatch.{suffix}.json"


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
    scope = load_bounded_workflow_job_scope(WorkflowRunManifestStore(root), job_id)
    for path in _reservation_paths(root, job_id):
        payload = _read_regular(path)
        value = json.loads(payload)
        schema = value.get("schema_version") if type(value) is dict else None
        expected_keys = {
            "action_sha256",
            "ordinal",
            "request_body_length",
            "request_body_sha256",
            "reservation_index",
            "retry_index",
            "run_id",
            "schema_version",
            "scope_sha256",
        }
        if (
            type(value) is not dict
            or schema != "workflow-bounded-job-reservation.v2"
            or set(value) != expected_keys
            or payload != _canonical(value)
            or value.get("scope_sha256") != scope.digest
            or type(value.get("ordinal")) is not int
            or not 0 <= value["ordinal"] < len(scope.actions)
            or value.get("action_sha256")
            != _digest(_read_regular(_action_path(root, job_id, value["ordinal"])))
            or not _valid_digest(value.get("request_body_sha256"))
            or type(value.get("request_body_length")) is not int
            or value["request_body_length"] < 0
            or type(value.get("reservation_index")) is not int
            or value["reservation_index"] < 0
            or type(value.get("retry_index", 0)) is not int
            or value.get("retry_index", 0) < 0
            or path
            != _reservation_path(
                root,
                job_id,
                value.get("ordinal", -1),
                value.get("retry_index", 0),
            )
        ):
            raise WorkflowJobAuthorizationError("job_reservation_evidence")
        records.append(value)
    records.sort(
        key=lambda item: item.get("reservation_index", item.get("ordinal", -1))
    )
    if (
        len(records) > scope.max_provider_attempts
        or [item["reservation_index"] for item in records] != list(range(len(records)))
        or sum(1 for item in records if item.get("retry_index", 0) > 0)
        > scope.max_retries
    ):
        raise WorkflowJobAuthorizationError("job_reservation_evidence")
    return records


def _reservation_paths(root: Path, job_id: str) -> list[Path]:
    return sorted(root.glob(f"workflow-bounded-job.{job_id}.reservation.*.json"))


def _prior_job_attempts_are_retryable(
    authorization: BoundedJobActionAuthorization,
    *,
    expected_count: int,
    request_body_sha256: str,
) -> bool:
    """Require complete, durably retryable evidence for every earlier send."""
    from ai_office.execution_evidence import (
        list_run_execution_evidence,
        load_normalized_result_evidence,
        load_raw_response_evidence,
    )

    attempts = [
        attempt
        for attempt in list_run_execution_evidence(
            authorization.store_root, authorization.run_id
        )
        if (attempt.workflow_id, attempt.step_id, attempt.step_index)
        == (
            authorization.workflow_id,
            authorization.step_id,
            authorization.step_index,
        )
    ]
    action_records = [
        item
        for item in _reservation_records(authorization.store_root, authorization.job_id)
        if item.get("action_sha256") == authorization.action_sha256
    ]
    if len(attempts) < expected_count or len(action_records) < expected_count:
        return False
    action_records.sort(key=lambda item: item.get("retry_index", 0))
    action_records = action_records[:expected_count]
    by_reservation = {attempt.job_reservation_sha256: attempt for attempt in attempts}
    if len(by_reservation) < expected_count:
        return False
    for retry_index, reservation in enumerate(action_records):
        reservation_path = _reservation_path(
            authorization.store_root,
            authorization.job_id,
            authorization.ordinal,
            retry_index,
        )
        reservation_sha256 = _digest(_read_regular(reservation_path))
        attempt = by_reservation.get(reservation_sha256)
        if attempt is None:
            return False
        if (
            attempt.request_body_sha256 != request_body_sha256
            or reservation.get("request_body_sha256") != request_body_sha256
            or reservation.get("retry_index", 0) != retry_index
            or reservation.get("action_sha256") != authorization.action_sha256
        ):
            return False
        try:
            raw = load_raw_response_evidence(
                authorization.store_root, authorization.run_id, attempt.attempt_id
            )
            normalized = load_normalized_result_evidence(
                authorization.store_root, authorization.run_id, attempt.attempt_id
            )
        except Exception:
            return False
        normalized_status = getattr(normalized.result, "status_code", None)
        if normalized_status is None:
            normalized_status = getattr(
                getattr(normalized.result, "response_diagnostics", None),
                "status_code",
                None,
            )
        if not (
            getattr(normalized.result, "category", None)
            in {"api_error", "invalid_response"}
            and normalized_status == raw.status_code
            and _retryable_http_response(raw.status_code, raw.body)
        ):
            return False
        try:
            validate_bounded_job_attempt_authority(
                WorkflowRunManifestStore(authorization.store_root),
                job_id=authorization.job_id,
                ordinal=authorization.ordinal,
                action_sha256=authorization.action_sha256,
                reservation_sha256=reservation_sha256,
                request_body_sha256=request_body_sha256,
                request_body_length=attempt.request_body_length,
                attempt_id=attempt.attempt_id,
                attempt_evidence_sha256=attempt.digest,
            )
        except Exception:
            return False
    return True


def validate_bounded_job_retry_claim(
    authorization: BoundedJobActionAuthorization,
    *,
    reservation_sha256: str,
    request_body_sha256: str,
    request_body_length: int,
) -> bool:
    """Bind a new same-action claim to the next approved retry reservation."""
    scope = load_bounded_workflow_job_scope(
        WorkflowRunManifestStore(authorization.store_root), authorization.job_id
    )
    reservation_path = next(
        (
            path
            for path in _reservation_paths(
                authorization.store_root, authorization.job_id
            )
            if _digest(_read_regular(path)) == reservation_sha256
        ),
        None,
    )
    if reservation_path is None:
        return False
    value = json.loads(_read_regular(reservation_path))
    retry_index = value.get("retry_index", 0)
    return bool(
        scope == authorization.scope
        and scope.schema_version == "workflow-bounded-job-scope.v2"
        and type(retry_index) is int
        and 1 <= retry_index <= scope.max_retries
        and value.get("ordinal") == authorization.ordinal
        and value.get("action_sha256") == authorization.action_sha256
        and value.get("request_body_sha256") == request_body_sha256
        and value.get("request_body_length") == request_body_length
        and _prior_job_attempts_are_retryable(
            authorization,
            expected_count=retry_index,
            request_body_sha256=request_body_sha256,
        )
    )


def bounded_job_retry_budget_remaining(
    authorization: BoundedJobActionAuthorization,
) -> bool:
    scope = load_bounded_workflow_job_scope(
        WorkflowRunManifestStore(authorization.store_root), authorization.job_id
    )
    records = _reservation_records(authorization.store_root, authorization.job_id)
    consumed = sum(1 for item in records if item.get("retry_index", 0) > 0)
    return bool(
        scope == authorization.scope
        and scope.schema_version == "workflow-bounded-job-scope.v2"
        and consumed < scope.max_retries
        and len(records) < scope.max_provider_attempts
        and time.time_ns() < scope.expires_at_ns
        and not _revoked(authorization.store_root, scope)
    )


def _retryable_http_response(status_code: int, body: bytes) -> bool:
    if status_code not in {429, 503}:
        return False
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return True
    if type(payload) is not dict:
        return True
    error = payload.get("error")
    if type(error) is not dict:
        return True
    markers = " ".join(
        str(error.get(key, "")) for key in ("code", "type", "message")
    ).casefold()
    return not any(
        marker in markers
        for marker in (
            "insufficient_quota",
            "quota_exceeded",
            "quota_exhausted",
            "quota exceeded",
            "quota exhausted",
            "usage limit reached",
            "billing",
            "payment required",
        )
    )


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
