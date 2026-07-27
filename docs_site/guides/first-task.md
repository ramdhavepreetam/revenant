# Run your first coding task

A fuller walkthrough of a real Revenant session: exploring a codebase, then making
an approved change.

!!! note "Prerequisites"
    - Revenant [installed](../installation.md).
    - Ollama running with a coding model pulled (`ollama pull qwen2.5-coder:7b`).
    - A project directory to work in.

---

## 1. Move into your project

Revenant reads the workspace relative to where you run it. `cd` into the repo:

```bash
cd /path/to/your/project
```

## 2. Explore, read-only

Start safe. `--read-only` disables every mutating tool, so Revenant can only
read, list, glob, and grep:

```bash
revenant --read-only --model qwen2.5-coder:7b \
  "Explain how configuration is loaded in this project."
```

Revenant runs a loop — search, read, reason — and prints each step:

```text
▸ grep(pattern="config", ...)         → 23 matches
▸ read(path="app/settings.py")        → 64 lines
▸ read(path="app/__init__.py")        → 30 lines
── Answer ───────────────────────────────────────────────
Configuration is loaded in `app/settings.py`, which reads …
```

## 3. Ask for a change

Drop `--read-only` to allow writes and edits. Mutations pause for approval:

```bash
revenant --model qwen2.5-coder:7b \
  "Add type hints to the load_config() function in app/settings.py."
```

## 4. Review the proposed edit

Revenant shows the diff and waits for you:

```text
▸ edit(path="app/settings.py")
  --- old ---
  def load_config(path):
  --- new ---
  def load_config(path: str) -> dict:
  Apply this change? [y/N]
```

- Type `y` to apply.
- Type `N` (or Enter) to reject; Revenant continues without the change.

!!! warning "Read before you approve"
    You are the only thing standing between the model and your files. Read each
    diff and command. See [Approvals & safety](approvals-and-safety.md).

## 5. Let it verify

Ask Revenant to run your tests. The command is approval-gated too:

```bash
revenant --model qwen2.5-coder:7b \
  "Run the test suite and fix any failures you introduced."
```

```text
▸ bash(command="pytest -q")
  Run this command? [y/N]
```

Approve it, and Revenant reads the output, iterates, and reports back.

---

## Tips

!!! tip "Scope the goal"
    Narrow, concrete goals ("add a docstring to `login()`") produce better
    results than broad ones ("improve the code"). Revenant works step by step.

!!! tip "Cap the work"
    Use `--max-steps N` to bound how long a run can go before it must produce a
    final answer.

---

## Next steps

- [Approve, edit, and run commands safely](approvals-and-safety.md)
- [Configure model routing](model-routing.md)
- [CLI reference](../reference/cli.md)
