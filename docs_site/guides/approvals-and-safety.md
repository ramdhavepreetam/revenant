# Approve, edit, and run commands safely

Revenant separates tools into **read-only** and **mutating**. Read-only tools run
freely; mutating tools are gated behind your explicit approval. This guide
explains the safety model and how to control it.

!!! note "Prerequisites"
    Revenant [installed](../installation.md) and a model available.

---

## The two tool classes

| Class | Tools | Behavior |
|-------|-------|----------|
| **Read-only** | `read`, `list`, `glob`, `grep` | Run without prompting. Cannot change anything. |
| **Mutating** | `write`, `edit`, `bash` | Prompt for approval before every call. |

All tools are **path-confined to the workspace** — Revenant cannot read or write
outside the directory you point it at.

## 1. Approve changes one at a time (default)

By default, each mutating call pauses and shows you exactly what it will do:

```text
▸ write(path="src/util.py")
  --- content ---
  def slugify(s): ...
  Apply this change? [y/N]
```

```text
▸ bash(command="npm run build")
  Run this command? [y/N]
```

Type `y` to proceed; anything else rejects the action and the loop continues.

## 2. Explore with zero mutation risk

To guarantee nothing can change, run **read-only**. Mutating tools are removed
entirely — there is nothing to approve because they cannot be called:

```bash
revenant --read-only "Audit this repo for hardcoded secrets and list them."
```

See [Work in read-only mode](read-only.md) for more.

## 3. Skip approvals (use with care)

`--yolo` auto-approves mutating tools. Use it only in throwaway workspaces (a
scratch clone, a container) where you accept unattended changes:

```bash
revenant --yolo "Reformat all Python files with black."
```

!!! danger "Yolo means unattended writes and shell commands"
    In `--yolo`, Revenant writes files and runs commands **without asking**. Only
    use it on disposable copies of your code, never on work you can't afford to
    lose.

## Destructive commands are always blocked

Even in `--yolo`, Revenant **hard-blocks** a set of destructive shell footguns.
These are refused regardless of approval mode:

| Blocked pattern | Example |
|-----------------|---------|
| Recursive root delete | `rm -rf /` |
| Fork bombs | `:(){ :|:& };:` |
| Filesystem creation / wipe | `mkfs …`, `dd of=/dev/sda` |

!!! warning "The block list is a backstop, not a substitute for review"
    It catches catastrophic patterns only. Ordinary damaging commands (dropping a
    database, force-pushing) are **not** on it — that's why approval mode exists.
    Prefer approving each command over `--yolo`.

---

## Recommended workflow

1. **Explore read-only** first to understand the change.
2. **Approve each mutation** when you switch to a working run.
3. Reserve **`--yolo`** for disposable environments only.

---

## Next steps

- [Work in read-only mode](read-only.md)
- [CLI reference](../reference/cli.md) — the `--read-only` and `--yolo` flags.
- [Tools reference](../reference/tools.md) — every tool and its class.
