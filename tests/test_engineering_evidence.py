"""Offline proof for bounded, explicit engineering evidence collection."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ai_office.cli as cli_module
from ai_office.cli import app
from ai_office.engineering_evidence import (
    MAX_CONTENT_CHARACTERS,
    EngineeringEvidenceError,
    PublicWebResponse,
    collect_engineering_evidence,
)
from ai_office.github_snapshot import GitHubRawResponse

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "workflows"
EMPLOYEES = ROOT / "employees"
runner = CliRunner()
SHA = "d" * 40
SOURCE = b"line one\nline two\nline three\n"
TOKEN = "synthetic-unit-test-credential"


def _github_response(value: object, status: int = 200) -> GitHubRawResponse:
    return GitHubRawResponse(
        status_code=status,
        body=json.dumps(value, ensure_ascii=False).encode("utf-8"),
    )


class FakeGitHub:
    def __init__(self, *, blob_sha: str | None = None) -> None:
        self.requests = []
        self.blob_sha = blob_sha or hashlib.sha1(
            b"blob " + str(len(SOURCE)).encode("ascii") + b"\0" + SOURCE,
            usedforsecurity=False,
        ).hexdigest()

    def __call__(self, request):
        self.requests.append(request)
        if request.path == "/repos/example/repo/issues/7":
            return _github_response(
                {
                    "number": 7,
                    "title": "USB not listed",
                    "state": "open",
                    "body": f"Observed only. token={TOKEN}",
                }
            )
        if request.path == f"/repos/example/repo/contents/src/device.cpp?ref={SHA}":
            return _github_response(
                {
                    "path": "src/device.cpp",
                    "encoding": "base64",
                    "sha": self.blob_sha,
                    "size": len(SOURCE),
                    "content": base64.b64encode(SOURCE).decode("ascii"),
                }
            )
        raise AssertionError(f"unexpected path: {request.path}")


def _request() -> dict[str, object]:
    return {
        "issues": ["https://github.com/example/repo/issues/7"],
        "source_files": [
            {
                "repository": "example/repo",
                "commit": SHA,
                "path": "src/device.cpp",
                "start_line": 2,
                "end_line": 3,
            }
        ],
        "web_documents": ["https://docs.example.test/guide"],
    }


def _web_response(url: str, hosts: frozenset[str]) -> PublicWebResponse:
    assert url == "https://docs.example.test/guide"
    assert hosts == frozenset({"docs.example.test"})
    return PublicWebResponse(
        200,
        (("content-type", "text/html; charset=utf-8"),),
        (
            "<h1>Guide</h1><script>must-not-appear()</script><p>Visible text "
            f"{TOKEN}</p>"
        ).encode(),
    )


def _fixed_clock() -> datetime:
    return datetime(2026, 10, 9, 12, 34, 56, tzinfo=UTC)


def test_collects_only_explicit_get_sources_and_emits_canonical_provenance() -> None:
    github = FakeGitHub()
    package_text, digest, status, unavailable = collect_engineering_evidence(
        _request(),
        allowed_web_hosts=["docs.example.test"],
        github_token=TOKEN,
        github_transport=github,
        web_transport=_web_response,
        clock=_fixed_clock,
    )
    package = json.loads(package_text)
    issue, source, web = package["sources"]

    assert status == "complete"
    assert package["collection_status"] == "complete"
    assert unavailable == ()
    assert digest == hashlib.sha256(package_text.encode()).hexdigest()
    assert [request.method for request in github.requests] == ["GET", "GET"]
    assert github.requests[0].path == "/repos/example/repo/issues/7"
    assert github.requests[1].path.endswith(f"?ref={SHA}")
    assert all(TOKEN not in value for value in (package_text, issue["content"]))
    assert issue["source_type"] == "github_issue"
    assert issue["status"] == "success"
    assert issue["retrieved_at"] == "2026-10-09T12:34:56Z"
    assert source["source_url"].endswith("src/device.cpp#L2-L3")
    assert source["content"] == "line two\nline three\n"
    assert source["commit_sha"] == SHA
    assert source["blob_sha"] == github.blob_sha
    assert source["source_sha256"] == hashlib.sha256(SOURCE).hexdigest()
    assert (
        source["content_sha256"]
        == hashlib.sha256(b"line two\nline three\n").hexdigest()
    )
    assert "Visible text" in web["content"]
    assert "must-not-appear" not in web["content"]
    assert TOKEN not in package_text
    assert package["trust_boundary"]


def test_partial_failure_is_visible_and_does_not_retry_or_follow_redirects() -> None:
    github = FakeGitHub()
    web_calls = []

    def redirect(url: str, hosts: frozenset[str]) -> PublicWebResponse:
        web_calls.append(url)
        return PublicWebResponse(302, (("location", "https://other.test/x"),), b"")

    text, _digest, status, unavailable = collect_engineering_evidence(
        _request(),
        allowed_web_hosts=["docs.example.test"],
        github_transport=github,
        web_transport=redirect,
        clock=_fixed_clock,
    )
    package = json.loads(text)

    assert status == "partial"
    assert package["collection_status"] == "partial"
    assert package["sources"][2]["status"] == "unavailable"
    assert package["sources"][2]["reason"] == "redirect_not_followed"
    assert unavailable == ("https://docs.example.test/guide",)
    assert len(github.requests) == 2
    assert web_calls == ["https://docs.example.test/guide"]


def test_missing_issue_is_explicit_and_not_retried() -> None:
    calls = []

    def missing(request):
        calls.append(request)
        return _github_response({"message": "Not Found"}, status=404)

    text, _digest, status, unavailable = collect_engineering_evidence(
        {
            "issues": ["https://github.com/example/repo/issues/7"],
            "source_files": [],
            "web_documents": [],
        },
        allowed_web_hosts=[],
        github_transport=missing,
        clock=_fixed_clock,
    )
    source = json.loads(text)["sources"][0]

    assert status == "partial"
    assert source["status"] == "unavailable"
    assert source["reason"] == "http_404"
    assert unavailable == ("https://github.com/example/repo/issues/7",)
    assert len(calls) == 1


def test_source_blob_sha_mismatch_is_unavailable_without_retry() -> None:
    github = FakeGitHub(blob_sha="e" * 40)
    request = {
        "issues": [],
        "source_files": _request()["source_files"],
        "web_documents": [],
    }
    text, _digest, status, unavailable = collect_engineering_evidence(
        request,
        allowed_web_hosts=[],
        github_transport=github,
        clock=_fixed_clock,
    )
    source = json.loads(text)["sources"][0]

    assert status == "partial"
    assert source["status"] == "unavailable"
    assert source["reason"] == "blob_content_mismatch"
    assert source["content"] is None
    assert unavailable == (source["source_url"],)
    assert len(github.requests) == 1


def test_oversized_issue_text_is_truncated_and_marks_package_partial() -> None:
    calls = []

    def long_issue(request):
        calls.append(request)
        return _github_response(
            {
                "number": 7,
                "title": "Long issue",
                "state": "open",
                "body": "x" * (MAX_CONTENT_CHARACTERS + 100),
            }
        )

    text, _digest, status, unavailable = collect_engineering_evidence(
        {
            "issues": ["https://github.com/example/repo/issues/7"],
            "source_files": [],
            "web_documents": [],
        },
        allowed_web_hosts=[],
        github_transport=long_issue,
        clock=_fixed_clock,
    )
    source = json.loads(text)["sources"][0]

    assert status == "partial"
    assert source["status"] == "partial"
    assert source["truncated"] is True
    assert len(source["content"]) == MAX_CONTENT_CHARACTERS
    assert unavailable == ("https://github.com/example/repo/issues/7",)
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("payload", "hosts"),
    [
        ({**_request(), "extra": True}, ["docs.example.test"]),
        (
            {
                "issues": [],
                "source_files": [
                    {
                        "repository": "example/repo",
                        "commit": "main",
                        "path": "src/device.cpp",
                        "start_line": None,
                        "end_line": None,
                    }
                ],
                "web_documents": [],
            },
            [],
        ),
        (
            {
                "issues": [],
                "source_files": [],
                "web_documents": ["http://docs.example.test/guide"],
            },
            ["docs.example.test"],
        ),
        (
            {
                "issues": [],
                "source_files": [],
                "web_documents": ["https://docs.example.test/guide"],
            },
            [],
        ),
    ],
)
def test_invalid_requests_are_rejected_before_network(payload, hosts) -> None:
    calls = []
    with pytest.raises(EngineeringEvidenceError):
        collect_engineering_evidence(
            payload,
            allowed_web_hosts=hosts,
            github_transport=lambda value: calls.append(value),
            web_transport=lambda url, allowed: calls.append(url),
        )
    assert calls == []


def test_cli_writes_private_output_exclusively_and_workflow_preview_is_offline(
    tmp_path: Path, monkeypatch
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_request()), encoding="utf-8")
    output = tmp_path / "evidence.json"
    github = FakeGitHub()
    created_modes = []
    original_open = os.open

    def observe_open(path, flags, mode=0o777, *, dir_fd=None):
        if path == output.name:
            created_modes.append(mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(cli_module.os, "open", observe_open)
    monkeypatch.setattr(
        cli_module,
        "collect_engineering_evidence",
        lambda request, **kwargs: collect_engineering_evidence(
            request,
            allowed_web_hosts=kwargs["allowed_web_hosts"],
            github_token=kwargs["github_token"],
            github_transport=github,
            web_transport=_web_response,
            clock=_fixed_clock,
        ),
    )
    previous_umask = os.umask(0)
    try:
        collected = runner.invoke(
            app,
            [
                "workflows",
                "collect-engineering-evidence",
                "--request",
                str(request_file),
                "--output",
                str(output),
                "--allowed-web-host",
                "docs.example.test",
            ],
        )
    finally:
        os.umask(previous_umask)
    assert collected.exit_code == 0, collected.stderr
    summary = json.loads(collected.stdout)
    assert summary["collection_status"] == "complete"
    assert created_modes == [0o600]
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    previous_request_count = len(github.requests)
    duplicate = runner.invoke(
        app,
        [
            "workflows",
            "collect-engineering-evidence",
            "--request",
            str(request_file),
            "--output",
            str(output),
            "--allowed-web-host",
            "docs.example.test",
        ],
    )
    assert duplicate.exit_code != 0
    assert output.read_bytes() == before
    assert len(github.requests) == previous_request_count

    def unexpected_provider_call():
        raise AssertionError("preview must not load a provider key")

    monkeypatch.setattr(
        cli_module, "load_openai_api_key_from_environment", unexpected_provider_call
    )
    preview = runner.invoke(
        app,
        [
            "workflows",
            "start",
            "investigate-calamares-usb-destination",
            "--run-id",
            "issue-721-collector-preview",
            "--run-input",
            output.read_text(encoding="utf-8"),
            "--run-store",
            str(tmp_path / "run-store"),
            "--directory",
            str(WORKFLOWS),
            "--employees-directory",
            str(EMPLOYEES),
            "--preview-only",
        ],
    )
    assert preview.exit_code == 0, preview.stderr
    preview_json = json.loads(preview.stdout)
    assert preview_json["status"] == "step_ready"
    assert preview_json["step_id"] == "analyze-calamares-evidence"
    assert preview_json["employee_id"] == "general-researcher"
    assert preview_json["run_input"] == before.decode("utf-8")
    assert not (tmp_path / "run-store").exists()


def test_cli_rejects_public_output_directory_before_network(
    tmp_path: Path, monkeypatch
) -> None:
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(_request()), encoding="utf-8")
    public_directory = tmp_path / "public"
    public_directory.mkdir(mode=0o755)
    public_directory.chmod(0o755)
    output = public_directory / "evidence.json"
    calls = []

    def unexpected_collection(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("public output path must be rejected before collection")

    monkeypatch.setattr(
        cli_module, "collect_engineering_evidence", unexpected_collection
    )
    result = runner.invoke(
        app,
        [
            "workflows",
            "collect-engineering-evidence",
            "--request",
            str(request_file),
            "--output",
            str(output),
            "--allowed-web-host",
            "docs.example.test",
        ],
    )

    assert result.exit_code == 2
    assert "output directory must be private" in result.stderr
    assert calls == []
    assert not output.exists()


def test_unsafe_private_address_is_rejected_before_connection(monkeypatch) -> None:
    import ai_office.engineering_evidence as module

    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (
                module.socket.AF_INET,
                module.socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1", 443),
            )
        ],
    )
    with pytest.raises(module._SourceUnavailable, match="non_public"):
        module._public_addresses("docs.example.test")
