# Revenant + AIBot — Handover

_Last updated: 2026-07-27. This is the single-page ground truth for the project: what it is,
where everything lives, what's done, and what's left. Written to hand off to another developer
(or a future session) with zero prior context._

---

## 1. What this project is

Two offline, on-prem programs built on a **shared local-LLM core**, talking to local models via
[Ollama](https://ollama.com). No cloud, no telemetry, no API keys.

- **Revenant** — a local **coding-agent CLI** (Claude-Code-style tool-calling loop). Reads/searches
  code, and with approval edits files and runs shell commands. **Public** (on PyPI).
- **AIBot** — a **companion web app** (backend API + static UI + local TTS) with layered memory.
  **Private** (never published).

The design goal was "Claude Code, but for my private Ollama LLM," generalized so one agent loop
powers both a coding assistant and the companion.

---

## 2. Current status (what's live)

| Deliverable | Status |
|---|---|
| Monorepo code | ✅ pushed → **private** repo `github.com/ramdhavepreetam/revenant` (branch `master`) |
| PyPI — `pip install revenant-cli` | ✅ **LIVE** (v0.1.0): [nerva-core](https://pypi.org/project/nerva-core/), [nerva-agent](https://pypi.org/project/nerva-agent/), [revenant-cli](https://pypi.org/project/revenant-cli/) |
| macOS installer (`.dmg`) | ✅ built + verified locally (`dist_installers/Revenant-0.1.0-macos-arm64.dmg`) |
| Windows installer | ✅ CI workflow ready — fires on a `v*` tag (not yet run) |
| Public docs site | ⏳ **pipeline built + CI-verified; needs a deploy token to go live** (see §7) |
| Tests | ✅ **158 pass** |
| Docs build | ✅ `mkdocs build --strict` clean |

**Private/public boundary is proven:** installing the public wheels into a clean venv gives a
working `revenant` command, and `import aibot_app` raises `ModuleNotFoundError` — the companion
code cannot leak into a public install.

---

## 3. Repository layout (monorepo of 4 pip packages)

```
revenant/  (private git repo; local folder is still named AIBot)
├── packages/
│   ├── nerva-core/     PUBLIC  shared: LLM layer + SQLite storage + profiles + memory (stdlib-only)
│   │                           modules: local_llm_writer, aibot_storage, aibot_profiles, aibot_memory
│   ├── nerva-agent/    PUBLIC  the agent ENGINE (depends on nerva-core)
│   │                           agent_loop, agent_tools, agent_protocol, agent_fs/edit/bash_tools,
│   │                           agent_router, agent_capacity, agent_native_tools, agent_companion_tools
│   ├── revenant-cli/   PUBLIC  the `revenant` command (cli.py); deps: nerva-core, nerva-agent
│   └── aibot-app/      PRIVATE web_app + backend_app + ui_app + tts_* + the 5 companion modules
│                               (aibot_personal_memory, aibot_companion_memory,
│                                aibot_companion_compiler, aibot_context, aibot_summary)
├── frontend/           AIBot React/Vite UI (builds into web/)
├── web/                static UI assets served by aibot-ui
├── docs/  mkdocs.yml   full docs (local); mkdocs.public.yml = Revenant-only public build
├── packaging/          revenant.spec (PyInstaller), revenant.iss (Inno Setup), build-public-docs.sh, INSTALL-macos.txt
├── .github/workflows/  build-installers.yml, docs-public.yml
├── tests/              158 tests (pytest); pytest.ini
├── Makefile            make dev / test / docs
├── requirements-docs.txt
├── .aibot/             RUNTIME DATA (gitignored) — conversations.sqlite3, profiles.json, models venvs
└── README.md
```

Dependency DAG (acyclic): `nerva-core ← nerva-agent ← {revenant-cli, aibot-app}`.
The `aibot_*` module *names* were intentionally kept (not renamed) to limit churn.

---

## 4. Getting started (a new dev, from scratch)

```bash
git clone git@github.com:ramdhavepreetam/revenant.git    # private
cd revenant
make dev        # pip install -e each of the 4 packages in dependency order
make test       # 158 tests
make docs       # mkdocs build --strict
```

Requires **Python 3.11+** and **Ollama running** with models pulled (see §6). Console scripts
after `make dev`: `revenant`, `aibot-backend`, `aibot-ui`.

Run the CLI: `revenant "summarize what packages/nerva-agent does"`
Run the app:  `aibot-backend` (API :8766) + `aibot-ui` (UI :8765) in two terminals.

> **Path note:** `default_data_dir()` returns `Path(".aibot")` — CWD-relative. Run commands from
> the repo root so `.aibot/` and `web/` resolve. (Fixed during the monorepo split; see §9.)

---

## 5. The Revenant agent — how it works

One loop, `nerva_agent.agent_loop.AgentLoop.run(goal)`:
`build system+messages → call model → parse action → (approve if mutating) → dispatch tool →
feed observation back → repeat until final answer / max_steps`.

- **Dual tool-call protocol** (`agent_protocol.parse_action`): native `tool_calls` for models with
  a tool template (qwen2.5); a prompt-based ` ```action {json} ``` ` fallback for models without one
  (Stheno, and the abliterated coder — see §6). Handles sloppy 8B output (single quotes, mixed
  quotes via `ast.literal_eval`, `arguments`/`args` aliases, double-wrapping).
- **Tools** (`agent_*_tools`): read/list/glob/grep (read-only); write/edit/bash (mutating,
  approval-gated). All path-confined to the workspace. `run_bash` hard-blocks destructive footguns
  (`rm -rf /`, fork bombs, `mkfs`, …) even in `--yolo`.
- **Context compaction** (`_compact_messages`): folds oldest steps into a recap when over
  `max_context_tokens`, keeping system+goal+recent verbatim. Local analog of compaction.
- **Two front-ends, same loop:** the CLI (`revenant_cli.cli`, coding registry, `role=code`) and the
  companion (`aibot_app.web_app.handle_agent_turn` at `POST /api/agent`, memory/reminder registry,
  `role=companion`). The companion's `memory_save` gates `boundary` writes to `pending` (human review).

Model routing (`agent_router` + `profiles.json` `model_roles`) auto-picks a model per turn via a
cheap heuristic + a tiny classifier. Hardware tuning (`agent_capacity`) sets `max_steps`/context
budget from RAM. Native-tool support is auto-detected per model (`agent_native_tools`, probe-once).

---

## 6. Models (Ollama)

Current `model_roles` (in `nerva_core.aibot_profiles.DEFAULT_PROFILES`; overridable in
`.aibot/profiles.json`):

| Role | Model | Notes |
|---|---|---|
| `code` | `huihui_ai/qwen2.5-coder-abliterate:14b` | code-specialized, uncensored. **No native tool template → uses prompt-based protocol** (auto-detected). |
| `language` | `qwen2.5:14b` | discussion/reasoning |
| `companion` | Stheno-8B GGUF | persona/roleplay (private app) |
| `summary` | `gemma:latest` | rolling summaries |
| `router` | `qwen2.5:7b` | tiny fast classifier (has native tools) |
| `fallback` | → language | when classification fails |

Pull what you need: `ollama pull qwen2.5-coder:7b` (a tool-capable coder is a fine default for the
public CLI). The published CLI defaults to whatever the user's `profiles.json` / `--model` says.

---

## 7. Publishing & distribution

### PyPI (DONE — but immutable)
Wheels/sdists are in `dist_pypi/` (gitignored). v0.1.0 is **already published and permanent** —
you **cannot** overwrite it. To release a fix: bump `version` in the 3 public `pyproject.toml`
files, rebuild, re-upload:
```bash
for p in nerva-core nerva-agent revenant-cli; do python -m build packages/$p --outdir dist_pypi; done
python -m twine upload dist_pypi/nerva_core-<v>* dist_pypi/nerva_agent-<v>* dist_pypi/revenant_cli-<v>*
```
Credentials come from `~/.pypirc` (`[pypi]` section). `aibot-app` has a `Private :: Do Not Upload`
classifier so twine refuses it.

### Installers (Mac done locally; Windows via CI)
- Spec: `packaging/revenant.spec` (PyInstaller). **Build in an isolated venv** (the system Python's
  matplotlib/numpy breaks PyInstaller). Produces a ~8 MB standalone binary.
- macOS `.dmg` built locally (`dist_installers/`). Unsigned → Gatekeeper workaround in
  `docs/install.md` (`xattr -d com.apple.quarantine`).
- **CI:** push a `v*` tag → `.github/workflows/build-installers.yml` builds macOS `.dmg` + Windows
  Inno-Setup `.exe` (`packaging/revenant.iss`) and attaches them to a GitHub Release:
  ```bash
  git tag v0.1.0 && git push origin v0.1.0
  ```

### Public docs site (NEEDS ONE ACTION — deploy token)
GitHub Pages can't run on the private repo (free plan). Docs publish to the **public** repo
`github.com/ramdhavepreetam/revenant-docs`. Pipeline is built and **CI-verified** (build + companion-
filter pass); it only fails at the push step because the secret is missing.

**To make docs live:**
1. Create a **fine-grained PAT**: scope to **only `revenant-docs`**, permission **Contents: Read and write**.
2. Add it to the *revenant* repo as secret `DOCS_DEPLOY_TOKEN`:
   `gh secret set DOCS_DEPLOY_TOKEN --repo ramdhavepreetam/revenant`
3. Run: `gh workflow run docs-public.yml --repo ramdhavepreetam/revenant`
4. Enable Pages on the public repo:
   `gh api -X POST repos/ramdhavepreetam/revenant-docs/pages -f 'source[branch]=gh-pages'`
→ Live at `https://ramdhavepreetam.github.io/revenant-docs/`.

`packaging/build-public-docs.sh` produces the companion-free site (drops companion pages + strips
links + **hard-fails if any `aibot_app.*` code path leaks into the HTML**). `mkdocs.public.yml` is
the Revenant-only nav.

---

## 8. Documentation map

- **User:** `docs/install.md`, `docs/revenant-cli.md`, `docs/model-routing.md`, `docs/architecture.md`, `docs/index.md`.
- **Design records:** `docs/adr/0001-0003`, `docs/agent-harness-plan.md`, `docs/companion-harness-plan.md`, `docs/knowledge-base.md` (companion — private only).
- **Companion (private):** `docs/companion-agent.md`.
- **API reference:** `docs/api/*.md` (mkdocstrings autodoc from docstrings).
- **This file:** `HANDOVER.md`.

---

## 9. Gotchas / things a new dev must know

1. **Immutable PyPI:** v0.1.0 is published forever. Bump versions for any change.
2. **Build installers in an isolated venv** — system-Python site-packages (matplotlib/numpy) break PyInstaller.
3. **CWD-relative data dir:** run from repo root (`.aibot/` and `web/` are found via `Path.cwd()`).
4. **Coder model has no native tools:** the abliterated GGUF uses the prompt-based protocol; that's
   expected and works. Native detection is cached per model (`agent_native_tools.clear_cache()` after a re-pull).
5. **Companion boundary safety:** `memory_save(category="boundary")` stays `pending` (human-gated) —
   don't "fix" it to auto-activate.
6. **`aibot_*` names kept on purpose** — a future rename to drop the prefix is optional cleanup.
7. **egg-info / dist_* / site* dirs are gitignored** build artifacts — don't commit them.

---

## 10. Outstanding / next steps (owner)

- [ ] **Docs site:** add `DOCS_DEPLOY_TOKEN`, run `docs-public.yml`, enable Pages (§7).
- [ ] **Installers release:** push a `v0.1.0` tag to build the Mac+Windows artifacts (§7).
- [ ] Optional: Apple Developer ID → sign/notarize the macOS binary (removes Gatekeeper warning).
- [ ] Optional: README deep-cleanup — the lower half still has stale `python3 local_llm_writer.py` CLI examples (that module is now in `nerva_core`, not a root script).
- [ ] Optional: rename `aibot_*` modules to drop the prefix (cosmetic).

---

## 11. Key commands cheat-sheet

```bash
make dev / make test / make docs         # dev install / 158 tests / strict docs build
revenant "..."                           # run the coding agent (needs Ollama)
aibot-backend / aibot-ui                 # run the companion app (:8766 / :8765)
bash packaging/build-public-docs.sh      # build the companion-free public docs -> site_public/
pyinstaller packaging/revenant.spec      # build the standalone binary (in an isolated venv)
git tag vX.Y.Z && git push origin vX.Y.Z # trigger the installer-build CI + release
```
