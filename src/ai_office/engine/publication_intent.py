"""Signed, authority-free handoff of one verified publication candidate.

The intent is a provenance-bearing input for a later trusted publisher. It is
not a human approval, publication grant, or permission to perform an effect.
The signing key must remain outside the AI employee process; consumers need
only a pinned public key.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from collections.abc import Mapping
from hashlib import sha256
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from ai_office.engine.artifact import read_run_artifact
from ai_office.engine.local_execution import (
    FILE_CHANGE_SET_CONTENT_TYPE,
    load_local_execution_authorization,
    load_local_execution_evidence,
)
from ai_office.engine.workflow_bounded_job import (
    WorkflowJobAuthorizationError,
    inspect_bounded_document_job,
    load_bounded_workflow_job_scope,
)
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)

_HANDOFF_SCHEMA = "publication-intent-handoff.v1"
_INTENT_SCHEMA = "publication-intent.v1"
_AUTHORITY = "intent_only"
_MAX_HANDOFF_BYTES = 2_000_000
_MAX_ARTIFACT_BYTES = 1_048_576
_MAX_INTENT_TTL_NS = 60 * 60 * 1_000_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REF_CHARS = re.compile(r"^[A-Za-z0-9._/-]+$")
_EFFECTS = (
    "remote_blob_create",
    "remote_tree_create",
    "remote_commit_create",
    "remote_ref_create",
    "draft_pr",
)
PUBLICATION_INTENT_EFFECTS = _EFFECTS

PublicationEffect = Literal[
    "remote_blob_create",
    "remote_tree_create",
    "remote_commit_create",
    "remote_ref_create",
    "draft_pr",
]


class PublicationIntentError(ValueError):
    """Safe failure classification for intent creation or verification."""

    def __init__(self, classification: str = "contract") -> None:
        super().__init__("publication intent is invalid")
        self.classification = classification


class PublicationIntent(BaseModel):
    """Exact candidate and destination metadata, without publication authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["publication-intent.v1"]
    authority: Literal["intent_only"]
    source_run_id: str
    source_manifest_digest: str
    workflow_id: str
    job_id: str
    job_scope_sha256: str
    job_status: Literal["candidate", "reviewed"]
    revisions_used: int
    artifact_run_id: str
    manifest_digest: str
    artifact_id: str
    artifact_sha256: str
    artifact_content_type: str
    artifact_content_length: int
    artifact_content_base64: str
    review_artifact_run_id: str
    review_artifact_id: str
    review_artifact_sha256: str
    verification_authorization_id: str
    verification_attempt_id: str
    verification_evidence_sha256: str
    verification_diff_sha256: str
    repository_id: int
    repository_full_name: str
    base_ref: str
    base_revision: str
    target_ref: str
    requested_effects: tuple[PublicationEffect, ...]
    workflow_run_id: int
    workflow_run_attempt: int
    expires_at_ns: int

    @model_validator(mode="after")
    def validate_contract(self) -> PublicationIntent:
        for value in (
            self.source_manifest_digest,
            self.job_scope_sha256,
            self.manifest_digest,
            self.artifact_id,
            self.artifact_sha256,
            self.review_artifact_id,
            self.review_artifact_sha256,
            self.verification_authorization_id,
            self.verification_evidence_sha256,
            self.verification_diff_sha256,
        ):
            if not _SHA256.fullmatch(value):
                _raise("digest")
        for value in (
            self.source_run_id,
            self.workflow_id,
            self.job_id,
            self.artifact_run_id,
            self.review_artifact_run_id,
            self.verification_attempt_id,
        ):
            if not _IDENTIFIER.fullmatch(value):
                _raise("identity")
        if (
            self.revisions_used < 0
            or (self.job_status == "candidate") != (self.revisions_used == 0)
            or self.repository_id <= 0
            or not _FULL_NAME.fullmatch(self.repository_full_name)
            or not _valid_ref(self.base_ref)
            or not _valid_ref(self.target_ref, target=True)
            or self.base_ref == self.target_ref
            or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.base_revision)
            or self.requested_effects != _EFFECTS
            or self.workflow_run_id <= 0
            or self.workflow_run_attempt <= 0
            or self.expires_at_ns <= 0
            or self.authority != _AUTHORITY
            or self.artifact_content_type != FILE_CHANGE_SET_CONTENT_TYPE
            or not 0 <= self.artifact_content_length <= _MAX_ARTIFACT_BYTES
        ):
            _raise("scope")
        try:
            content = base64.b64decode(self.artifact_content_base64, validate=True)
        except (binascii.Error, ValueError):
            _raise("artifact_encoding")
        if (
            base64.b64encode(content).decode("ascii") != self.artifact_content_base64
            or len(content) != self.artifact_content_length
            or sha256(content).hexdigest() != self.artifact_sha256
        ):
            _raise("artifact_digest")
        return self


