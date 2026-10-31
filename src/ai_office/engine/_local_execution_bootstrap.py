"""Trusted in-sandbox bootstrap for one bounded local file-change attempt."""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import resource
import selectors
import signal
import stat
import subprocess
import sys
import sysconfig
import time
import traceback
from pathlib import Path, PurePosixPath


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise ValueError
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError
    if parts[0] == ".git":
        raise ValueError
    return value


def _snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    result: dict[str, tuple[int, bytes]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if path.is_symlink():
            raise ValueError
        if stat.S_ISDIR(mode):
            result[relative] = (stat.S_IFDIR | (mode & 0o777), b"")
        elif stat.S_ISREG(mode):
            result[relative] = (mode & 0o777, path.read_bytes())
        else:
            raise ValueError
    return result


def _tree_digest(snapshot: dict[str, tuple[int, bytes]]) -> str:
    return _digest(
        _canonical(
            [
                {"mode": mode, "path": path, "sha256": _digest(content)}
                for path, (mode, content) in sorted(snapshot.items())
            ]
        )
    )


def _verify_trusted_bundle(expected_sha256: object) -> str | None:
    if expected_sha256 is None:
        if Path("/input/trusted").exists():
            raise ValueError
        return None
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError
    root = Path("/input/trusted")
    manifest_path = root / "manifest.json"
    metadata = manifest_path.lstat()
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or metadata.st_nlink != 1
        or metadata.st_size > 1_048_576
    ):
        raise ValueError
    manifest_bytes = manifest_path.read_bytes()
    if _digest(manifest_bytes) != expected_sha256:
        raise ValueError
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    if (
        type(manifest) is not dict
        or set(manifest)
        != {"schema_version", "python_cache_tag", "python_platform", "files"}
        or manifest["schema_version"] != "local-execution-trusted-bundle.v1"
        or manifest_bytes != _canonical(manifest)
        or manifest["python_cache_tag"] != sys.implementation.cache_tag
        or manifest["python_platform"] != sysconfig.get_platform()
        or type(manifest["files"]) is not list
        or not 1 <= len(manifest["files"]) <= 1024
    ):
        raise ValueError
    expected_files = {"manifest.json"}
    expected_directories: set[str] = set()
    roots: set[str] = set()
    total = 0
    previous_path = ""
    for item in manifest["files"]:
        if (
            type(item) is not dict
            or set(item) != {"path", "sha256", "size"}
            or type(item["path"]) is not str
            or type(item["sha256"]) is not str
            or type(item["size"]) is not int
        ):
            raise ValueError
        relative = _safe_path(item["path"])
        first = relative.partition("/")[0]
        if (
            first not in {"oracle", "site-packages", "source"}
            or not relative.startswith(f"{first}/")
            or relative <= previous_path
            or len(item["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in item["sha256"])
            or not 0 <= item["size"] <= 16_777_216
            or relative in expected_files
        ):
            raise ValueError
        previous_path = relative
        roots.add(first)
        total += item["size"]
        if total > 33_554_432:
            raise ValueError
        expected_files.add(relative)
        parent = PurePosixPath(relative).parent
        while str(parent) != ".":
            expected_directories.add(parent.as_posix())
            if len(expected_directories) > 4096:
                raise ValueError
            parent = parent.parent
    if roots != {"oracle", "site-packages", "source"}:
        raise ValueError
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            raise ValueError
        if path.is_dir():
            actual_directories.add(relative)
            if len(actual_directories) > 4096:
                raise ValueError
        elif path.is_file() and metadata.st_nlink == 1:
            actual_files.add(relative)
        else:
            raise ValueError
    if actual_files != expected_files or actual_directories != expected_directories:
        raise ValueError
    for item in manifest["files"]:
        path = root.joinpath(*item["path"].split("/"))
        metadata = path.lstat()
        if metadata.st_size != item["size"] or metadata.st_size > 16_777_216:
            raise ValueError
        if _digest(path.read_bytes()) != item["sha256"]:
            raise ValueError
    return "/input/trusted/source/src:/input/trusted/site-packages"


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _run_command(
    argv: list[str], *, timeout: float, output_budget: int, pythonpath: str | None
) -> tuple[dict[str, object], int]:
    command_env = {
        "HOME": "/nonexistent",
        "PATH": "/usr/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": "/tmp",
    }
    if pythonpath is not None:
        command_env["PYTHONPATH"] = pythonpath
    process = subprocess.Popen(
        argv,
        cwd="/workspace",
        env=command_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
        close_fds=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    status = "completed"
    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            status = "timeout"
            _terminate(process)
            break
        events = selector.select(min(remaining, 0.1))
        if process.poll() is not None and not events:
            # Drain pipe EOFs on the next selector pass.
            continue
        for key, _ in events:
            stream = key.fileobj
            try:
                chunk = os.read(stream.fileno(), 65536)
            except OSError:
                chunk = b""
            if not chunk:
                selector.unregister(stream)
                stream.close()
                continue
            used = sum(len(value) for value in outputs.values())
            remaining_output = output_budget - used
            if len(chunk) > remaining_output:
                if remaining_output > 0:
                    outputs[key.data].extend(chunk[:remaining_output])
                status = "output_limit"
                _terminate(process)
                for open_key in list(selector.get_map().values()):
                    selector.unregister(open_key.fileobj)
                    open_key.fileobj.close()
                break
            outputs[key.data].extend(chunk)
        if status != "completed":
            break
    return_code = process.wait()
    return (
        {
            "argv": argv,
            "exit_code": return_code,
            "status": status if status != "completed" else "completed",
            "stderr_base64": base64.b64encode(outputs["stderr"]).decode("ascii"),
            "stdout_base64": base64.b64encode(outputs["stdout"]).decode("ascii"),
        },
        return_code,
    )


def run(plan_path: Path) -> dict[str, object]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    pythonpath = _verify_trusted_bundle(plan.get("trusted_bundle_manifest_sha256"))
    limits = plan["limits"]
    resource.setrlimit(
        resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"])
    )
    resource.setrlimit(
        resource.RLIMIT_AS, (limits["memory_bytes"], limits["memory_bytes"])
    )
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (limits["max_file_bytes"], limits["max_file_bytes"]),
    )
    resource.setrlimit(
        resource.RLIMIT_NPROC,
        (limits["max_processes"], limits["max_processes"]),
    )
    resource.setrlimit(
        resource.RLIMIT_NOFILE,
        (limits["max_open_files"], limits["max_open_files"]),
    )
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    sources = plan["sources"]
    changes = plan["changes"]
    workspace = Path("/workspace")
    before: dict[str, tuple[int, bytes]] = {}
    for source in sources:
        relative = _safe_path(source["path"])
        if relative in before:
            raise ValueError
        input_path = Path("/input/sources") / relative
        content = input_path.read_bytes()
        if _digest(content) != source["sha256"]:
            raise ValueError
        if type(source["mode"]) is not int or not 0 <= source["mode"] <= 0o777:
            raise ValueError
        destination = workspace / relative
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            source["mode"],
        )
        os.fchmod(descriptor, source["mode"])
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        before[relative] = (source["mode"], content)
    before = _snapshot(workspace)
    before_digest = _tree_digest(before)
    after_patch = dict(before)
    for change in changes:
        relative = _safe_path(change["path"])
        current = after_patch.get(relative)
        content = base64.b64decode(change["content_base64"], validate=True)
        if current is None or _digest(current[1]) != change["before_sha256"]:
            raise ValueError
        if len(content) > plan["limits"]["max_file_bytes"]:
            raise ValueError
        content.decode("utf-8")
        destination = workspace / relative
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        after_patch[relative] = (current[0], content)
    after_patch = _snapshot(workspace)
    after_patch_digest = _tree_digest(after_patch)
    command_results: list[dict[str, object]] = []
    output_used = 0
    status = "verified"
    for argv in plan["commands"]:
        remaining_budget = plan["limits"]["max_output_bytes"] - output_used
        command_result, return_code = _run_command(
            argv,
            timeout=plan["limits"]["wall_timeout_seconds"],
            output_budget=remaining_budget,
            pythonpath=pythonpath,
        )
        output_used += len(base64.b64decode(command_result["stdout_base64"])) + len(
            base64.b64decode(command_result["stderr_base64"])
        )
        command_results.append(command_result)
        if command_result["status"] != "completed":
            status = str(command_result["status"])
            break
        if return_code != 0:
            status = "command_failed"
            command_result["status"] = "command_failed"
            break
        if _tree_digest(_snapshot(workspace)) != after_patch_digest:
            status = "workspace_changed_by_validation"
            break
    for argv in plan["commands"][len(command_results) :]:
        command_results.append(
            {
                "argv": argv,
                "exit_code": None,
                "status": "not_started",
                "stderr_base64": "",
                "stdout_base64": "",
            }
        )
    final_snapshot = _snapshot(workspace)
    if _tree_digest(final_snapshot) != after_patch_digest:
        status = "workspace_changed_by_validation"
    changed_paths = sorted(set(before) | set(final_snapshot))
    diff_lines: list[str] = []
    for relative in changed_paths:
        old = before.get(relative)
        new = final_snapshot.get(relative)
        if old == new:
            continue
        if old is None or new is None:
            status = "unexpected_file_set"
            continue
        old_text = old[1].decode("utf-8")
        new_text = new[1].decode("utf-8")
        diff_lines.extend(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
    diff = "".join(diff_lines)
    return {
        "after_tree_sha256": _tree_digest(final_snapshot),
        "before_tree_sha256": before_digest,
        "commands": command_results,
        "diff": diff,
        "diff_sha256": _digest(diff.encode("utf-8")),
        "network_namespace": os.readlink("/proc/self/ns/net"),
        "schema_version": "local-execution-result.v1",
        "status": status,
        "trusted_bundle_manifest_sha256": plan.get("trusted_bundle_manifest_sha256"),
    }


if __name__ == "__main__":
    try:
        result = run(Path("/input/plan.json"))
    except Exception as error:
        failed_frame = traceback.extract_tb(error.__traceback__)[-1]
        result = {
            "error_type": type(error).__name__,
            "error_location": [failed_frame.name, failed_frame.lineno],
            "schema_version": "local-execution-result.v1",
            "status": "sandbox_setup_failed",
        }
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
        raise SystemExit(2)
    sys.stdout.write(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
