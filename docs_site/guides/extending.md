# Extending Revenant

Beyond one-shot `run` and interactive `chat`, Revenant ships four capabilities
that make it a platform: **MCP** (external tools), **skills** (reusable
workflows), **loops** (autonomous runs), and a **code graph** (repo-scale
reasoning). All stay fully offline.

---

## MCP servers

The [Model Context Protocol](https://modelcontextprotocol.io) lets Revenant call
tools it didn't ship with — git, a database, a browser — served by a local
process. Configure servers in `.revenant.toml`:

```toml
[[mcp.servers]]
name = "git"
transport = "stdio"
command = "mcp-server-git"
args = ["--repo", "."]
read_only = ["status", "log"]   # tools that skip the approval gate
```

Each remote tool joins the agent's registry as `<name>.<tool>` (e.g.
`git.status`). Remote tools are approval-gated by default; list `read_only` tools
to relax that. Inspect what's wired up:

```bash
revenant mcp list          # servers + their tools
revenant mcp test git      # connect and report health
```

Only the **stdio** transport (a local subprocess) is supported today, keeping the
offline guarantee intact. A server that fails to connect is skipped with a
warning — it never blocks a run.

---

## Skills

A skill is a named, reusable procedure — "run-tests", "write-migration",
"review-diff" — stored as a `SKILL.md` file with TOML frontmatter:

```markdown title=".revenant/skills/run-tests/SKILL.md"
+++
name = "run-tests"
description = "Run the suite and summarize failures."
tools = ["run_bash", "read_file"]   # optional: scope the agent's tools
+++
1. Run `pytest -q`.
2. If it fails, read the failing file and propose a minimal fix.
```

**Progressive disclosure** keeps context small: only each skill's one-line
`description` is loaded until you invoke it, at which point its full body loads
and (if declared) the agent's tools are scoped to just that skill's list.

```bash
revenant skills list             # discovered skills
revenant skills show run-tests   # a skill's full body
```

Inside `chat`, run one with `/skill run-tests`, or one-shot from the shell:

```bash
revenant run --skill run-tests               # run the skill's procedure
revenant run --skill review-diff "the auth changes"   # skill + extra context
```

Skills are discovered from the project (`.revenant/skills/`) and your user config
(`~/.config/revenant/skills/`); project skills win on a name clash.

---

## Autonomous loops

`revenant loop` runs a goal **unattended**, iterating until a success condition
is met or a budget is spent. It is always bounded — there is no run-forever mode.

```bash
# Keep working until the tests pass, checkpointing before each iteration.
revenant loop --autonomous --until-tests "make the failing tests pass"
```

Success conditions:

| Flag | Meaning |
|------|---------|
| `--until-tests` | The test command (`--test-cmd`, default `pytest -q`) exits `0`. |
| `--until "CMD"` | An arbitrary shell command exits `0`. |
| `--until-file PATH` | `PATH` exists. |
| *(none)* | The agent declares completion (still budget-bounded). |

Safety:

- `--autonomous` auto-approves edits **only within the budget** and takes an
  [undo](../reference/cli.md#undo) checkpoint before each iteration, so you can
  step back a whole round.
- `--dry-run` previews a run with **zero disk writes** (it forces read-only).
- `--max-iterations` / `--max-wall` bound every run.

Each loop is saved as a session — a stopped loop prints a `revenant resume <id>`
hint so you can pick it up.

Add `--watch '<glob>'` to re-run the whole loop whenever a matching workspace
file changes (an mtime poll that respects your ignore globs):

```bash
revenant loop --watch 'src/**/*.py' --until-tests "keep the tests green"
```

---

## The code graph

Grep finds text; a graph finds **meaning**. Revenant indexes your workspace into
a symbol/dependency graph (accurate for Python via the stdlib `ast`; a regex
fallback for other languages) and gives the agent structural tools:

| Tool | Answers |
|------|---------|
| `defn_of(symbol)` | Where is this defined? |
| `who_calls(symbol)` | What calls this? |
| `neighbors(path)` | What does this file import / who imports it? |
| `impact_of(symbol)` | What's the blast radius of changing this? |

The graph is built automatically at the start of a run (respecting your ignore
globs). On a very large repo, skip it with `--no-graph`.

---

## Verify → repair (catch broken edits)

A local model will sometimes write code that *looks* right but doesn't compile or
fails the tests. With `[verify]` enabled, Revenant checks every edit and feeds any
failure straight back to the model to fix — so broken code is caught and repaired
inside the run, not shipped to you:

```toml
[verify]
enabled = true
commands = ["pytest -q"]
max_repair_attempts = 3
```

On each edit the harness byte-compiles changed Python and runs your configured
checks; a failure is fed back with the exact error, and the model repairs it. If
it still can't pass after `max_repair_attempts`, the edit is reverted (via undo)
and the model is told to stop. See [configuration](../reference/config.md#verify-repair).

## Undo — always reversible

Every mutating run is reversible with [`revenant undo`](../reference/cli.md#undo).
In a **git** workspace, undo restores the whole working tree from a private
shadow-commit — including files a `run_bash` command created — without touching
your branches. In a non-git workspace it restores the files `write`/`edit`
touched. This is what makes autonomous loops safe to run.

---

## Next steps

- [CLI reference](../reference/cli.md)
- [Tools reference](../reference/tools.md)
- [Configuration reference](../reference/config.md)