def build_publication_intent_handoff(
    store: WorkflowRunManifestStore,
    *,
    job_id: str,
    workflow_run_id: int,
    workflow_run_attempt: int,
    expires_at_ns: int,
    signing_key: Ed25519PrivateKey,
    now_ns: int | None = None,
) -> bytes:
    """Build a signed intent from the existing authoritative Run/Job records.

    The signed bytes are a handoff input, not a grant. Human approval and
    effect authorization are intentionally absent and must be checked by a
    later trusted publisher against GitHub's authenticated approval history.
    """
    now = time.time_ns() if now_ns is None else now_ns
    if (
        type(store) is not WorkflowRunManifestStore
        or not isinstance(signing_key, Ed25519PrivateKey)
        or type(workflow_run_id) is not int
        or type(workflow_run_attempt) is not int
        or workflow_run_id <= 0
        or workflow_run_attempt <= 0
        or type(expires_at_ns) is not int
        or expires_at_ns <= now
        or expires_at_ns - now > _MAX_INTENT_TTL_NS
    ):
        _raise("issuer_context")

    try:
        scope = load_bounded_workflow_job_scope(store, job_id)
        inspected = inspect_bounded_document_job(
            store, job_id, include_local_effect=True
        )
        status = inspected.get("status")
        artifact_run_id = inspected.get("final_artifact_run_id")
        artifact_id = inspected.get("final_artifact_id")
        review_run_id = inspected.get("review_artifact_run_id")
        review_artifact_id = inspected.get("review_artifact_id")
        revisions_used = inspected.get("revisions_used")
        final_local_effect = inspected.get("final_local_effect")
        if (
            status not in {"candidate", "reviewed"}
            or type(artifact_run_id) is not str
            or type(artifact_id) is not str
            or type(review_run_id) is not str
            or type(review_artifact_id) is not str
            or type(revisions_used) is not int
            or type(final_local_effect) is not dict
            or scope.source_manifest_run_id is None
            or scope.source_manifest_digest is None
        ):
            _raise("job_not_terminal")

        verification_authorization_id = final_local_effect.get("authorization_id")
        if type(verification_authorization_id) is not str or not _SHA256.fullmatch(
            verification_authorization_id
        ):
            _raise("verification_link")

        artifact = read_run_artifact(store, artifact_run_id, artifact_id)
        review_artifact = read_run_artifact(store, review_run_id, review_artifact_id)
        manifest = load_workflow_run_manifest(store, artifact_run_id)
        source_manifest = load_workflow_run_manifest(
            store, scope.source_manifest_run_id
        )
        authorization = load_local_execution_authorization(
            store, artifact_run_id, verification_authorization_id
        )
        evidence = load_local_execution_evidence(
            store, artifact_run_id, verification_authorization_id
        )
        assignment = _strict_json(source_manifest.run_input.encode("utf-8"))
        change_set = _strict_json(artifact.content)
    except PublicationIntentError:
        raise
    except (OSError, TypeError, ValueError, KeyError, WorkflowJobAuthorizationError):
        _raise("source_evidence")

    repository = assignment.get("repository") if type(assignment) is dict else None
    publication = assignment.get("publication") if type(assignment) is dict else None
    allowed_paths = (
        assignment.get("allowed_paths") if type(assignment) is dict else None
    )
    if (
        type(repository) is not dict
        or type(publication) is not dict
        or type(allowed_paths) is not dict
        or type(repository.get("full_name")) is not str
        or type(repository.get("id")) is not int
        or isinstance(repository.get("id"), bool)
        or type(repository.get("base_ref")) is not str
        or type(repository.get("base_revision")) is not str
        or type(publication.get("target_ref")) is not str
        or type(publication.get("requested_effects")) is not list
        or type(allowed_paths.get("read")) is not list
        or type(allowed_paths.get("write")) is not list
        or not set(authorization.policy.write_paths) <= set(allowed_paths["write"])
        or not set(authorization.policy.read_paths) <= set(allowed_paths["read"])
        or source_manifest.run_id != scope.source_manifest_run_id
        or source_manifest.digest != scope.source_manifest_digest
        or scope.expires_at_ns <= now
        or final_local_effect.get("run_id") != artifact.run_id
        or final_local_effect.get("artifact_id") != artifact.artifact_id
        or final_local_effect.get("artifact_sha256") != artifact.content_sha256
        or final_local_effect.get("scope_sha256") != scope.digest
        or artifact.run_id != artifact_run_id
        or artifact.artifact_id != artifact_id
        or artifact.manifest_digest != manifest.digest
        or artifact.workflow_id != manifest.workflow_id
        or review_artifact.run_id != review_run_id
        or review_artifact.artifact_id != review_artifact_id
        or artifact.content_type != FILE_CHANGE_SET_CONTENT_TYPE
        or authorization.run_id != artifact.run_id
        or authorization.manifest_digest != artifact.manifest_digest
        or authorization.artifact_id != artifact.artifact_id
        or authorization.artifact_sha256 != artifact.content_sha256
        or authorization.policy.expires_at_ns <= now
        or authorization.policy.base_revision != repository.get("base_revision")
        or evidence.get("status") != "verified"
        or evidence.get("run_id") != artifact.run_id
        or evidence.get("manifest_digest") != artifact.manifest_digest
        or evidence.get("artifact_id") != artifact.artifact_id
        or evidence.get("artifact_sha256") != artifact.content_sha256
        or evidence.get("authorization_id") != verification_authorization_id
        or evidence.get("base_revision") != repository.get("base_revision")
        or final_local_effect.get("evidence_sha256")
        != sha256(_canonical(evidence)).hexdigest()
        or type(evidence.get("attempt_id")) is not str
        or type(evidence.get("diff_sha256")) is not str
        or not _SHA256.fullmatch(evidence.get("diff_sha256", ""))
        or type(change_set) is not dict
        or set(change_set) != {"schema_version", "base_revision", "changes"}
        or change_set.get("schema_version") != "file-change-set.v1"
        or change_set.get("base_revision") != repository.get("base_revision")
        or type(change_set.get("changes")) is not list
        or not change_set["changes"]
        or any(
            type(change) is not dict
            or type(change.get("path")) is not str
            or change.get("path") not in authorization.policy.write_paths
            for change in change_set["changes"]
        )
        or type(repository.get("id")) is not int
        or type(workflow_run_id) is not int
        or type(workflow_run_attempt) is not int
    ):
        _raise("source_binding")

    try:
        intent = PublicationIntent.model_validate(
            {
                "schema_version": _INTENT_SCHEMA,
                "authority": _AUTHORITY,
                "source_run_id": source_manifest.run_id,
                "source_manifest_digest": source_manifest.digest,
                "workflow_id": artifact.workflow_id,
                "job_id": scope.job_id,
                "job_scope_sha256": scope.digest,
                "job_status": status,
                "revisions_used": revisions_used,
                "artifact_run_id": artifact.run_id,
                "manifest_digest": artifact.manifest_digest,
                "artifact_id": artifact.artifact_id,
                "artifact_sha256": artifact.content_sha256,
                "artifact_content_type": artifact.content_type,
                "artifact_content_length": artifact.content_length,
                "artifact_content_base64": base64.b64encode(artifact.content).decode(
                    "ascii"
                ),
                "review_artifact_run_id": review_artifact.run_id,
                "review_artifact_id": review_artifact.artifact_id,
                "review_artifact_sha256": review_artifact.content_sha256,
                "verification_authorization_id": verification_authorization_id,
                "verification_attempt_id": evidence["attempt_id"],
                "verification_evidence_sha256": sha256(
                    _canonical(evidence)
                ).hexdigest(),
                "verification_diff_sha256": evidence["diff_sha256"],
                "repository_id": repository["id"],
                "repository_full_name": repository["full_name"],
                "base_ref": repository["base_ref"],
                "base_revision": repository["base_revision"],
                "target_ref": publication["target_ref"],
                "requested_effects": tuple(publication["requested_effects"]),
                "workflow_run_id": workflow_run_id,
                "workflow_run_attempt": workflow_run_attempt,
                "expires_at_ns": expires_at_ns,
            }
        )
    except (TypeError, ValueError, ValidationError):
        _raise("intent_contract")

    payload = _canonical(intent.model_dump(mode="json"))
    public_bytes = _raw_public_key(signing_key.public_key())
    key_id = sha256(public_bytes).hexdigest()
    envelope = {
        "intent": intent.model_dump(mode="json"),
        "key_id": key_id,
        "schema_version": _HANDOFF_SCHEMA,
        "signature_base64": base64.b64encode(signing_key.sign(payload)).decode("ascii"),
    }
    encoded = _canonical(envelope)
    if len(encoded) > _MAX_HANDOFF_BYTES:
        _raise("handoff_size")
    return encoded


