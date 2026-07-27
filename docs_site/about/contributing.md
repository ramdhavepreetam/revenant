# Contributing

Contributions are welcome. This page covers the dev setup, project layout, and
the workflow for changes.

!!! note "Prerequisites"
    - **Python 3.11+**
    - **[Ollama](https://ollama.com)** running with at least one model pulled.
    - Access to the repository.

---

## 1. Set up a dev environment

```bash
git clone git@github.com:ramdhavepreetam/revenant.git
cd revenant
make dev     # editable-install the packages in dependency order
make test    # run the test suite
```

!!! warning "Run from the repo root"
    Revenant's data directory resolves relative to the current directory. Run
    `make` targets and the CLI from the repo root.

## 2. Understand the layout

Revenant is a monorepo of pip packages:

| Package | What lives here |
|---------|-----------------|
| `packages/nerva-core` | LLM layer, SQLite storage, profiles, memory. |
| `packages/nerva-agent` | The agent engine: loop, tools, protocol, routing. |
| `packages/revenant-cli` | The `revenant` command. |

The dependency graph is acyclic: `nerva-core ← nerva-agent ← revenant-cli`.
Keep it that way — lower packages must not import higher ones.

## 3. Make your change

1. Branch from the default branch.
2. Make the change, matching the style of the surrounding code.
3. Add or update tests.
4. Run the suite:

   ```bash
   make test
   ```

5. If you touched docs, build them:

   ```bash
   mkdocs build -f mkdocs.material.yml --strict
   ```

!!! tip "Match the code you touch"
    Follow the naming, comment density, and idioms of the file you're editing
    rather than introducing a new style.

## 4. Open a pull request

- Keep PRs focused and describe the change and its motivation.
- Ensure `make test` passes and the docs build is clean.
- Note any user-facing change so it can be added to the
  [Changelog](changelog.md).

## Coding guidelines

| Guideline | Why |
|-----------|-----|
| Keep the dependency graph acyclic | Front-ends stay thin; the engine stays reusable. |
| `nerva-core` is stdlib-only | It's the shared foundation; avoid heavy deps. |
| Gate mutating tools behind approval | The safety model depends on it. |
| Don't weaken the destructive-command block list | It's a backstop against catastrophic commands. |

---

## Next steps

- [Architecture](../architecture.md) — how the pieces fit.
- [Deployment](../deployment.md) — release and packaging.
- [Changelog](changelog.md)
