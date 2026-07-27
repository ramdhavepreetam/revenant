# Changelog

All notable changes to Revenant are documented here. This project follows
[Semantic Versioning](https://semver.org/).

---

## [0.1.0] — Initial release

The first public release of Revenant.

### Added

- **`revenant` CLI** — a local, offline coding agent that runs a tool-using loop
  against your own Ollama models.
- **Agent loop** with read-only tools (`read`, `list`, `glob`, `grep`) and
  approval-gated mutating tools (`write`, `edit`, `bash`).
- **Dual tool-call protocol** — native `tool_calls` plus a prompt-based `action`
  fallback, auto-detected and cached per model.
- **Model routing** by role (`code`, `language`, `router`, `fallback`) via a
  heuristic + tiny classifier.
- **Context compaction** — folds old steps into a recap when over the context
  budget.
- **Hardware-aware tuning** — `max_steps` and context budget derived from RAM.
- **Safety** — path-confined workspace and a destructive-command block list
  active even in `--yolo`.
- **Distribution** — published to PyPI (`nerva-core`, `nerva-agent`,
  `revenant-cli`); macOS `.dmg` and Windows `.exe` installers via CI on `v*`
  tags.

!!! note "Immutable releases"
    PyPI versions are permanent. Fixes ship as a new version.

---

[0.1.0]: https://github.com/ramdhavepreetam/revenant/releases/tag/v0.1.0