def verify_publication_intent_handoff(
    handoff: bytes,
    trusted_public_keys: Mapping[str, bytes],
    *,
    expected_workflow_run_id: int,
    expected_workflow_run_attempt: int,
    expected_repository_id: int,
    expected_repository_full_name: str,
    expected_base_ref: str,
    expected_base_revision: str,
    expected_target_ref: str,
    now_ns: int | None = None,
) -> PublicationIntent:
    """Independently verify the signature, source bytes, expiry and run context.

    Successful verification only returns a typed intent with
    ``authority='intent_only'``. This function never checks or infers human
    approval and cannot authorize a publication effect.
    """
    now = time.time_ns() if now_ns is None else now_ns
    if (
        type(handoff) is not bytes
        or not 0 < len(handoff) <= _MAX_HANDOFF_BYTES
        or not isinstance(trusted_public_keys, Mapping)
        or type(expected_workflow_run_id) is not int
        or type(expected_workflow_run_attempt) is not int
        or type(expected_repository_id) is not int
        or expected_workflow_run_id <= 0
        or expected_workflow_run_attempt <= 0
        or expected_repository_id <= 0
        or type(now) is not int
        or now <= 0
    ):
        _raise("consumer_context")
    try:
        envelope = _strict_json(handoff)
        if (
            type(envelope) is not dict
            or set(envelope)
            != {"intent", "key_id", "schema_version", "signature_base64"}
            or envelope.get("schema_version") != _HANDOFF_SCHEMA
            or type(envelope.get("intent")) is not dict
        ):
            _raise("handoff_shape")
        intent_bytes = _canonical(envelope["intent"])
        intent = PublicationIntent.model_validate_json(intent_bytes)
        if intent_bytes != _canonical(intent.model_dump(mode="json")):
            _raise("intent_canonical")
        key_id = envelope.get("key_id")
        signature_b64 = envelope.get("signature_base64")
        public_bytes = trusted_public_keys.get(key_id)
        if (
            type(key_id) is not str
            or not _SHA256.fullmatch(key_id)
            or type(public_bytes) is not bytes
            or sha256(public_bytes).hexdigest() != key_id
            or type(signature_b64) is not str
        ):
            _raise("issuer_key")
        signature = base64.b64decode(signature_b64, validate=True)
        if base64.b64encode(signature).decode("ascii") != signature_b64:
            _raise("signature_encoding")
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, intent_bytes)
        if _canonical(envelope) != handoff:
            _raise("handoff_canonical")
    except PublicationIntentError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        ValidationError,
        binascii.Error,
        InvalidSignature,
    ):
        _raise("handoff_integrity")

    if (
        intent.workflow_run_id != expected_workflow_run_id
        or intent.workflow_run_attempt != expected_workflow_run_attempt
        or intent.repository_id != expected_repository_id
        or intent.repository_full_name != expected_repository_full_name
        or intent.base_ref != expected_base_ref
        or intent.base_revision != expected_base_revision
        or intent.target_ref != expected_target_ref
    ):
        _raise("context_mismatch")
    if intent.expires_at_ns <= now or intent.expires_at_ns - now > _MAX_INTENT_TTL_NS:
        _raise("expired")
    return intent


def _valid_ref(value: str, *, target: bool = False) -> bool:
    if (
        type(value) is not str
        or len(value) > 255
        or not _REF_CHARS.fullmatch(value)
        or not value.startswith("refs/heads/")
        or ".." in value
        or "@{" in value
        or "//" in value
    ):
        return False
    parts = value.split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.startswith(".")
        or part.endswith(".")
        or part.endswith(".lock")
        for part in parts
    ):
        return False
    return not target or value.startswith("refs/heads/codex/")


def _raw_public_key(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _raise("serialization")


def _strict_json(raw: bytes) -> object:
    def pairs_no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    def reject_constant(_: str) -> None:
        raise ValueError("invalid JSON constant")

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs_no_duplicates,
        parse_constant=reject_constant,
    )


def _raise(classification: str) -> None:
    raise PublicationIntentError(classification)
