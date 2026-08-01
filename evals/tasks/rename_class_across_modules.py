"""Task: rename a class across modules, incl. subclassing + type usage (W0, ADR-0019).

`UserAccount` must become `Account` in its definition (`models.py`) AND at every
reference: a subclass base (`AdminAccount(UserAccount)` in `admin.py`), a
constructor call and isinstance check (`service.py`), and an import in a fourth
module (`serialize.py`). Class renames are harder than function renames for a
weak model because the name appears in varied syntactic positions (base class,
call, isinstance, annotation) -- exactly what a graph-driven rename (W4) handles
and a naive find-one-occurrence editor misses. The hidden test uses the new name
in all those positions.
"""
from __future__ import annotations

from pathlib import Path

from evals.tasks.base import ScoreResult, Task, run_pytest

_MODELS = '''\
class UserAccount:
    def __init__(self, name):
        self.name = name

    def label(self):
        return f"account:{self.name}"
'''

_ADMIN = '''\
from models import UserAccount


class AdminAccount(UserAccount):
    def label(self):
        return f"admin:{self.name}"
'''

_SERVICE = '''\
from models import UserAccount


def make_account(name):
    return UserAccount(name)


def is_account(obj):
    return isinstance(obj, UserAccount)
'''

_SERIALIZE = '''\
from models import UserAccount


def to_dict(acc: UserAccount):
    return {"name": acc.name}
'''

_TEST = '''\
from models import Account
from admin import AdminAccount
from service import make_account, is_account
from serialize import to_dict


def test_class_renamed():
    a = Account("ada")
    assert a.label() == "account:ada"


def test_subclass_and_usage():
    admin = AdminAccount("root")
    assert admin.label() == "admin:root"
    made = make_account("bob")
    assert is_account(made)
    assert to_dict(made) == {"name": "bob"}
'''


def setup(workspace: Path) -> None:
    (workspace / "models.py").write_text(_MODELS)
    (workspace / "admin.py").write_text(_ADMIN)
    (workspace / "service.py").write_text(_SERVICE)
    (workspace / "serialize.py").write_text(_SERIALIZE)
    (workspace / "test_rename_class.py").write_text(_TEST)


def score(workspace: Path) -> ScoreResult:
    try:
        proc = run_pytest(workspace, "test_rename_class.py")
    except Exception as exc:  # noqa: BLE001
        return ScoreResult.fail(f"scorer error: {exc!r}")
    if proc.returncode == 0:
        return ScoreResult.ok("UserAccount renamed to Account across all modules")
    return ScoreResult.fail(
        "class rename incomplete or absent:\n" + (proc.stdout or proc.stderr)[-1500:]
    )


TASK = Task(
    name="rename_class_across_modules",
    goal=(
        "Rename the class `UserAccount` to `Account` throughout this project: its "
        "definition in models.py and every reference -- the subclass base in "
        "admin.py, the constructor call and isinstance check in service.py, and "
        "the import and type annotation in serialize.py. Keep all behavior the "
        "same; only the class name changes."
    ),
    setup=setup,
    score=score,
    description="Project-wide class rename across 4 modules with varied usage (W0/W4).",
)
