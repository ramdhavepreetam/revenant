"""Per-workspace session persistence for `revenant resume` (F3, ADR-0007).

Compaction (F5) keeps a *live* run inside the model's window, but nothing
persists a run so it can be picked up later. This stores each run's full
transcript + metadata as one JSON file under `<ws>/.aibot/sessions/<id>.json`,
so `revenant resume` (a separate invocation) can re-hydrate it.

Why per-workspace JSON (not the shared ConversationStore SQLite DB): sessions
must travel with the repo and stay offline/repo-local, which a single shared
companion DB can't do. This mirrors the pattern `checkpoint.py` already uses
(`.aibot/checkpoints.json`). See ADR-0007.

Everything is best-effort: a failed write never breaks a run; a corrupt or
unknown-shaped record is skipped/repaired on load rather than crashing resume.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

_SESSIONS_DIRNAME = "sessions"


def _sessions_dir(workspace: Path) -> Path:
    return workspace / ".aibot" / _SESSIONS_DIRNAME


def _new_id() -> str:
    """A short, chronologically-sortable id: `<epoch-ms in base36-ish>`.

    Uses a zero-padded millisecond timestamp so lexical sort == time order and
    filenames stay collision-free within a run.
    """
    return f"{int(time.time() * 1000):013d}"


@dataclass
class Session:
    """One saved run. `messages` is the AgentResult transcript to re-hydrate."""

    id: str
    workspace: str
    model: str
    goal: str
    messages: list[dict] = field(default_factory=list)
    summary: str = ""
    turns_covered: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def meta(self) -> dict:
        """Lightweight metadata for `resume list` — no transcript."""
        return {
            "id": self.id, "goal": self.goal, "model": self.model,
            "turns_covered": self.turns_covered,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "message_count": len(self.messages),
        }


def save_session(
    workspace: Path,
    *,
    goal: str,
    model: str,
    messages: list[dict],
    session_id: str | None = None,
    summary: str = "",
    turns_covered: int = 0,
) -> str | None:
    """Persist a run's transcript. Returns the session id, or None on failure.

    Passing an existing `session_id` updates that session in place (used by the
    REPL to keep one session across turns); otherwise a new id is minted.
    """
    workspace = Path(workspace)
    sid = session_id or _new_id()
    now = time.time()
    existing = load_session(workspace, sid) if session_id else None
    session = Session(
        id=sid,
        workspace=str(workspace),
        model=model,
        goal=goal,
        messages=messages,
        summary=summary,
        turns_covered=turns_covered,
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    try:
        d = _sessions_dir(workspace)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.json").write_text(
            json.dumps(asdict(session), ensure_ascii=False), encoding="utf-8"
        )
        return sid
    except OSError:
        return None  # persistence is best-effort; the run itself is unaffected


def load_session(workspace: Path, session_id: str) -> Session | None:
    """Load one session by id, or None if missing/corrupt."""
    path = _sessions_dir(Path(workspace)) / f"{session_id}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or "id" not in raw:
        return None
    return _from_raw(raw)


def list_sessions(workspace: Path) -> list[dict]:
    """Metadata for all sessions in a workspace, newest-updated first.

    Corrupt files are silently skipped so one bad file can't hide the rest.
    """
    d = _sessions_dir(Path(workspace))
    if not d.is_dir():
        return []
    metas: list[dict] = []
    for path in d.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and "id" in raw:
            metas.append(_from_raw(raw).meta())
    metas.sort(key=lambda m: m.get("updated_at", 0.0), reverse=True)
    return metas


def latest_session_id(workspace: Path) -> str | None:
    """The id of the most-recently-updated session, or None if there are none."""
    metas = list_sessions(workspace)
    return metas[0]["id"] if metas else None


def _from_raw(raw: dict[str, Any]) -> Session:
    """Build a Session from a loaded dict, tolerating missing/extra keys."""
    msgs = raw.get("messages")
    return Session(
        id=str(raw["id"]),
        workspace=str(raw.get("workspace", "")),
        model=str(raw.get("model", "")),
        goal=str(raw.get("goal", "")),
        messages=msgs if isinstance(msgs, list) else [],
        summary=str(raw.get("summary", "")),
        turns_covered=int(raw.get("turns_covered", 0) or 0),
        created_at=float(raw.get("created_at", 0.0) or 0.0),
        updated_at=float(raw.get("updated_at", 0.0) or 0.0),
    )
