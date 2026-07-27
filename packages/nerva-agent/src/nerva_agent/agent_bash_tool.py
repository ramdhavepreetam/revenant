"""Shell tool for the Revenant coding agent (P3): run_bash.

This is an on-prem private development tool, so it does NOT censor development
commands — the agent can run compilers, test runners, git, package managers, etc.
The only restrictions are **damage guards** (not content filters): a small set of
catastrophic, near-always-a-mistake patterns are hard-denied even in auto-approve
(yolo) mode. Everything else goes through the loop's approval gate like any other
mutating tool.

The command runs with the workspace as its working directory, with a timeout, and
returns combined stdout/stderr plus the exit code so the model can react to failures.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from nerva_agent.agent_tools import Tool, ToolParam, ToolError

DEFAULT_TIMEOUT = 120  # seconds
MAX_OUTPUT_BYTES = 30_000


class BashBlocked(ToolError):
    """A command matched a hard-deny footgun pattern."""


# Catastrophic patterns that are (almost) never intentional in a dev loop. These
# are blocked unconditionally — the point is to stop an agent mistake from wiping
# a disk, not to police what the developer may do. Keep this list SHORT and
# high-precision; this is a guardrail, not a policy engine.
# Simple regex footguns (independent of rm's flag-order complexity).
_FOOTGUNS = [
    re.compile(r":\(\)\s*\{.*\|.*&.*\}"),                                 # fork bomb :(){ :|:& };:
    re.compile(r"\bmkfs\b"),                                             # format a filesystem
    re.compile(r"\bdd\b.*\bof=/dev/"),                                    # dd onto a device
    re.compile(r">\s*/dev/sd[a-z]"),                                     # redirect onto a disk
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"),                  # power control
    re.compile(r"\b(chmod|chown)\s+-[a-z]*R[a-z]*\s+.*\s+/(\s|$)"),      # recursive perms on /
]

# Recursive `rm`: has a recursive flag (-r/-R anywhere in a short-flag cluster,
# or --recursive), in any flag order (-rf, -fr, -Rf, -r -f all match).
_RM_RECURSIVE = re.compile(r"\brm\b[^|;&]*?(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b")
# A dangerous delete target: an argument that STARTS with an absolute path (/...),
# home (~ / $HOME), or a parent ref (..). Workspace-relative targets (./build,
# build, node_modules) do NOT match. `./x` is safe; `/x` and `../x` are not.
_DANGEROUS_TARGET = re.compile(
    r"(?:^|\s)"                       # start of an argument
    r"(?:"
    r"/"                             # absolute path: / or /anything
    r"|~"                            # home
    r"|\$HOME\b|\$\{HOME\}"          # $HOME / ${HOME}
    r"|\.\.(?:/|\s|$)"               # parent ref: .. or ../...
    r")"
)


def _is_recursive_rm_of_danger(command: str) -> bool:
    if not _RM_RECURSIVE.search(command):
        return False
    # Look at the argument portion after `rm` for a dangerous target.
    tail = command[command.find("rm") + 2:]
    return bool(_DANGEROUS_TARGET.search(tail))


def _check_footguns(command: str) -> None:
    if _is_recursive_rm_of_danger(command):
        raise BashBlocked(
            "command matches a hard-blocked destructive pattern and was refused "
            "for safety (this guard applies even in auto-approve mode). Use a "
            "narrower, workspace-scoped command."
        )
    for pattern in _FOOTGUNS:
        if pattern.search(command):
            raise BashBlocked(
                "command matches a hard-blocked destructive pattern and was refused "
                "for safety (this guard applies even in auto-approve mode). Use a "
                "narrower, workspace-scoped command."
            )


def _run_bash(root: Path, command: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    if not command or not command.strip():
        raise ToolError("empty command")
    _check_footguns(command)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(root),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"command timed out after {timeout}s") from None

    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_OUTPUT_BYTES:
        out = out[:MAX_OUTPUT_BYTES] + f"\n[... output truncated at {MAX_OUTPUT_BYTES} bytes ...]"
    return f"(exit {proc.returncode})\n{out}".rstrip()


def build_bash_tool(root: str | Path, timeout: int = DEFAULT_TIMEOUT) -> Tool:
    """Build the run_bash tool bound to `root` as its working directory."""
    root_path = Path(root).resolve()
    return Tool(
        "run_bash",
        "Run a shell command in the workspace directory and return its combined "
        "stdout/stderr and exit code. Use for builds, tests, git, and file ops.",
        [ToolParam("command", "string", "The shell command to run.")],
        run=lambda command: _run_bash(root_path, command, timeout),
        mutating=True,
    )
