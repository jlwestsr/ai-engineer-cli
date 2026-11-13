"""Path sandbox, exclusions, and workspace-scoped engineering tools."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from ai_engineer.tools.registry import ToolRegistry

MAX_FILE_BYTES = 256 * 1024
MAX_OUTPUT_CHARS = 16_000
MAX_SCAN_FILES = 5_000
SEARCH_MAX_SECONDS = 15.0
EXCLUDED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".aie",
}
ALLOWED_HIDDEN_NAMES = {
    ".github",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".pre-commit-config.yaml",
    ".python-version",
    ".coveragerc",
    ".flake8",
    ".dockerignore",
    ".env.example",
}
SECRET_PATTERNS = (".env*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*")


def scrub_subprocess_env(environment: dict[str, str]) -> dict[str, str]:
    """Drop credential-like and process-control variables from a child-process env copy."""
    from ai_engineer.envpolicy import is_denied_env_key

    cleaned = dict(environment)
    for name in list(cleaned):
        if is_denied_env_key(name, for_envfile=False):
            cleaned.pop(name, None)
    cleaned["GIT_TERMINAL_PROMPT"] = "0"
    return cleaned


def object_schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


class WorkspaceTools:
    """Inspection, planning, and explicitly permitted workspace actions."""

    def __init__(
        self,
        root: Path,
        *,
        allow_edits: bool = False,
        allow_tests: bool = False,
        command_timeout: int = 30,
    ) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace must be a directory")
        if not 1 <= command_timeout <= 300:
            raise ValueError("command_timeout must be between 1 and 300 seconds")
        self.allow_edits = allow_edits
        self.allow_tests = allow_tests
        self.command_timeout = command_timeout
        self._plan: list[dict[str, str]] = []
        self._scan_truncated = False

    @staticmethod
    def _excluded(parts: tuple[str, ...]) -> bool:
        return any(
            part in EXCLUDED_DIRECTORIES
            or (part.startswith(".") and part not in ALLOWED_HIDDEN_NAMES)
            or (
                part not in ALLOWED_HIDDEN_NAMES
                and any(fnmatch.fnmatch(part.lower(), pattern) for pattern in SECRET_PATTERNS)
            )
            for part in parts
        )

    def _path(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or self._excluded(path.parts):
            raise ValueError("Path must stay within the workspace and avoid excluded files")
        current = self.root
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Symbolic links are not accessible through workspace tools")
        resolved = current.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Path escapes the workspace")
        return resolved

    def _files(self) -> Iterator[Path]:
        self._scan_truncated = False
        inspected = 0
        for directory, directories, filenames in os.walk(self.root, followlinks=False):
            parent = Path(directory)
            directories[:] = sorted(
                name
                for name in directories
                if not self._excluded((name,)) and not (parent / name).is_symlink()
            )
            for name in sorted(filenames):
                inspected += 1
                if inspected > MAX_SCAN_FILES:
                    self._scan_truncated = True
                    return
                path = parent / name
                if not self._excluded((name,)) and not path.is_symlink():
                    yield path

    def _text(self, relative: str) -> tuple[Path, bytes, str]:
        path = self._path(relative)
        if not path.is_file():
            raise ValueError("Path must identify an existing regular file")
        try:
            # O_NOFOLLOW rejects a final-component symlink planted after _path().
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as error:
            raise ValueError("Path must identify an existing regular file") from error
        try:
            with os.fdopen(descriptor, "rb") as source:
                data = source.read(MAX_FILE_BYTES + 1)
        except OSError as error:
            raise ValueError("Path must identify an existing regular file") from error
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("File exceeds the 256 KiB tool limit")
        if b"\0" in data:
            raise ValueError("Binary files are not supported")
        return path, data, data.decode("utf-8")

    def set_permissions(
        self, *, allow_edits: bool | None = None, allow_tests: bool | None = None
    ) -> None:
        if allow_edits is not None:
            self.allow_edits = allow_edits
        if allow_tests is not None:
            self.allow_tests = allow_tests

    def register(self, registry: ToolRegistry) -> None:
        string = {"type": "string"}
        integer = {"type": "integer"}
        registry.register(
            "list_files",
            "List workspace files; excludes hidden/generated/credential paths.",
            object_schema({"pattern": string, "limit": integer}),
            self.list_files,
        )
        registry.register(
            "read_file",
            "Read numbered UTF-8 lines and a SHA-256 for a future guarded edit.",
            object_schema(
                {"path": string, "start_line": integer, "end_line": integer},
                ("path",),
            ),
            self.read_file,
        )
        registry.register(
            "search_text",
            "Search for a literal string, returning file/line evidence.",
            object_schema(
                {
                    "text": string,
                    "pattern": string,
                    "limit": integer,
                    "ignore_case": {"type": "boolean"},
                },
                ("text",),
            ),
            self.search_text,
        )
        registry.register(
            "python_symbols",
            "Outline Python classes/functions with signatures and line ranges; never imports code.",
            object_schema({"path": string}, ("path",)),
            self.python_symbols,
        )
        registry.register(
            "git_status",
            "Inspect Git status under the workspace; never commits or pushes.",
            object_schema({}),
            self.git_status,
        )
        registry.register(
            "git_diff",
            "Inspect tracked changes; staged selects the index instead of worktree.",
            object_schema({"staged": {"type": "boolean"}}),
            self.git_diff,
        )
        registry.register(
            "update_plan",
            "Replace the task plan with up to 20 explicit steps and statuses.",
            object_schema(
                {
                    "steps": {
                        "type": "array",
                        "items": object_schema(
                            {"title": string, "status": string},
                            ("title", "status"),
                        ),
                    }
                },
                ("steps",),
            ),
            self.update_plan,
        )
        registry.register(
            "get_plan",
            "Read the current instance's plan without modifying it.",
            object_schema({}),
            self.get_plan,
        )
        registry.register(
            "get_current_time",
            "Get the current local time.",
            object_schema({}),
            self.get_current_time,
        )
        self._register_gated(registry)

    def _register_gated(self, registry: ToolRegistry) -> None:
        string = {"type": "string"}
        if self.allow_edits:
            if "edit_file" not in registry.names:
                registry.register(
                    "edit_file",
                    "Replace exactly one text occurrence in an existing file after SHA-256 validation.",
                    object_schema(
                        {
                            "path": string,
                            "expected_sha256": string,
                            "old_text": string,
                            "new_text": string,
                        },
                        ("path", "expected_sha256", "old_text", "new_text"),
                    ),
                    self.edit_file,
                )
            if "create_file" not in registry.names:
                registry.register(
                    "create_file",
                    "Create one new UTF-8 file; never overwrites an existing file.",
                    object_schema({"path": string, "content": string}, ("path", "content")),
                    self.create_file,
                )
        else:
            registry.unregister("edit_file")
            registry.unregister("create_file")
        if self.allow_tests:
            if "run_python_tests" not in registry.names:
                registry.register(
                    "run_python_tests",
                    "Run 'python -m pytest -q' for the repository test suite. This executes trusted repository code.",
                    object_schema({}),
                    self.run_python_tests,
                )
        else:
            registry.unregister("run_python_tests")

    def refresh_registry(self, registry: ToolRegistry) -> None:
        """Re-apply gated tools after mid-session permission changes."""
        self._register_gated(registry)

    def context(self) -> str:
        permissions = (
            f"Workspace: {self.root}\n"
            f"File edits permitted: {self.allow_edits}\n"
            f"Repository tests permitted: {self.allow_tests}\n"
        )
        try:
            _, _, instructions = self._text("AGENTS.md")
        except (OSError, ValueError):
            return permissions + "Root AGENTS.md is absent or unreadable."
        suffix = "\n[AGENTS.md truncated]" if len(instructions) > 8_000 else ""
        return permissions + "Root project instructions:\n" + instructions[:8_000] + suffix

    def list_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = arguments.get("limit", 100)
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        paths = []
        for path in self._files():
            relative = path.relative_to(self.root).as_posix()
            if fnmatch.fnmatch(relative, arguments.get("pattern", "*")):
                paths.append(relative)
                if len(paths) >= limit:
                    return {
                        "files": paths,
                        "limit_reached": True,
                        "scan_truncated": self._scan_truncated,
                        "scan_limit": MAX_SCAN_FILES,
                    }
        return {
            "files": paths,
            "limit_reached": False,
            "scan_truncated": self._scan_truncated,
            "scan_limit": MAX_SCAN_FILES,
        }

    def read_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        start = arguments.get("start_line", 1)
        end = arguments.get("end_line", start + 199)
        if start < 1 or end < start or end - start >= 200:
            raise ValueError(
                "Read an inclusive range of 1 to 200 lines, starting at line 1 or later"
            )
        path, data, text = self._text(arguments["path"])
        lines = text.splitlines()
        content = "\n".join(
            f"{number}: {line}" for number, line in enumerate(lines, 1) if start <= number <= end
        )
        char_truncated = len(content) > MAX_OUTPUT_CHARS
        return {
            "path": path.relative_to(self.root).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "total_lines": len(lines),
            "content": content[:MAX_OUTPUT_CHARS],
            "truncated": end < len(lines) or char_truncated,
        }

    def search_text(self, arguments: dict[str, Any]) -> dict[str, Any]:
        needle = arguments["text"]
        limit = arguments.get("limit", 50)
        if not needle or len(needle) > 200 or not 1 <= limit <= 100:
            raise ValueError("text must contain 1-200 characters and limit must be 1-100")
        ignore_case = arguments.get("ignore_case", False)
        if ignore_case:
            needle = needle.casefold()
        matches: list[dict[str, Any]] = []
        skipped = 0
        timed_out = False
        started = time.monotonic()
        for path in self._files():
            if time.monotonic() - started >= SEARCH_MAX_SECONDS:
                timed_out = True
                break
            relative = path.relative_to(self.root).as_posix()
            if not fnmatch.fnmatch(relative, arguments.get("pattern", "*")):
                continue
            try:
                _, _, text = self._text(relative)
            except (OSError, ValueError):
                skipped += 1
                continue
            for number, line in enumerate(text.splitlines(), 1):
                haystack = line.casefold() if ignore_case else line
                if needle in haystack:
                    matches.append({"path": relative, "line": number, "text": line[:500]})
                    if len(matches) >= limit:
                        return {
                            "matches": matches,
                            "limit_reached": True,
                            "scan_truncated": self._scan_truncated,
                            "timed_out": False,
                            "skipped": skipped,
                            "scan_limit": MAX_SCAN_FILES,
                            "time_limit_seconds": SEARCH_MAX_SECONDS,
                        }
        return {
            "matches": matches,
            "limit_reached": False,
            "scan_truncated": self._scan_truncated,
            "timed_out": timed_out,
            "skipped": skipped,
            "scan_limit": MAX_SCAN_FILES,
            "time_limit_seconds": SEARCH_MAX_SECONDS,
        }

    def python_symbols(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _, _, text = self._text(arguments["path"])
        try:
            tree = ast.parse(text)
        except SyntaxError as error:
            raise ValueError(f"Invalid Python syntax at line {error.lineno}") from error
        definitions = sorted(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            ),
            key=lambda node: node.lineno,
        )
        symbols = []
        for node in definitions[:200]:
            docstring = ast.get_docstring(node)
            symbols.append(
                {
                    "name": node.name,
                    "kind": type(node).__name__,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "arguments": (
                        ast.unparse(node.args) if not isinstance(node, ast.ClassDef) else None
                    ),
                    "summary": docstring.splitlines()[0][:300] if docstring else None,
                }
            )
        return {"symbols": symbols, "truncated": len(definitions) > 200}

    def _command(self, command: list[str]) -> dict[str, Any]:
        if os.name != "posix":
            raise ValueError("Command tools currently require a POSIX host")
        environment = scrub_subprocess_env(os.environ.copy())
        started = time.monotonic()
        output = bytearray()
        output_bytes = 0
        timed_out = False
        with subprocess.Popen(
            command,
            cwd=self.root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ) as process:
            assert process.stdout is not None
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        remaining = self.command_timeout - (time.monotonic() - started)
                        if remaining <= 0:
                            timed_out = True
                            break
                        for key, _ in selector.select(min(remaining, 0.1)):
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            output_bytes += len(chunk)
                            output.extend(chunk[: max(0, MAX_OUTPUT_CHARS - len(output))])
                    if not timed_out:
                        try:
                            process.wait(
                                timeout=max(
                                    0.01,
                                    self.command_timeout - (time.monotonic() - started),
                                )
                            )
                        except subprocess.TimeoutExpired:
                            timed_out = True
                    if timed_out:
                        self._terminate(process)
                    process.wait()
            except BaseException:
                self._terminate(process)
                raise
        result: dict[str, Any] = {
            "command": command,
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "output": output.decode("utf-8", errors="replace"),
            "truncated": output_bytes > len(output) or timed_out,
        }
        if timed_out:
            result["error"] = "Command timed out"
        elif process.returncode not in (0, None):
            result["error"] = f"Command exited with code {process.returncode}"
        return result

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        """Ask the process group to exit, then force-kill if it stays up."""
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return
        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def _git_command(self, arguments: list[str]) -> dict[str, Any]:
        return self._command(
            [
                "git",
                "--no-pager",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=/dev/null",
                "--literal-pathspecs",
                *arguments,
            ]
        )

    def _git_workspace_prefix(self) -> str:
        """Prefix to strip from porcelain paths (always repo-root relative)."""
        listing = self._git_command(["rev-parse", "--show-toplevel"])
        if listing["exit_code"] != 0 or listing.get("timed_out") or listing.get("truncated"):
            return ""
        top = Path(listing["output"].strip()).resolve()
        try:
            relative = self.root.relative_to(top)
        except ValueError:
            return ""
        if relative == Path("."):
            return ""
        return relative.as_posix().rstrip("/") + "/"

    def _workspace_git_path(self, repo_relative: str, *, prefix: str) -> str | None:
        """Map a repo-root porcelain path into a workspace-relative path, or None if out of scope."""
        name = repo_relative
        if prefix:
            if not name.startswith(prefix):
                return None
            name = name[len(prefix) :]
        try:
            self._path(name)
        except ValueError:
            return None
        return name

    def git_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        # Porcelain paths are always relative to the repository root (not cwd).
        listing = self._git_command(
            ["status", "--porcelain", "-z", "--untracked-files=normal", "--", "."]
        )
        if listing["exit_code"] != 0 or listing.get("timed_out"):
            return listing
        if listing["truncated"]:
            raise ValueError("Git status output exceeded the tool output budget")
        prefix = self._git_workspace_prefix()
        lines: list[str] = []
        entries = listing["output"].split("\0")
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            # Porcelain -z: "XY path" (space after XY). Renames: next NUL record is the new path.
            if len(entry) < 3 or entry[2:3] != " ":
                continue
            status = entry[:2]
            path_text = entry[3:]
            repo_paths = [path_text]
            if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
                if index < len(entries) and entries[index]:
                    repo_paths.append(entries[index])
                    index += 1
            mapped: list[str] = []
            for name in repo_paths:
                workspace_name = self._workspace_git_path(name, prefix=prefix)
                if workspace_name is None:
                    mapped = []
                    break
                mapped.append(workspace_name)
            if not mapped:
                continue
            if len(mapped) == 1:
                lines.append(f"{status} {mapped[0]}")
            else:
                lines.append(f"{status} {mapped[0]} -> {mapped[1]}")
        return {
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
            "output": "\n".join(lines),
            "filtered": True,
        }

    def git_diff(self, arguments: dict[str, Any]) -> dict[str, Any]:
        options = [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--no-color",
            "--relative",
            "--no-renames",
        ]
        if arguments.get("staged", False):
            options.append("--cached")
        listing = self._git_command([*options, "--name-only", "-z", "--", "."])
        if listing["exit_code"] != 0:
            return listing
        if listing["truncated"]:
            raise ValueError("Too many changed paths for a bounded diff; inspect Git status")
        paths = []
        for name in listing["output"].split("\0"):
            if not name:
                continue
            try:
                self._path(name)
            except ValueError:
                continue
            paths.append(name)
        if not paths:
            return {"exit_code": 0, "output": "", "truncated": False, "timed_out": False}
        return self._git_command([*options, "--", *paths])

    def update_plan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        steps = arguments["steps"]
        if len(steps) > 20:
            raise ValueError("Plan may contain at most 20 steps")
        for step in steps:
            if not step["title"].strip() or len(step["title"]) > 200:
                raise ValueError("Plan titles must contain 1-200 characters")
            if step["status"] not in {"pending", "in_progress", "done", "blocked"}:
                raise ValueError("Invalid plan status")
        if sum(step["status"] == "in_progress" for step in steps) > 1:
            raise ValueError("Only one plan step may be in progress")
        self._plan = [dict(step) for step in steps]
        return self.get_plan({})

    def get_plan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"steps": [dict(step) for step in self._plan]}

    def get_current_time(self, arguments: dict[str, Any]) -> str:
        from datetime import datetime

        return datetime.now().astimezone().isoformat(timespec="seconds")

    def edit_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_edits:
            raise PermissionError("File edits require --allow-edits")
        path, data, text = self._text(arguments["path"])
        if hashlib.sha256(data).hexdigest() != arguments["expected_sha256"]:
            raise ValueError("File changed since it was read; read it again before editing")
        old_text = arguments["old_text"]
        if not old_text or text.count(old_text) != 1:
            raise ValueError("old_text must be non-empty and match exactly once")
        replacement = text.replace(old_text, arguments["new_text"], 1).encode("utf-8")
        if len(replacement) > MAX_FILE_BYTES or b"\0" in replacement:
            raise ValueError("Edited files must be text of at most 256 KiB")
        descriptor, temporary = tempfile.mkstemp(prefix="aie-edit-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(replacement)
                os.fchmod(target.fileno(), path.stat().st_mode & 0o777)
            _, current_data, _ = self._text(arguments["path"])
            if current_data != data:
                raise ValueError("File changed during editing; read it again")
            # Re-walk immediately before rename to shrink the symlink TOCTOU window.
            verified = self._path(arguments["path"])
            if verified != path or path.is_symlink():
                raise ValueError("Path changed during editing; read it again")
            if not verified.resolve().is_relative_to(self.root):
                raise ValueError("Path escapes the workspace")
            os.replace(temporary, verified)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {
            "path": path.relative_to(self.root).as_posix(),
            "sha256": hashlib.sha256(replacement).hexdigest(),
        }

    def create_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_edits:
            raise PermissionError("File creation requires --allow-edits")
        path = self._path(arguments["path"])
        data = arguments["content"].encode("utf-8")
        if len(data) > MAX_FILE_BYTES or b"\0" in data:
            raise ValueError("New files must be UTF-8 text of at most 256 KiB")
        with path.open("xb") as target:
            target.write(data)
        return {
            "path": path.relative_to(self.root).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def run_python_tests(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.allow_tests:
            raise PermissionError("Repository tests require --allow-tests")
        result = self._command(
            [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        )
        if result.get("exit_code") and "No module named pytest" in result.get("output", ""):
            result = dict(result)
            result["hint"] = "Install the test runner with: uv sync --all-extras"
        return result
