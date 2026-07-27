# Quickstart

Get a working `revenant` command and run your first coding task in under five minutes.

!!! note "Prerequisites"
    - **Python 3.11+** on your `PATH`.
    - **[Ollama](https://ollama.com) installed and running** (`ollama serve`).
    - At least one pulled model (this guide uses a small, tool-capable coder).

---

## 1. Install Revenant

=== "pip (PyPI)"

    ```bash
    pip install revenant-cli
    ```

    This pulls the three public packages — `nerva-core`, `nerva-agent`, and
    `revenant-cli` — and installs the `revenant` command.

=== "pipx (isolated)"

    ```bash
    pipx install revenant-cli
    ```

    `pipx` keeps Revenant and its dependencies in their own virtualenv, off your
    global site-packages.

Confirm the command is available:

```bash
revenant --help
```

## 2. Start Ollama and pull a model

Revenant talks to Ollama over its local HTTP API (default `http://localhost:11434`).

```bash
# In one terminal — leave it running:
ollama serve

# In another — pull a small, tool-capable coding model:
ollama pull qwen2.5-coder:7b
```

!!! tip "Which model?"
    A **tool-capable** model (one with a native tool template, like
    `qwen2.5-coder:7b`) gives the smoothest experience. Revenant also supports
    models *without* a tool template via a prompt-based fallback — see
    [Configure model routing](guides/model-routing.md).

## 3. Run your first task

Point Revenant at a repository and give it a goal. Start **read-only** so it can
only read and search — no writes, no shell commands:

```bash
cd /path/to/your/project
revenant --read-only --model qwen2.5-coder:7b "Where is authentication handled in this codebase?"
```

Revenant will search your files, read the relevant ones, and answer — printing
each tool call and observation as it goes.

!!! example "What you'll see"
    ```text
    ▸ grep(pattern="auth", ...)          → 12 matches across 4 files
    ▸ read(path="src/auth/session.py")   → 88 lines
    ── Answer ─────────────────────────────────────────────
    Authentication is handled in `src/auth/session.py` …
    ```

## 4. Let it make a change (with approval)

Drop `--read-only` to allow mutating tools. Every write, edit, or shell command
now pauses for your approval before it runs:

```bash
revenant --model qwen2.5-coder:7b "Add a docstring to the login() function in src/auth/session.py"
```

When Revenant proposes an edit, it shows you the diff and waits:

```text
▸ edit(path="src/auth/session.py")
  --- old ---
  def login(user, pw):
  --- new ---
  def login(user, pw):
      """Authenticate a user and return a session token."""
  Apply this change? [y/N]
```

!!! warning "You are the safety net"
    Nothing is written or executed until you approve it. Read every diff and
    command before saying yes. See
    [Approve, edit, and run commands safely](guides/approvals-and-safety.md).

---

## Next steps

- [Installation](installation.md) — Docker, standalone binary, and dev setups.
- [Configuration](configuration.md) — models, roles, and environment settings.
- [Run your first coding task](guides/first-task.md) — a fuller walkthrough.
