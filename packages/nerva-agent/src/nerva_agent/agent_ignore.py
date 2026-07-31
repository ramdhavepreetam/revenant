"""Ignore-file matching for the workspace file tools (F7).

The fs tools (glob/grep/list_dir) previously hardcoded a tiny noise list
(node_modules, __pycache__, site). That let build/vendor dirs drown the model's
context and, worse, get indexed for knowledge-doc recall (F6). This module reads
`.revenantignore` and `.gitignore` from the workspace root and builds one matcher
the tools consult when walking the tree.

Scope is deliberately small and predictable — a useful subset of gitignore syntax,
not a full reimplementation:

  - blank lines and `# comments` are skipped
  - a trailing `/` marks a directory-only pattern
  - a leading `/` anchors the pattern to the workspace root
  - `*`/`?`/`[...]` glob metacharacters work via fnmatch
  - a leading `!` negates (un-ignores) a previously matched path
  - a bare name (no slash) matches at any depth (like gitignore)

Patterns are matched against workspace-relative POSIX paths. Everything is
best-effort: a missing or unreadable ignore file simply contributes nothing.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

# Always-ignored directories, even with no ignore file present. These are pure
# noise for a coding agent and were the old hardcoded list.
DEFAULT_IGNORES = (".git/", "node_modules/", "__pycache__/", ".venv/", "venv/")

IGNORE_FILENAMES = (".revenantignore", ".gitignore")


@dataclass
class _Rule:
    pattern: str          # normalized, without leading '!' or trailing '/'
    dir_only: bool        # pattern ended with '/'
    anchored: bool        # pattern began with '/' (root-relative)
    negated: bool         # pattern began with '!'


@dataclass
class IgnoreMatcher:
    """Matches workspace-relative paths against collected ignore rules.

    Rules are applied in order; the last matching rule wins, so a later `!name`
    can un-ignore something an earlier rule excluded (gitignore semantics).
    """

    rules: list[_Rule] = field(default_factory=list)

    def match(self, rel_path: str, is_dir: bool) -> bool:
        """True if `rel_path` (workspace-relative, POSIX) should be ignored."""
        rel_path = rel_path.replace("\\", "/").strip("/")
        if not rel_path:
            return False
        ignored = False
        for rule in self.rules:
            if _rule_matches(rule, rel_path, is_dir):
                ignored = not rule.negated
        return ignored


def _rule_matches(rule: _Rule, rel_path: str, is_dir: bool) -> bool:
    pat = rule.pattern
    segments = rel_path.split("/")
    if rule.dir_only:
        # A dir-only rule (`build/`) ignores the directory itself AND everything
        # under it. So it matches when the path IS the dir, or has an ancestor
        # segment matching the pattern. A file at the leaf only matches if it's a
        # directory (is_dir); a file under an ignored dir matches via its ancestors.
        parents = segments[:-1]
        if any(fnmatch.fnmatch(seg, pat) for seg in parents):
            return True
        if is_dir and _leaf_or_path_matches(rule, rel_path, segments):
            return True
        return False
    return _leaf_or_path_matches(rule, rel_path, segments)


def _leaf_or_path_matches(rule: _Rule, rel_path: str, segments: list[str]) -> bool:
    pat = rule.pattern
    if rule.anchored:
        # Root-anchored: match the full path, or ignore a whole subtree under it.
        return fnmatch.fnmatch(rel_path, pat) or rel_path.startswith(pat + "/")
    # Unanchored: match the full path, or any single path segment — so a bare
    # `build` ignores `a/b/build` and `*.log` ignores `logs/app.log`.
    if fnmatch.fnmatch(rel_path, pat):
        return True
    return any(fnmatch.fnmatch(seg, pat) for seg in segments)


def _parse_line(line: str) -> _Rule | None:
    raw = line.rstrip("\n")
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        return None
    negated = stripped.startswith("!")
    if negated:
        stripped = stripped[1:]
    anchored = stripped.startswith("/")
    if anchored:
        stripped = stripped[1:]
    dir_only = stripped.endswith("/")
    if dir_only:
        stripped = stripped.rstrip("/")
    if not stripped:
        return None
    return _Rule(pattern=stripped, dir_only=dir_only, anchored=anchored, negated=negated)


def load_ignore_matcher(root: str | Path) -> IgnoreMatcher:
    """Build an IgnoreMatcher from DEFAULT_IGNORES + any ignore files at `root`."""
    root_path = Path(root)
    rules: list[_Rule] = []
    for pat in DEFAULT_IGNORES:
        rule = _parse_line(pat)
        if rule:
            rules.append(rule)
    for name in IGNORE_FILENAMES:
        f = root_path / name
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, FileNotFoundError):
            continue
        for line in text.splitlines():
            rule = _parse_line(line)
            if rule:
                rules.append(rule)
    return IgnoreMatcher(rules)
