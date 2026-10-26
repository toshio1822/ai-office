"""Test-only offline delivery proof; never a production publication client."""

from __future__ import annotations

import base64
import difflib
import hmac
import json
import os
import re
import stat
import subprocess
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from ai_office.engine.artifact import read_run_artifact
from ai_office.engine.local_execution import (
    FILE_CHANGE_SET_CONTENT_TYPE,
    LocalExecutionError,
    load_local_execution_authorization,
    load_local_execution_evidence,
)
from ai_office.engine.workflow_bounded_job import (
    BoundedDocumentJobOutcome,
    BoundedWorkflowJobScope,
    WorkflowJobAuthorizationError,
    inspect_bounded_document_job,
    load_bounded_workflow_job_scope,
)
from ai_office.engine.workflow_run_manifest import (
    WorkflowRunManifestStore,
    load_workflow_run_manifest,
)

_EFFECTS = (
    "local_commit",
    "remote_blob_create",
    "remote_tree_create",
    "remote_commit_create",
    "remote_ref_create",
    "draft_pr",
)
_REF_COMPONENT = re.compile(r"^[A-Za-z0-9._/-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PATH_TYPE = type(Path())
_TEST_APPROVAL_KEY = b"offline-only-synthetic-issuer-key"
_FAKE_CREDENTIAL_ENV = "AI_OFFICE_TEST_FAKE_GITHUB_TOKEN"
_FAKE_CREDENTIAL_VALUE = "offline-only-fake-installation-token"


class OfflineDeliveryError(RuntimeError):
    """Safe classification from the test-only publication boundary."""


class SimulatedEffectCrash(RuntimeError):
    """The test stopped after an effect but before writing its result record."""


class AmbiguousFakeResponse(RuntimeError):
    """The fake API stored the PR but intentionally lost its response."""


@dataclass(frozen=True)
class SyntheticDeliveryApproval:
    """Test-only signed claim; it is not an authenticated human identity."""

    issuer: str
    principal: str
    approval_id: str
    binding_sha256: str
    signature: str


@dataclass(frozen=True)
class FakeGitHubCredential:
    """Ephemeral fixture credential, deliberately absent from persisted state."""

    token: str
    repository_id: str
    permissions: frozenset[str]
    expires_at_ns: int


class FakeGitHubDeliveryAPI:
    """Persistent local fake for Git Database, ref and Draft PR REST effects."""

    def __init__(
        self,
        *,
        remote: Path,
        state_path: Path,
        credential: FakeGitHubCredential,
        drop_next_pr_response: bool = False,
    ) -> None:
        if (
            type(remote) is not _PATH_TYPE
            or type(state_path) is not _PATH_TYPE
            or type(credential) is not FakeGitHubCredential
            or not remote.is_absolute()
            or not state_path.is_absolute()
            or remote.resolve() == state_path.resolve()
        ):
            raise OfflineDeliveryError("fake_api_configuration")
        self.remote = remote.resolve(strict=True)
        self.state_path = state_path
        self.credential = credential
        self.drop_next_pr_response = drop_next_pr_response
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(self.state_path.parent.stat().st_mode) & 0o077:
            raise OfflineDeliveryError("fake_api_state_permissions")
        if self.state_path.exists():
            self._state()
        else:
            self._save_state(
                {"drafts": [], "requests": [], "schema_version": "fake-github.v1"}
            )

    @property
    def repository_id(self) -> str:
        return self.credential.repository_id

    @property
    def requests(self) -> list[dict[str, object]]:
        return list(self._state()["requests"])

    @property
    def posts(self) -> int:
        return sum(item["method"] == "POST" for item in self.requests)

    @property
    def reads(self) -> int:
        return sum(item["method"] == "GET" for item in self.requests)

    def ensure_scope(self, repository_id: str) -> None:
        self._authorize(repository_id, {"contents:write", "pull_requests:write"})

    def create_blob(self, *, repository_id: str, content: bytes) -> str:
        self._authorize(repository_id, {"contents:write"})
        if type(content) is not bytes:
            raise OfflineDeliveryError("fake_blob_content")
        # The Git command must consume the exact bytes, not text-normalized data.
        blob_sha = (
            _git_bytes(
                self.remote,
                "hash-object",
                "-w",
                "--stdin",
                env=_git_env(),
                input=content,
            )
            .decode("ascii")
            .strip()
        )
        self._record("POST", "/git/blobs", blob_sha)
        return blob_sha

    def get_blob(self, *, repository_id: str, blob_sha: str) -> bytes | None:
        self._authorize(repository_id, {"contents:write"})
        if not re.fullmatch(r"[0-9a-f]{40}", blob_sha):
            raise OfflineDeliveryError("fake_blob_identity")
        if _optional_git(self.remote, "cat-file", "-e", f"{blob_sha}^{{blob}}") is None:
            self._record("GET", f"/git/blobs/{blob_sha}", None)
            return None
        content = _git_bytes(self.remote, "cat-file", "blob", blob_sha)
        self._record("GET", f"/git/blobs/{blob_sha}", blob_sha)
        return content

    def create_tree(
        self,
        *,
        repository_id: str,
        base_revision: str,
        updates: list[dict[str, str]],
    ) -> str:
        self._authorize(repository_id, {"contents:write"})
        index = self.state_path.parent / f".fake-index-{os.getpid()}-{time.time_ns()}"
        env = _git_env()
        env["GIT_INDEX_FILE"] = str(index)
        try:
            _git(self.remote, "read-tree", base_revision, env=env)
            for update in updates:
                path = update["path"]
                mode = update["mode"]
                blob_sha = update["sha"]
                if (
                    not _relative_path(path)
                    or mode not in {"100644", "100755"}
                    or self.get_blob(repository_id=repository_id, blob_sha=blob_sha)
                    is None
                ):
                    raise OfflineDeliveryError("fake_tree_entry")
                _git(
                    self.remote,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    mode,
                    blob_sha,
                    path,
                    env=env,
                )
            tree_sha = _git(self.remote, "write-tree", env=env)
        finally:
            index.unlink(missing_ok=True)
            Path(f"{index}.lock").unlink(missing_ok=True)
        self._record("POST", "/git/trees", tree_sha)
        return tree_sha

    def get_tree(
        self, *, repository_id: str, tree_sha: str
    ) -> dict[str, tuple[str, str]] | None:
        self._authorize(repository_id, {"contents:write"})
        if not re.fullmatch(r"[0-9a-f]{40}", tree_sha):
            raise OfflineDeliveryError("fake_tree_identity")
        if _optional_git(self.remote, "cat-file", "-e", f"{tree_sha}^{{tree}}") is None:
            self._record("GET", f"/git/trees/{tree_sha}", None)
            return None
        entries = _tree_entries(self.remote, tree_sha)
        self._record("GET", f"/git/trees/{tree_sha}", tree_sha)
        return entries

    def create_commit(
        self,
        *,
        repository_id: str,
        commit_sha: str,
        raw_commit: bytes,
        tree_sha: str,
        parent_sha: str,
    ) -> str:
        self._authorize(repository_id, {"contents:write"})
        headers = raw_commit.split(b"\n\n", 1)[0].splitlines()
        fields = {line.split(b" ", 1)[0]: line.split(b" ", 1)[1] for line in headers}
        if fields.get(b"tree", b"").decode("ascii", "ignore") != tree_sha or [
            item[7:].decode("ascii", "ignore")
            for item in headers
            if item.startswith(b"parent ")
        ] != [parent_sha]:
            raise OfflineDeliveryError("fake_commit_binding")
        actual_sha = (
            _git_bytes(
                self.remote,
                "hash-object",
                "-w",
                "-t",
                "commit",
                "--stdin",
                env=_git_env(),
                input=raw_commit,
            )
            .decode("ascii")
            .strip()
        )
        if actual_sha != commit_sha:
            raise OfflineDeliveryError("fake_commit_identity")
        self._record("POST", "/git/commits", actual_sha)
        return actual_sha

    def get_commit(
        self, *, repository_id: str, commit_sha: str
    ) -> dict[str, str] | None:
        self._authorize(repository_id, {"contents:write"})
        if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
            raise OfflineDeliveryError("fake_commit_identity")
        if (
            _optional_git(self.remote, "cat-file", "-e", f"{commit_sha}^{{commit}}")
            is None
        ):
            self._record("GET", f"/git/commits/{commit_sha}", None)
            return None
        raw = _git_bytes(self.remote, "cat-file", "commit", commit_sha)
        headers = raw.split(b"\n\n", 1)[0].splitlines()
        trees = [
            item[5:].decode("ascii") for item in headers if item.startswith(b"tree ")
        ]
        parents = [
            item[7:].decode("ascii") for item in headers if item.startswith(b"parent ")
        ]
        if len(trees) != 1 or len(parents) != 1:
            raise OfflineDeliveryError("fake_commit_shape")
        self._record("GET", f"/git/commits/{commit_sha}", commit_sha)
        return {
            "commit_sha": commit_sha,
            "parent_sha": parents[0],
            "tree_sha": trees[0],
        }

    def create_ref(
        self, *, repository_id: str, target_ref: str, commit_sha: str, base_sha: str
    ) -> None:
        self._authorize(repository_id, {"contents:write"})
        if (
            not target_ref.startswith("refs/heads/codex/")
            or _optional_git(self.remote, "rev-parse", target_ref) is not None
            or _git(self.remote, "rev-parse", "refs/heads/main") != base_sha
        ):
            raise OfflineDeliveryError("fake_ref_scope")
        _git(self.remote, "update-ref", target_ref, commit_sha, "0" * len(commit_sha))
        self._record("POST", "/git/refs", target_ref)

    def get_ref(self, *, repository_id: str, target_ref: str) -> str | None:
        self._authorize(repository_id, {"contents:write"})
        commit_sha = _optional_git(self.remote, "rev-parse", target_ref)
        self._record("GET", f"/git/ref/{target_ref}", commit_sha)
        return commit_sha

    def create_draft(
        self,
        *,
        repository_id: str,
        base_ref: str,
        base_sha: str,
        head_ref: str,
        head_sha: str,
        diff_sha256: str,
    ) -> dict[str, object]:
        self._authorize(repository_id, {"pull_requests:write"})
        if (
            base_ref != "refs/heads/main"
            or self.get_ref(repository_id=repository_id, target_ref=base_ref)
            != base_sha
            or self.get_ref(repository_id=repository_id, target_ref=head_ref)
            != head_sha
        ):
            raise OfflineDeliveryError("fake_pr_binding")
        state = self._state()
        if any(
            item.get("repository_id") == repository_id
            and item.get("head_ref") == head_ref
            for item in state["drafts"]
        ):
            raise OfflineDeliveryError("draft_pr_already_exists")
        record = {
            "base_ref": base_ref,
            "base_sha": base_sha,
            "draft": True,
            "head_ref": head_ref,
            "head_sha": head_sha,
            "diff_sha256": diff_sha256,
            "repository_id": repository_id,
            "url": f"https://fake.invalid/pulls/{len(state['drafts']) + 1}",
        }
        state["drafts"].append(record)
        state["requests"].append(
            {"method": "POST", "endpoint": "/pulls", "object_id": head_sha}
        )
        self._save_state(state)
        if self.drop_next_pr_response:
            self.drop_next_pr_response = False
            raise AmbiguousFakeResponse
        return dict(record)

    def get_draft(
        self, *, repository_id: str, head_ref: str
    ) -> dict[str, object] | None:
        self._authorize(repository_id, {"pull_requests:write"})
        state = self._state()
        record = next(
            (
                item
                for item in state["drafts"]
                if item.get("repository_id") == repository_id
                and item.get("head_ref") == head_ref
            ),
            None,
        )
        state["requests"].append(
            {"method": "GET", "endpoint": "/pulls", "object_id": head_ref}
        )
        self._save_state(state)
        return dict(record) if record is not None else None

    def _authorize(self, repository_id: str, required: set[str]) -> None:
        credential = self.credential
        if (
            type(credential) is not FakeGitHubCredential
            or credential.repository_id != repository_id
            or not credential.token
            or credential.token != _FAKE_CREDENTIAL_VALUE
            or not required <= credential.permissions
            or credential.permissions
            != frozenset({"contents:write", "pull_requests:write"})
            or credential.expires_at_ns <= time.time_ns()
        ):
            raise OfflineDeliveryError("fake_credential_scope")

    def _record(self, method: str, endpoint: str, object_id: str | None) -> None:
        state = self._state()
        state["requests"].append(
            {"method": method, "endpoint": endpoint, "object_id": object_id}
        )
        self._save_state(state)

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"drafts": [], "requests": [], "schema_version": "fake-github.v1"}
        if stat.S_IMODE(self.state_path.stat().st_mode) & 0o077:
            raise OfflineDeliveryError("fake_api_state_permissions")
        state = _read_record(self.state_path)
        if (
            state.get("schema_version") != "fake-github.v1"
            or type(state.get("drafts")) is not list
            or type(state.get("requests")) is not list
        ):
            raise OfflineDeliveryError("fake_api_state")
        return state

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_canonical(state))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
            directory = os.open(
                self.state_path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def issue_test_delivery_grant(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    outcome: BoundedDocumentJobOutcome,
    *,
    workspace: Path,
    remote: Path,
    target_ref: str,
    verification_authorization_id: str,
    expires_at_ns: int,
    approval: object | None = None,
    now_ns: int | None = None,
) -> str:
    """Persist a synthetic grant under the existing Run store namespace."""
    now = time.time_ns() if now_ns is None else now_ns
    binding = _validate_candidate(
        store,
        scope,
        outcome,
        workspace=workspace,
        remote=remote,
        target_ref=target_ref,
        verification_authorization_id=verification_authorization_id,
        now_ns=now,
        require_target_absent=True,
    )
    if expires_at_ns <= now or expires_at_ns - now > 60 * 60 * 1_000_000_000:
        raise OfflineDeliveryError("grant_expiry")
    if approval is None:
        approval = _synthetic_human_approval(binding)
    approval_record = _validate_synthetic_approval(approval, binding)
    grant_body = {
        "approval": approval_record,
        "binding": binding,
        "effects": list(_EFFECTS),
        "expires_at_ns": expires_at_ns,
        "job_id": scope.job_id,
        "max_calls_per_effect": 1,
        "schema_version": "offline-git-delivery-test-grant.v1",
        "trust_note": "synthetic test grant; not authenticated human approval",
    }
    grant_id = _digest(_canonical(grant_body))
    grant = {**grant_body, "grant_id": grant_id}
    _write_once(_grant_path(store, scope.job_id, grant_id), _canonical(grant))
    return grant_id


def _synthetic_human_approval(
    binding: dict[str, object],
) -> SyntheticDeliveryApproval:
    approval_id = "synthetic-approval-" + _digest(_canonical(binding))[:20]
    payload = {
        "approval_id": approval_id,
        "binding_sha256": _digest(_canonical(binding)),
        "issuer": "offline-test-human-issuer",
        "principal": "test:authenticated-human-fixture",
    }
    signature = hmac.new(_TEST_APPROVAL_KEY, _canonical(payload), sha256).hexdigest()
    return SyntheticDeliveryApproval(**payload, signature=signature)


def _validate_synthetic_approval(
    approval: object, binding: dict[str, object]
) -> dict[str, str]:
    if type(approval) is SyntheticDeliveryApproval:
        record = {
            "approval_id": approval.approval_id,
            "binding_sha256": approval.binding_sha256,
            "issuer": approval.issuer,
            "principal": approval.principal,
            "signature": approval.signature,
        }
    elif type(approval) is dict:
        record = approval
    else:
        raise OfflineDeliveryError("synthetic_human_approval")
    expected_payload = {
        "approval_id": "synthetic-approval-" + _digest(_canonical(binding))[:20],
        "binding_sha256": _digest(_canonical(binding)),
        "issuer": "offline-test-human-issuer",
        "principal": "test:authenticated-human-fixture",
    }
    signature = hmac.new(
        _TEST_APPROVAL_KEY, _canonical(expected_payload), sha256
    ).hexdigest()
    if (
        type(record) is not dict
        or set(record) != {*expected_payload, "signature"}
        or any(record.get(key) != value for key, value in expected_payload.items())
        or type(record.get("signature")) is not str
        or not hmac.compare_digest(record["signature"], signature)
    ):
        raise OfflineDeliveryError("synthetic_human_approval")
    return {**expected_payload, "signature": signature}


def fake_github_credential_for_test(
    repository_id: str, *, expires_at_ns: int
) -> FakeGitHubCredential:
    """Build a fixed fake token with only the fake repo's required permissions."""
    token = os.environ.get(_FAKE_CREDENTIAL_ENV, _FAKE_CREDENTIAL_VALUE)
    if token != _FAKE_CREDENTIAL_VALUE:
        raise OfflineDeliveryError("fake_credential_scope")
    return FakeGitHubCredential(
        token=token,
        repository_id=repository_id,
        permissions=frozenset({"contents:write", "pull_requests:write"}),
        expires_at_ns=expires_at_ns,
    )


def revoke_test_delivery_grant(
    store: WorkflowRunManifestStore, job_id: str, grant_id: str
) -> None:
    _write_once(
        _revocation_path(store, job_id, grant_id),
        _canonical(
            {
                "grant_id": grant_id,
                "job_id": job_id,
                "schema_version": "offline-git-delivery-test-revocation.v1",
            }
        ),
    )


def run_offline_delivery(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    outcome: BoundedDocumentJobOutcome,
    *,
    workspace: Path,
    remote: Path,
    target_ref: str,
    verification_authorization_id: str,
    grant_id: str,
    fake_pr_api: FakeGitHubDeliveryAPI,
    now_ns: int | None = None,
    crash_after: str | None = None,
) -> dict[str, object]:
    """Drive the allowed local effects, reconciling old Attempts with reads only."""
    now = time.time_ns() if now_ns is None else now_ns
    grant_path = _grant_path(store, scope.job_id, grant_id)
    grant = _read_record(grant_path)
    grant_binding = grant.get("binding")
    if (
        grant.get("schema_version") != "offline-git-delivery-test-grant.v1"
        or _digest(_canonical({k: v for k, v in grant.items() if k != "grant_id"}))
        != grant_id
        or grant.get("grant_id") != grant_id
        or grant.get("job_id") != scope.job_id
        or tuple(grant.get("effects", ())) != _EFFECTS
        or type(grant_binding) is not dict
        or grant_binding.get("scope_sha256") != scope.digest
        or grant.get("max_calls_per_effect") != 1
    ):
        raise OfflineDeliveryError("grant_binding")
    _validate_synthetic_approval(grant.get("approval"), grant_binding)
    if type(fake_pr_api) is not FakeGitHubDeliveryAPI:
        raise OfflineDeliveryError("live_github_api_forbidden")
    fake_pr_api.ensure_scope(str(grant_binding["repository_id"]))
    if (
        fake_pr_api.remote.resolve()
        != Path(str(grant_binding["remote_path"])).resolve()
        or _paths_overlap(fake_pr_api.state_path, workspace)
        or _paths_overlap(fake_pr_api.state_path, store.root)
        or _paths_overlap(fake_pr_api.state_path, remote)
        or _paths_overlap(store.root, workspace)
        or _paths_overlap(store.root, remote)
        or _paths_overlap(workspace, remote)
    ):
        raise OfflineDeliveryError("offline_state_location")

    results: dict[str, dict[str, object]] = {}
    for effect in _EFFECTS:
        attempt_path = _attempt_path(store, scope.job_id, grant_id, effect)
        result_path = _result_path(store, scope.job_id, grant_id, effect)
        if result_path.exists():
            result = _read_record(result_path)
            if result.get("grant_id") != grant_id or result.get("effect") != effect:
                raise OfflineDeliveryError("effect_result_binding")
            _verify_effect_result(
                effect,
                result,
                workspace=workspace,
                remote=remote,
                target_ref=target_ref,
                fake_pr_api=fake_pr_api,
                repository_id=str(grant_binding["repository_id"]),
                base_revision=str(grant_binding["base_revision"]),
                diff_sha256=str(grant_binding["verification_diff_sha256"]),
            )
            results[effect] = result
            continue

        # An existing Attempt is resolved by reads only, even if its grant has
        # since expired or been revoked. Never replay an ambiguous effect.
        if attempt_path.exists():
            attempt = _read_record(attempt_path)
            if attempt.get("grant_id") != grant_id or attempt.get("effect") != effect:
                raise OfflineDeliveryError("effect_attempt_binding")
            result = _reconcile_effect(
                effect,
                grant_id=grant_id,
                workspace=workspace,
                remote=remote,
                target_ref=target_ref,
                fake_pr_api=fake_pr_api,
                repository_id=str(grant_binding["repository_id"]),
                base_revision=str(grant_binding["base_revision"]),
                artifact_id=str(grant_binding["artifact_id"]),
                artifact_sha256=str(grant_binding["artifact_sha256"]),
                diff_sha256=str(grant_binding["verification_diff_sha256"]),
                store=store,
                scope=scope,
            )
            _write_once(result_path, _canonical(result))
            results[effect] = result
            continue

        now = time.time_ns() if now_ns is None else now_ns
        if (
            grant["expires_at_ns"] <= now
            or _revocation_path(store, scope.job_id, grant_id).exists()
        ):
            raise OfflineDeliveryError("grant_expired_or_revoked")
        # Only unseen effects need a full revalidation: all reviewers, the
        # final Artifact/Evidence chain, clean pinned base, paths and fake ref.
        current_binding = _validate_candidate(
            store,
            scope,
            outcome,
            workspace=workspace,
            remote=remote,
            target_ref=target_ref,
            verification_authorization_id=verification_authorization_id,
            now_ns=now,
            require_target_absent=effect == "local_commit",
        )
        for key in (
            "artifact_id",
            "artifact_sha256",
            "base_revision",
            "manifest_digest",
            "remote_path",
            "repository_id",
            "review_artifact_id",
            "review_artifact_sha256",
            "run_id",
            "target_ref",
            "terminal_status",
            "verification_evidence_sha256",
            "verification_diff_sha256",
            "verification_authorization_id",
            "scope_sha256",
            "revisions_used",
        ):
            if grant_binding.get(key) != current_binding.get(key):
                raise OfflineDeliveryError("grant_binding")
        _write_once(
            attempt_path,
            _canonical(
                {
                    "artifact_id": grant["binding"]["artifact_id"],
                    "artifact_sha256": grant["binding"]["artifact_sha256"],
                    "effect": effect,
                    "grant_id": grant_id,
                    "job_id": scope.job_id,
                    "scope_sha256": scope.digest,
                    "schema_version": "offline-git-delivery-test-attempt.v1",
                }
            ),
        )
        # Candidate inspection can be slow. Recheck immediately after the
        # durable claim so expiry/revocation cannot authorize a later effect.
        now = time.time_ns() if now_ns is None else now_ns
        if (
            grant["expires_at_ns"] <= now
            or _revocation_path(store, scope.job_id, grant_id).exists()
        ):
            raise OfflineDeliveryError("grant_expired_or_revoked")
        if effect == "local_commit":
            commit_sha = _create_candidate_commit(
                workspace,
                base_revision=str(grant_binding["base_revision"]),
                target_ref=target_ref,
                scope=scope,
                store=store,
                artifact_id=str(grant_binding["artifact_id"]),
            )
            result = _effect_result(grant_id, effect, commit_sha=commit_sha)
        elif effect == "remote_blob_create":
            commit_sha = str(results["local_commit"]["commit_sha"])
            blob_ids = _create_fake_git_blobs(
                workspace,
                remote=fake_pr_api.remote,
                fake_api=fake_pr_api,
                repository_id=str(grant_binding["repository_id"]),
                target_ref=target_ref,
                store=store,
                scope=scope,
                artifact_id=str(grant_binding["artifact_id"]),
                base_revision=str(grant_binding["base_revision"]),
            )
            result = _effect_result(
                grant_id, effect, commit_sha=commit_sha, blobs=blob_ids
            )
        elif effect == "remote_tree_create":
            commit_sha = str(results["local_commit"]["commit_sha"])
            updates = results["remote_blob_create"]["blobs"]
            tree_sha = fake_pr_api.create_tree(
                repository_id=str(grant_binding["repository_id"]),
                base_revision=str(grant_binding["base_revision"]),
                updates=updates,
            )
            expected_tree = _git(workspace, "rev-parse", f"{commit_sha}^{{tree}}")
            if tree_sha != expected_tree:
                raise OfflineDeliveryError("remote_tree_mismatch")
            result = _effect_result(
                grant_id, effect, commit_sha=commit_sha, tree_sha=tree_sha
            )
        elif effect == "remote_commit_create":
            commit_sha = str(results["local_commit"]["commit_sha"])
            tree_sha = str(results["remote_tree_create"]["tree_sha"])
            raw_commit = _git_bytes(workspace, "cat-file", "commit", commit_sha)
            actual_sha = fake_pr_api.create_commit(
                repository_id=str(grant_binding["repository_id"]),
                commit_sha=commit_sha,
                raw_commit=raw_commit,
                tree_sha=tree_sha,
                parent_sha=str(grant_binding["base_revision"]),
            )
            result = _effect_result(grant_id, effect, commit_sha=actual_sha)
        elif effect == "remote_ref_create":
            commit_sha = str(results["remote_commit_create"]["commit_sha"])
            fake_pr_api.create_ref(
                repository_id=str(grant_binding["repository_id"]),
                target_ref=target_ref,
                commit_sha=commit_sha,
                base_sha=str(grant_binding["base_revision"]),
            )
            result = _effect_result(
                grant_id, effect, commit_sha=commit_sha, remote_ref=target_ref
            )
        else:
            commit_sha = str(results["local_commit"]["commit_sha"])
            remote_commit = str(results["remote_commit_create"]["commit_sha"])
            if (
                remote_commit != commit_sha
                or fake_pr_api.get_ref(
                    repository_id=str(grant_binding["repository_id"]),
                    target_ref=target_ref,
                )
                != commit_sha
            ):
                raise OfflineDeliveryError("remote_ref_mismatch")
            try:
                record = fake_pr_api.create_draft(
                    repository_id=str(grant_binding["repository_id"]),
                    base_ref="refs/heads/main",
                    base_sha=str(grant_binding["base_revision"]),
                    head_ref=target_ref,
                    head_sha=commit_sha,
                    diff_sha256=str(grant_binding["verification_diff_sha256"]),
                )
            except AmbiguousFakeResponse as exc:
                raise SimulatedEffectCrash("fake PR response lost") from exc
            result = _effect_result(
                grant_id,
                effect,
                commit_sha=commit_sha,
                draft_pr=record,
                diff_sha256=str(grant_binding["verification_diff_sha256"]),
            )
        if crash_after == effect:
            # Model process death after the external/local effect but before
            # its result record; a later call may only reconcile read-only.
            raise SimulatedEffectCrash(effect)
        _write_once(result_path, _canonical(result))
        results[effect] = result
    return {
        "artifact_id": grant_binding["artifact_id"],
        "commit_sha": results["remote_commit_create"]["commit_sha"],
        "draft_pr": results["draft_pr"]["draft_pr"],
        "effects": [results[item] for item in _EFFECTS],
        "remote_ref": target_ref,
    }


def resume_offline_delivery_process(payload: dict[str, str]) -> dict[str, object]:
    """Reopen persisted Run/API state as a fresh test process would."""
    store = WorkflowRunManifestStore(Path(payload["store_root"]))
    job_id = payload["job_id"]
    scope = load_bounded_workflow_job_scope(store, job_id)
    inspected = inspect_bounded_document_job(store, job_id)
    outcome = BoundedDocumentJobOutcome(
        status=inspected["status"],
        final_artifact_run_id=inspected["final_artifact_run_id"],
        final_artifact_id=inspected["final_artifact_id"],
        review_artifact_run_id=inspected["review_artifact_run_id"],
        review_artifact_id=inspected["review_artifact_id"],
        revisions_used=inspected["revisions_used"],
    )
    repository_id = payload["repository_id"]
    credential = fake_github_credential_for_test(
        repository_id, expires_at_ns=time.time_ns() + 60 * 60 * 1_000_000_000
    )
    api = FakeGitHubDeliveryAPI(
        remote=Path(payload["remote"]),
        state_path=Path(payload["api_state"]),
        credential=credential,
        drop_next_pr_response=os.environ.get("AI_OFFICE_TEST_DROP_PR_RESPONSE") == "1",
    )
    return run_offline_delivery(
        store,
        scope,
        outcome,
        workspace=Path(payload["workspace"]),
        remote=Path(payload["remote"]),
        target_ref=payload["target_ref"],
        verification_authorization_id=payload["verification_authorization_id"],
        grant_id=payload["grant_id"],
        fake_pr_api=api,
    )


def _validate_candidate(
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    outcome: BoundedDocumentJobOutcome,
    *,
    workspace: Path,
    remote: Path,
    target_ref: str,
    verification_authorization_id: str,
    now_ns: int,
    require_target_absent: bool,
) -> dict[str, object]:
    if now_ns <= 0 or type(outcome) is not BoundedDocumentJobOutcome:
        raise OfflineDeliveryError("job_not_reviewed")
    try:
        inspected = inspect_bounded_document_job(store, scope.job_id)
        persisted_scope = load_bounded_workflow_job_scope(store, scope.job_id)
        status = inspected.get("status")
        final_run_id = inspected.get("final_artifact_run_id")
        final_artifact_id = inspected.get("final_artifact_id")
        review_run_id = inspected.get("review_artifact_run_id")
        review_artifact_id = inspected.get("review_artifact_id")
        revisions_used = inspected.get("revisions_used")
        if (
            status not in {"candidate", "reviewed"}
            or persisted_scope != scope
            or type(final_run_id) is not str
            or type(final_artifact_id) is not str
            or type(review_run_id) is not str
            or type(review_artifact_id) is not str
            or type(revisions_used) is not int
            or (status == "candidate") != (revisions_used == 0)
            or (status == "reviewed") != (revisions_used > 0)
        ):
            raise OfflineDeliveryError("job_not_reviewed")
        # `inspect_bounded_document_job` rebuilds this terminal result from
        # immutable Job history, including every final-round PASS. A returned
        # outcome is only a consistency check; never use it as the artifact
        # selector or permission source.
        if (
            outcome.status != status
            or outcome.final_artifact_run_id != final_run_id
            or outcome.final_artifact_id != final_artifact_id
            or outcome.review_artifact_run_id != review_run_id
            or outcome.review_artifact_id != review_artifact_id
            or outcome.revisions_used != revisions_used
        ):
            raise OfflineDeliveryError("terminal_outcome_mismatch")
        artifact = read_run_artifact(store, final_run_id, final_artifact_id)
        review_artifact = read_run_artifact(store, review_run_id, review_artifact_id)
        manifest = load_workflow_run_manifest(store, artifact.run_id)
        source_manifest = load_workflow_run_manifest(store, scope.initial_run_id)
        assignment = json.loads(source_manifest.run_input)
        authorization = load_local_execution_authorization(
            store, artifact.run_id, verification_authorization_id
        )
        evidence = load_local_execution_evidence(
            store, artifact.run_id, verification_authorization_id
        )
    except (
        WorkflowJobAuthorizationError,
        LocalExecutionError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        raise OfflineDeliveryError("candidate_evidence") from exc
    if (
        type(workspace) is not _PATH_TYPE
        or type(remote) is not _PATH_TYPE
        or workspace.is_symlink()
        or remote.is_symlink()
        or artifact.content_type != FILE_CHANGE_SET_CONTENT_TYPE
        or artifact.manifest_digest != manifest.digest
        or artifact.run_id != outcome.final_artifact_run_id
        or artifact.artifact_id != outcome.final_artifact_id
        or review_artifact.artifact_id != outcome.review_artifact_id
        or authorization.artifact_id != artifact.artifact_id
        or authorization.artifact_sha256 != artifact.content_sha256
        or authorization.manifest_digest != artifact.manifest_digest
        or evidence.get("status") != "verified"
        or evidence.get("run_id") != artifact.run_id
        or evidence.get("artifact_id") != artifact.artifact_id
        or evidence.get("artifact_sha256") != artifact.content_sha256
        or evidence.get("base_revision") != authorization.policy.base_revision
    ):
        raise OfflineDeliveryError("candidate_evidence")
    if stat.S_IMODE(store.root.stat().st_mode) & 0o077:
        raise OfflineDeliveryError("run_store_permissions")
    evidence_links = []
    for link_path in store.root.glob(
        f"workflow-bounded-job.{scope.job_id}.local-effect.*.json"
    ):
        link = _read_record(link_path)
        if (
            link.get("artifact_id") == artifact.artifact_id
            and link.get("run_id") == artifact.run_id
            and link.get("authorization_id") == verification_authorization_id
        ):
            evidence_links.append(link)
    if (
        len(evidence_links) != 1
        or evidence_links[0].get("scope_sha256") != scope.digest
        or evidence_links[0].get("artifact_sha256") != artifact.content_sha256
        or evidence_links[0].get("evidence_sha256") != _digest(_canonical(evidence))
    ):
        raise OfflineDeliveryError("verification_link_missing")
    run_repository = assignment.get("repository")
    paths = assignment.get("allowed_paths")
    if (
        type(run_repository) is not dict
        or type(paths) is not dict
        or run_repository.get("path") != str(workspace.resolve())
        or run_repository.get("base_revision") != authorization.policy.base_revision
        or authorization.policy.base_revision != _git(workspace, "rev-parse", "HEAD")
        or _git(workspace, "status", "--porcelain=v1", "--untracked-files=all")
        or type(paths.get("read")) is not list
        or type(paths.get("write")) is not list
        or not set(authorization.policy.read_paths) <= set(paths["read"])
        or not set(authorization.policy.write_paths) <= set(paths["write"])
    ):
        raise OfflineDeliveryError("workspace_or_run_scope")
    try:
        change_set = _strict_json(artifact.content)
        changes = change_set["changes"]
    except (ValueError, KeyError, TypeError):
        raise OfflineDeliveryError("artifact_contract") from None
    if (
        type(change_set) is not dict
        or set(change_set) != {"schema_version", "base_revision", "changes"}
        or change_set.get("schema_version") != "file-change-set.v1"
        or change_set.get("base_revision") != run_repository["base_revision"]
        or type(changes) is not list
        or not changes
    ):
        raise OfflineDeliveryError("artifact_contract")
    allowed_writes = set(paths["write"]) & set(authorization.policy.write_paths)
    updated: dict[str, bytes] = {}
    base_entries = _tree_entries(workspace, authorization.policy.base_revision)
    for change in changes:
        if (
            type(change) is not dict
            or set(change) != {"path", "before_sha256", "content_base64"}
            or type(change["path"]) is not str
            or change["path"] in updated
            or change["path"] not in allowed_writes
            or not _relative_path(change["path"])
            or not _SHA256.fullmatch(change["before_sha256"])
        ):
            raise OfflineDeliveryError("path_scope")
        entry = base_entries.get(change["path"])
        if entry is None or entry[0] not in {"100644", "100755"}:
            raise OfflineDeliveryError("base_file_scope")
        old = _git_bytes(workspace, "cat-file", "blob", entry[1])
        try:
            new = base64.b64decode(change["content_base64"], validate=True)
            new.decode("utf-8")
        except (ValueError, UnicodeError):
            raise OfflineDeliveryError("artifact_bytes") from None
        if _digest(old) != change["before_sha256"]:
            raise OfflineDeliveryError("preimage_mismatch")
        _validate_attributes(workspace, [change["path"]])
        working_mode = stat.S_IMODE((workspace / change["path"]).stat().st_mode)
        expected_git_mode = "100755" if working_mode & 0o111 else "100644"
        if (
            (workspace / change["path"]).is_symlink()
            or not (workspace / change["path"]).is_file()
            or (workspace / change["path"]).read_bytes() != old
            or expected_git_mode != entry[0]
        ):
            raise OfflineDeliveryError("workspace_base_mismatch")
        updated[change["path"]] = new
    _validate_run_tree_evidence(
        workspace,
        policy=authorization.policy,
        base_revision=authorization.policy.base_revision,
        updates=updated,
        evidence=evidence,
    )
    if (
        not target_ref.startswith("refs/heads/codex/")
        or not _REF_COMPONENT.fullmatch(target_ref.removeprefix("refs/heads/"))
        or any(
            part in {"", ".", ".."} or part.endswith(".lock")
            for part in target_ref.split("/")
        )
    ):
        raise OfflineDeliveryError("protected_or_invalid_ref")
    if _git(workspace, "check-ref-format", target_ref) != "":
        raise OfflineDeliveryError("protected_or_invalid_ref")
    if (
        type(remote) is not _PATH_TYPE
        or not remote.is_absolute()
        or "://" in str(remote)
    ):
        raise OfflineDeliveryError("only_local_fake_remote_supported")
    remote_path = remote.resolve(strict=True)
    if _git(remote_path, "rev-parse", "--is-bare-repository") != "true":
        raise OfflineDeliveryError("fake_remote_not_bare")
    remote_base = _git(remote_path, "rev-parse", "refs/heads/main")
    if remote_base != authorization.policy.base_revision:
        raise OfflineDeliveryError("remote_base_mismatch")
    remote_ref = _optional_git(remote_path, "rev-parse", target_ref)
    local_ref = _optional_git(workspace, "rev-parse", target_ref)
    if require_target_absent and (remote_ref is not None or local_ref is not None):
        raise OfflineDeliveryError("target_ref_exists")
    if remote_ref not in {None, local_ref}:
        raise OfflineDeliveryError("remote_ref_mismatch")
    # This record is deliberately a synthetic offline capability. No authentic
    # human identity or credential is represented by approved_by.
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_sha256": artifact.content_sha256,
        "base_revision": authorization.policy.base_revision,
        "manifest_digest": artifact.manifest_digest,
        "remote_path": str(remote_path),
        "repository_id": _repository_identity(workspace),
        "run_id": artifact.run_id,
        "scope_sha256": scope.digest,
        "target_ref": target_ref,
        "terminal_status": status,
        "revisions_used": revisions_used,
        "verification_evidence_sha256": _digest(_canonical(evidence)),
        "verification_diff_sha256": evidence.get("diff_sha256"),
        "verification_authorization_id": verification_authorization_id,
        "review_artifact_id": review_artifact.artifact_id,
        "review_artifact_sha256": review_artifact.content_sha256,
    }


def _validate_run_tree_evidence(
    workspace: Path,
    *,
    policy,
    base_revision: str,
    updates: dict[str, bytes],
    evidence: dict[str, Any],
) -> None:
    _validate_attributes(workspace, list(policy.read_paths))
    entries = _tree_entries(workspace, base_revision)
    before: dict[str, tuple[int, bytes]] = {}
    after: dict[str, tuple[int, bytes]] = {}
    for path in policy.read_paths:
        entry = entries.get(path)
        if entry is None or entry[0] not in {"100644", "100755"}:
            raise OfflineDeliveryError("source_context_missing")
        content = _git_bytes(workspace, "cat-file", "blob", entry[1])
        mode = stat.S_IMODE((workspace / path).stat().st_mode)
        expected_git_mode = "100755" if mode & 0o111 else "100644"
        if (
            (workspace / path).is_symlink()
            or not (workspace / path).is_file()
            or (workspace / path).read_bytes() != content
            or expected_git_mode != entry[0]
        ):
            raise OfflineDeliveryError("source_context_mismatch")
        before[path] = (mode, content)
        after[path] = (mode, updates.get(path, content))
    if evidence.get("before_tree_sha256") != _tree_digest(before) or evidence.get(
        "after_tree_sha256"
    ) != _tree_digest(after):
        raise OfflineDeliveryError("verification_tree_mismatch")
    expected_diff = "".join(
        line
        for path in sorted(updates)
        for line in difflib.unified_diff(
            before[path][1].decode("utf-8").splitlines(keepends=True),
            updates[path].decode("utf-8").splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    if evidence.get("diff") != expected_diff or evidence.get("diff_sha256") != _digest(
        expected_diff.encode("utf-8")
    ):
        raise OfflineDeliveryError("verification_diff_mismatch")


def _create_candidate_commit(
    workspace: Path,
    *,
    base_revision: str,
    target_ref: str,
    scope: BoundedWorkflowJobScope,
    store: WorkflowRunManifestStore,
    artifact_id: str,
) -> str:
    if _optional_git(workspace, "rev-parse", target_ref) is not None:
        raise OfflineDeliveryError("target_ref_exists")
    artifact = _artifact_for_scope(store, scope, artifact_id)
    change_set = _strict_json(artifact.content)
    changes = change_set["changes"]
    base_entries = _tree_entries(workspace, base_revision)
    index = store.root / f".offline-index-{scope.job_id}-{os.getpid()}"
    env = _git_env()
    env["GIT_INDEX_FILE"] = str(index)
    try:
        _git(workspace, "read-tree", base_revision, env=env)
        for change in changes:
            path = change["path"]
            mode, _ = base_entries[path]
            content = base64.b64decode(change["content_base64"], validate=True)
            blob = (
                _git_bytes(workspace, "hash-object", "-w", "--stdin", input=content)
                .decode()
                .strip()
            )
            _git(
                workspace,
                "update-index",
                "--add",
                "--cacheinfo",
                mode,
                blob,
                path,
                env=env,
            )
        tree = _git(workspace, "write-tree", env=env)
        commit_env = {
            **env,
            "GIT_AUTHOR_NAME": "Offline Proof",
            "GIT_AUTHOR_EMAIL": "offline-proof@example.invalid",
            "GIT_COMMITTER_NAME": "Offline Proof",
            "GIT_COMMITTER_EMAIL": "offline-proof@example.invalid",
        }
        commit = _git(
            workspace,
            "-c",
            "commit.gpgsign=false",
            "commit-tree",
            tree,
            "-p",
            base_revision,
            "-m",
            "offline proof: apply reviewed candidate",
            env=commit_env,
        )
        _git(workspace, "update-ref", target_ref, commit, "0" * len(base_revision))
    finally:
        index.unlink(missing_ok=True)
        Path(f"{index}.lock").unlink(missing_ok=True)
    _verify_commit_candidate(workspace, base_revision, target_ref, artifact)
    return _git(workspace, "rev-parse", target_ref)


def _verify_commit_candidate(
    workspace: Path, base_revision: str, target_ref: str, artifact
) -> None:
    commit = _git(workspace, "rev-parse", target_ref)
    if _git(workspace, "show", "-s", "--format=%P", commit) != base_revision:
        raise OfflineDeliveryError("commit_parent_mismatch")
    change_set = _strict_json(artifact.content)
    expected_paths = {item["path"] for item in change_set["changes"]}
    rows = _git(
        workspace,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "--no-renames",
        "-r",
        base_revision,
        commit,
    ).splitlines()
    actual = {}
    for row in rows:
        parts = row.split("\t", 1)
        if len(parts) != 2:
            raise OfflineDeliveryError("commit_diff_invalid")
        actual[parts[1]] = parts[0]
    if set(actual) != expected_paths or any(
        status != "M" for status in actual.values()
    ):
        raise OfflineDeliveryError("commit_diff_mismatch")
    before_entries = _tree_entries(workspace, base_revision)
    after_entries = _tree_entries(workspace, commit)
    for change in change_set["changes"]:
        path = change["path"]
        before = before_entries.get(path)
        after = after_entries.get(path)
        content = base64.b64decode(change["content_base64"], validate=True)
        if (
            before is None
            or after is None
            or before[0] != after[0]
            or after[0] not in {"100644", "100755"}
        ):
            raise OfflineDeliveryError("commit_mode_mismatch")
        if _git_bytes(workspace, "cat-file", "blob", after[1]) != content:
            raise OfflineDeliveryError("commit_content_mismatch")


def _candidate_blob_updates(
    workspace: Path, base_revision: str, commit_sha: str
) -> list[dict[str, str]]:
    entries = _tree_entries(workspace, commit_sha)
    rows = _git(
        workspace,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "--no-renames",
        "-r",
        base_revision,
        commit_sha,
    ).splitlines()
    updates = []
    for row in rows:
        parts = row.split("\t", 1)
        if len(parts) != 2 or parts[0] != "M" or parts[1] not in entries:
            raise OfflineDeliveryError("commit_diff_mismatch")
        mode, blob_sha = entries[parts[1]]
        if mode not in {"100644", "100755"}:
            raise OfflineDeliveryError("commit_mode_mismatch")
        updates.append({"mode": mode, "path": parts[1], "sha": blob_sha})
    if not updates:
        raise OfflineDeliveryError("commit_diff_mismatch")
    return sorted(updates, key=lambda item: item["path"])


def _create_fake_git_blobs(
    workspace: Path,
    *,
    remote: Path,
    fake_api: FakeGitHubDeliveryAPI,
    repository_id: str,
    target_ref: str,
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
    artifact_id: str,
    base_revision: str,
) -> list[dict[str, str]]:
    artifact = _artifact_for_scope(store, scope, artifact_id)
    _verify_commit_candidate(workspace, base_revision, target_ref, artifact)
    commit_sha = _git(workspace, "rev-parse", target_ref)
    updates = _candidate_blob_updates(workspace, base_revision, commit_sha)
    for update in updates:
        content = _git_bytes(workspace, "cat-file", "blob", update["sha"])
        remote_sha = fake_api.create_blob(repository_id=repository_id, content=content)
        if remote_sha != update["sha"]:
            raise OfflineDeliveryError("remote_blob_mismatch")
    if _git(remote, "rev-parse", "refs/heads/main") != base_revision:
        raise OfflineDeliveryError("remote_base_mismatch")
    return updates


def _paths_overlap(first: Path, second: Path) -> bool:
    left = first.resolve()
    right = second.resolve()
    return left == right or left in right.parents or right in left.parents


def create_fake_bare_remote(remote: Path, workspace: Path, base_revision: str) -> None:
    """Create an offline-only bare fake seeded at its protected main ref."""
    subprocess.run(
        [
            "/usr/bin/git",
            "clone",
            "--quiet",
            "--bare",
            "--no-hardlinks",
            "--template=/dev/null",
            str(workspace),
            str(remote),
        ],
        check=True,
        capture_output=True,
        env=_git_env(),
        shell=False,
        timeout=10,
    )
    _git(remote, "config", "core.hooksPath", "/dev/null")
    _git(remote, "update-ref", "refs/heads/main", base_revision)
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")


def _reconcile_effect(
    effect: str,
    *,
    grant_id: str,
    workspace: Path,
    remote: Path,
    target_ref: str,
    fake_pr_api: FakeGitHubDeliveryAPI,
    repository_id: str,
    base_revision: str,
    artifact_id: str,
    artifact_sha256: str,
    diff_sha256: str,
    store: WorkflowRunManifestStore,
    scope: BoundedWorkflowJobScope,
) -> dict[str, object]:
    if effect == "local_commit":
        commit_sha = _optional_git(workspace, "rev-parse", target_ref)
        if commit_sha is None:
            raise OfflineDeliveryError("commit_outcome_ambiguous")
        artifact = _artifact_for_scope(store, scope, artifact_id)
        if artifact.content_sha256 != artifact_sha256:
            raise OfflineDeliveryError("artifact_changed")
        _verify_commit_candidate(workspace, base_revision, target_ref, artifact)
        return _effect_result(grant_id, effect, commit_sha=commit_sha, reconciled=True)
    local_commit = _optional_git(workspace, "rev-parse", target_ref)
    if effect == "remote_blob_create":
        if local_commit is None:
            raise OfflineDeliveryError("git_blob_outcome_ambiguous")
        artifact = _artifact_for_scope(store, scope, artifact_id)
        _verify_commit_candidate(workspace, base_revision, target_ref, artifact)
        updates = _candidate_blob_updates(workspace, base_revision, local_commit)
        for update in updates:
            content = _git_bytes(workspace, "cat-file", "blob", update["sha"])
            if (
                fake_pr_api.get_blob(
                    repository_id=repository_id, blob_sha=update["sha"]
                )
                != content
            ):
                raise OfflineDeliveryError("git_blob_outcome_ambiguous")
        return _effect_result(
            grant_id,
            effect,
            commit_sha=local_commit,
            blobs=updates,
            reconciled=True,
        )
    expected_tree = (
        _git(workspace, "rev-parse", f"{local_commit}^{{tree}}")
        if local_commit
        else None
    )
    if effect == "remote_tree_create":
        entries = (
            fake_pr_api.get_tree(repository_id=repository_id, tree_sha=expected_tree)
            if expected_tree is not None
            else None
        )
        if local_commit is None or entries != _tree_entries(workspace, local_commit):
            raise OfflineDeliveryError("git_tree_outcome_ambiguous")
        return _effect_result(
            grant_id,
            effect,
            commit_sha=local_commit,
            tree_sha=expected_tree,
            reconciled=True,
        )
    if effect == "remote_commit_create":
        record = (
            fake_pr_api.get_commit(repository_id=repository_id, commit_sha=local_commit)
            if local_commit is not None
            else None
        )
        if (
            record is None
            or record.get("commit_sha") != local_commit
            or record.get("parent_sha") != base_revision
            or record.get("tree_sha") != expected_tree
        ):
            raise OfflineDeliveryError("git_commit_outcome_ambiguous")
        return _effect_result(
            grant_id, effect, commit_sha=local_commit, reconciled=True
        )
    if effect == "remote_ref_create":
        remote_commit = fake_pr_api.get_ref(
            repository_id=repository_id, target_ref=target_ref
        )
        remote_base = fake_pr_api.get_ref(
            repository_id=repository_id, target_ref="refs/heads/main"
        )
        if (
            local_commit is None
            or remote_commit != local_commit
            or remote_base != base_revision
        ):
            raise OfflineDeliveryError("push_outcome_ambiguous")
        return _effect_result(
            grant_id,
            effect,
            commit_sha=local_commit,
            remote_ref=target_ref,
            reconciled=True,
        )
    record = fake_pr_api.get_draft(repository_id=repository_id, head_ref=target_ref)
    expected_commit = fake_pr_api.get_ref(
        repository_id=repository_id, target_ref=target_ref
    )
    if not _matches_fake_pr(
        record,
        repository_id,
        target_ref,
        expected_commit,
        base_revision,
        diff_sha256,
    ):
        raise OfflineDeliveryError("draft_pr_outcome_ambiguous")
    return _effect_result(
        grant_id,
        effect,
        commit_sha=str(expected_commit),
        draft_pr=record,
        reconciled=True,
    )


def _verify_effect_result(
    effect: str,
    result: dict[str, object],
    *,
    workspace: Path,
    remote: Path,
    target_ref: str,
    fake_pr_api: FakeGitHubDeliveryAPI,
    repository_id: str,
    base_revision: str,
    diff_sha256: str,
) -> None:
    commit = _optional_git(workspace, "rev-parse", target_ref)
    if effect == "local_commit" and commit != result.get("commit_sha"):
        raise OfflineDeliveryError("commit_result_mismatch")
    if effect == "remote_blob_create":
        for update in result.get("blobs", []):
            expected = _git_bytes(workspace, "cat-file", "blob", update["sha"])
            if (
                fake_pr_api.get_blob(
                    repository_id=repository_id, blob_sha=update["sha"]
                )
                != expected
            ):
                raise OfflineDeliveryError("blob_result_mismatch")
    if effect == "remote_tree_create":
        tree_sha = str(result.get("tree_sha"))
        if fake_pr_api.get_tree(
            repository_id=repository_id, tree_sha=tree_sha
        ) != _tree_entries(workspace, str(result.get("commit_sha"))):
            raise OfflineDeliveryError("tree_result_mismatch")
    if effect == "remote_commit_create":
        remote_commit = fake_pr_api.get_commit(
            repository_id=repository_id, commit_sha=str(result.get("commit_sha"))
        )
        if (
            remote_commit is None
            or remote_commit.get("parent_sha") != base_revision
            or remote_commit.get("tree_sha")
            != _git(workspace, "rev-parse", f"{commit}^{{tree}}")
        ):
            raise OfflineDeliveryError("commit_result_mismatch")
    if effect == "remote_ref_create" and (
        commit != result.get("commit_sha")
        or fake_pr_api.get_ref(repository_id=repository_id, target_ref=target_ref)
        != commit
    ):
        raise OfflineDeliveryError("push_result_mismatch")
    if effect == "draft_pr":
        record = fake_pr_api.get_draft(repository_id=repository_id, head_ref=target_ref)
        head = fake_pr_api.get_ref(repository_id=repository_id, target_ref=target_ref)
        base = fake_pr_api.get_ref(
            repository_id=repository_id, target_ref="refs/heads/main"
        )
        if record != result.get("draft_pr") or not _matches_fake_pr(
            record,
            repository_id,
            target_ref,
            head,
            base_revision,
            diff_sha256,
        ):
            raise OfflineDeliveryError("draft_pr_result_mismatch")
        if base != base_revision:
            raise OfflineDeliveryError("draft_pr_result_mismatch")


def _effect_result(
    grant_id: str,
    effect: str,
    *,
    commit_sha: str,
    remote_ref: str | None = None,
    draft_pr: dict[str, object] | None = None,
    blobs: list[dict[str, str]] | None = None,
    tree_sha: str | None = None,
    diff_sha256: str | None = None,
    reconciled: bool = False,
) -> dict[str, object]:
    return {
        "commit_sha": commit_sha,
        "draft_pr": draft_pr,
        "blobs": blobs,
        "tree_sha": tree_sha,
        "diff_sha256": diff_sha256,
        "effect": effect,
        "grant_id": grant_id,
        "reconciled_read_only": reconciled,
        "remote_ref": remote_ref,
        "schema_version": "offline-git-delivery-test-result.v1",
        "status": "completed",
    }


def _matches_fake_pr(
    record: dict[str, object] | None,
    repository_id: str,
    head_ref: str,
    head_sha: str | None,
    base_sha: str,
    diff_sha256: str,
) -> bool:
    return bool(
        type(record) is dict
        and head_sha is not None
        and record.get("repository_id") == repository_id
        and record.get("base_ref") == "refs/heads/main"
        and record.get("base_sha") == base_sha
        and record.get("head_ref") == head_ref
        and record.get("head_sha") == head_sha
        and record.get("diff_sha256") == diff_sha256
        and record.get("draft") is True
        and type(record.get("url")) is str
        and record["url"].startswith("https://fake.invalid/")
    )


def _artifact_for_scope(store, scope, artifact_id):
    inspected = inspect_bounded_document_job(store, scope.job_id)
    if (
        inspected.get("status") not in {"candidate", "reviewed"}
        or artifact_id != inspected.get("final_artifact_id")
        or type(inspected.get("final_artifact_run_id")) is not str
    ):
        raise OfflineDeliveryError("job_not_reviewed")
    return read_run_artifact(
        store, inspected["final_artifact_run_id"], inspected["final_artifact_id"]
    )


def _tree_entries(workspace: Path, revision: str) -> dict[str, tuple[str, str]]:
    raw = _git_bytes(workspace, "ls-tree", "-r", "-z", "--full-tree", revision)
    entries: dict[str, tuple[str, str]] = {}
    for item in raw.split(b"\x00"):
        if not item:
            continue
        header, path = item.split(b"\t", 1)
        mode, object_type, object_id = header.split(b" ")
        if object_type != b"blob":
            continue
        entries[path.decode("utf-8")] = (
            mode.decode("ascii"),
            object_id.decode("ascii"),
        )
    return entries


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return _git_bytes(root, *args, env=env).decode("utf-8").strip()


def _git_bytes(
    root: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input: bytes | None = None,
) -> bytes:
    command = [
        "/usr/bin/git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.attributesFile=/dev/null",
        "-C",
        str(root),
        *args,
    ]
    result = subprocess.run(
        command,
        input=input,
        capture_output=True,
        check=True,
        env=_git_env() if env is None else env,
        shell=False,
        timeout=10,
    )
    return result.stdout


def _optional_git(root: Path, *args: str) -> str | None:
    try:
        return _git(root, *args)
    except subprocess.CalledProcessError:
        return None


def _git_env() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def _repository_identity(workspace: Path) -> str:
    git_dir = Path(_git(workspace, "rev-parse", "--absolute-git-dir")).resolve()
    identity = git_dir.stat()
    return _digest(
        b"offline-repository-v1\x00"
        + os.fsencode(git_dir)
        + identity.st_dev.to_bytes(16, "big")
        + identity.st_ino.to_bytes(16, "big")
    )


def _validate_attributes(workspace: Path, paths: list[str]) -> None:
    for path in paths:
        lines = _git(
            workspace,
            "check-attr",
            "filter",
            "text",
            "eol",
            "ident",
            "working-tree-encoding",
            "--",
            path,
        ).splitlines()
        if any(
            line.rsplit(": ", 1)[-1] not in {"unspecified", "unset"} for line in lines
        ):
            raise OfflineDeliveryError("git_attribute_transform")


def _relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(
        value
        and "\\" not in value
        and "\x00" not in value
        and not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.parts[0] != ".git"
    )


def _tree_digest(snapshot: dict[str, tuple[int, bytes]]) -> str:
    return _digest(
        _canonical(
            [
                {"mode": mode, "path": path, "sha256": _digest(content)}
                for path, (mode, content) in sorted(snapshot.items())
            ]
        )
    )


def _strict_json(content: bytes | str) -> Any:
    def unique(items: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    return json.loads(content, object_pairs_hook=unique)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def _grant_path(store: WorkflowRunManifestStore, job_id: str, grant_id: str) -> Path:
    return store.root / f"{job_id}.offline-delivery.{grant_id}.grant.json"


def _revocation_path(
    store: WorkflowRunManifestStore, job_id: str, grant_id: str
) -> Path:
    return store.root / f"{job_id}.offline-delivery.{grant_id}.revoked.json"


def _attempt_path(
    store: WorkflowRunManifestStore, job_id: str, grant_id: str, effect: str
) -> Path:
    return store.root / f"{job_id}.offline-delivery.{grant_id}.{effect}.attempt.json"


def _result_path(
    store: WorkflowRunManifestStore, job_id: str, grant_id: str, effect: str
) -> Path:
    return store.root / f"{job_id}.offline-delivery.{grant_id}.{effect}.result.json"


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _read_record(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    value = _strict_json(raw)
    if type(value) is not dict or _canonical(value) != raw:
        raise OfflineDeliveryError("record_integrity")
    return value
