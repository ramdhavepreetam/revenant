# Installing Revenant

Revenant is the local coding-agent CLI. It runs entirely on your machine against
[Ollama](https://ollama.com) — **no cloud, no API keys**. Three ways to install it.

!!! note "Ollama is required at runtime"
    However you install Revenant, it needs **Ollama running** with a model pulled — the
    LLM is not bundled. A tool-capable coder model is recommended:
    ```bash
    ollama pull qwen2.5-coder:7b
    ```

## 1. pip (any OS with Python 3.11+)

```bash
pip install revenant-cli
```

This installs the **`revenant`** command (the PyPI package is `revenant-cli`; the command is
`revenant`). It pulls in `nerva-core` and `nerva-agent` automatically — all pure standard
library, no heavy dependencies.

```bash
revenant "summarize what this repo does"
```

## 2. macOS — standalone installer (.dmg)

Download `Revenant-<version>-macos-arm64.dmg` from the
[Releases](https://github.com/ramdhavepreetam/revenant/releases) page, open it, and copy the
`revenant` binary onto your PATH:

```bash
sudo cp /Volumes/Revenant*/revenant /usr/local/bin/revenant
```

The binary is **self-contained** (bundled Python — nothing else to install).

!!! warning "Unsigned binary — Gatekeeper"
    The binary is not code-signed, so macOS may block it on first run. Allow it with:
    ```bash
    xattr -d com.apple.quarantine /usr/local/bin/revenant
    ```
    or right-click the binary in Finder → **Open** → **Open**.

## 3. Windows — standalone installer (.exe)

Download `Revenant-windows-x64-setup.exe` from the
[Releases](https://github.com/ramdhavepreetam/revenant/releases) page and run it. Tick
**"Add Revenant to your PATH"** during setup, then open a new terminal:

```powershell
revenant "where is auth handled?"
```

The installer bundles Python — nothing else to install.

## Building the installers yourself

The standalone executables are produced by [PyInstaller](https://pyinstaller.org) from
`packaging/revenant.spec`, and built in CI for both OSes by
`.github/workflows/build-installers.yml` (triggered on a `v*` tag or manually). To build
locally on the current OS:

```bash
pip install pyinstaller
pip install -e packages/nerva-core -e packages/nerva-agent -e packages/revenant-cli
pyinstaller packaging/revenant.spec --distpath dist_bin
./dist_bin/revenant --help
```
