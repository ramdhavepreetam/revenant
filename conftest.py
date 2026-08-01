"""Root conftest — makes repo-root packages importable under bare `pytest`.

`tests/test_evals.py` imports the `evals` package, which lives at the repo root
and is NOT pip-installed (unlike the `packages/*` which are editable installs).

pytest adds the directory containing the topmost `conftest.py` to `sys.path`, so
this file's mere presence at the repo root makes `import evals` work no matter how
pytest is invoked — bare `pytest` (as CI runs it) or `python -m pytest`. Without
it, bare `pytest` doesn't put the repo root on the path and evals fails to import.
"""
