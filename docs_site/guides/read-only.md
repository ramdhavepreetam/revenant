# Work in read-only mode

Read-only mode turns Revenant into a safe code-exploration and audit tool: it can
read, search, and reason about your codebase, but **cannot** write files or run
shell commands.

!!! note "Prerequisites"
    Revenant [installed](../installation.md) and a model available.

---

## Turn it on

Add `--read-only` to any run:

```bash
revenant --read-only "How does request routing work in this app?"
```

In this mode the mutating tools (`write`, `edit`, `bash`) are not registered at
all — so there is nothing to approve and no way for the agent to change anything.

| Available | Removed |
|-----------|---------|
| `read`, `list`, `glob`, `grep` | `write`, `edit`, `bash` |

## Good uses for read-only

!!! example "Onboarding to a new codebase"
    ```bash
    revenant --read-only "Give me a tour: entry points, main modules, and how they connect."
    ```

!!! example "Auditing"
    ```bash
    revenant --read-only "Find TODO/FIXME comments and summarize what's outstanding."
    ```

!!! example "Answering a targeted question"
    ```bash
    revenant --read-only "Which function validates the JWT, and where is it called?"
    ```

## Combine with a workspace boundary

Point `--workspace` at exactly the directory you want examined. Revenant is
path-confined to it — it cannot read outside:

```bash
revenant --read-only --workspace ./services/api "Document the public endpoints."
```

!!! tip "Safe by default for exploration"
    When you just want answers about code — not changes — reach for
    `--read-only`. It removes the entire class of "did the agent touch
    something?" worries.

---

## Next steps

- [Approve, edit, and run commands safely](approvals-and-safety.md) — when you're
  ready to allow changes.
- [Run your first coding task](first-task.md)
- [CLI reference](../reference/cli.md)
