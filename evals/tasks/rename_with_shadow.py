"""Task: rename a symbol WITHOUT touching a same-named decoy (W0, ADR-0019).

`send` (the module-level helper in `notify.py`) must become `dispatch`, updated
at its two call sites in `worker.py` and `queue.py`. The trap: an UNRELATED
local method also named `send` exists on a class in `mailbox.py` -- it must NOT
be renamed. This scores *precision*, not just recall: a blind project-wide
find/replace of "send" breaks `mailbox.py`; a graph-aware rename (W4, which
resolves the actual definition and its real call sites) leaves the decoy alone.
The hidden test asserts the decoy still works under its original name.
"""
from __future__ import annotations

from pathlib import Path

from evals.tasks.base import ScoreResult, Task, run_pytest

_NOTIFY = '''\
def send(message):
    """The notification helper that must be renamed to dispatch."""
    return f"sent:{message}"
'''

_WORKER = '''\
from notify import send


def run(message):
    return send(message)
'''

_QUEUE = '''\
from notify import send


def flush(messages):
    return [send(m) for m in messages]
'''

_MAILBOX = '''\
class Mailbox:
    def __init__(self):
        self.items = []

    def send(self, item):
        # UNRELATED same-named method -- must stay `send`, not be renamed.
        self.items.append(item)
        return len(self.items)
'''

_TEST = '''\
from notify import dispatch
from worker import run
from queue_ import flush
from mailbox import Mailbox


def test_helper_renamed():
    assert dispatch("hi") == "sent:hi"
    assert run("yo") == "sent:yo"
    assert flush(["a", "b"]) == ["sent:a", "sent:b"]


def test_unrelated_method_untouched():
    mb = Mailbox()
    assert mb.send("x") == 1  # the decoy `send` must still exist and work
'''


def setup(workspace: Path) -> None:
    (workspace / "notify.py").write_text(_NOTIFY)
    (workspace / "worker.py").write_text(_WORKER)
    # Named queue_.py so the test import is unambiguous vs. the stdlib `queue`.
    (workspace / "queue_.py").write_text(_QUEUE)
    (workspace / "mailbox.py").write_text(_MAILBOX)
    (workspace / "test_rename_shadow.py").write_text(_TEST)


def score(workspace: Path) -> ScoreResult:
    try:
        proc = run_pytest(workspace, "test_rename_shadow.py")
    except Exception as exc:  # noqa: BLE001
        return ScoreResult.fail(f"scorer error: {exc!r}")
    if proc.returncode == 0:
        return ScoreResult.ok("send->dispatch renamed; unrelated Mailbox.send left intact")
    return ScoreResult.fail(
        "rename wrong (incomplete, absent, or clobbered the decoy):\n"
        + (proc.stdout or proc.stderr)[-1500:]
    )


TASK = Task(
    name="rename_with_shadow",
    goal=(
        "Rename the module-level helper function `send` in notify.py to "
        "`dispatch`, and update its call sites in worker.py and queue_.py. Do "
        "NOT rename the unrelated `send` method on the Mailbox class in "
        "mailbox.py -- that is a different symbol and must keep its name. Behavior "
        "is unchanged; only the notify helper is renamed."
    ),
    setup=setup,
    score=score,
    description="Precision rename: update the real symbol, leave a same-named decoy (W0/W4).",
)
