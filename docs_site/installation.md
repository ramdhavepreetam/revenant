# Installation

Detailed setup for every environment. For the fastest path, see the
[Quickstart](getting-started.md).

!!! note "Prerequisites (all methods)"
    - **Python 3.11+**
    - **[Ollama](https://ollama.com)** installed, running (`ollama serve`), and
      reachable at `http://localhost:11434` (configurable).
    - At least one pulled model — see [Configuration](configuration.md#models).

---

## Method 1 — pip / pipx (recommended)

The public packages are on PyPI. Installing `revenant-cli` pulls its two
dependencies automatically.

=== "pip"

    ```bash
    pip install revenant-cli
    revenant --help
    ```

=== "pipx (isolated)"

    ```bash
    pipx install revenant-cli
    revenant --help
    ```

=== "Specific version"

    ```bash
    pip install "revenant-cli==0.1.0"
    ```

The three packages installed:

| Package | PyPI |
|---------|------|
| `nerva-core` | <https://pypi.org/project/nerva-core/> |
| `nerva-agent` | <https://pypi.org/project/nerva-agent/> |
| `revenant-cli` | <https://pypi.org/project/revenant-cli/> |

## Method 2 — standalone binary (no Python required)

Prebuilt installers bundle a self-contained `revenant` binary (~8 MB); you do
**not** need Python installed to run them. You still need Ollama.

=== "macOS (.dmg)"

    1. Download `Revenant-<version>-macos-arm64.dmg` from the
       [GitHub Releases](https://github.com/ramdhavepreetam/revenant/releases) page.
    2. Open the `.dmg` and move `revenant` where you like.
    3. The binary is **unsigned**, so Gatekeeper will quarantine it. Clear the
       quarantine attribute:

       ```bash
       xattr -d com.apple.quarantine /path/to/revenant
       ```

=== "Windows (.exe)"

    1. Download the Inno-Setup installer `Revenant-<version>-setup.exe` from
       [Releases](https://github.com/ramdhavepreetam/revenant/releases).
    2. Run it and follow the prompts. `revenant` is added to your `PATH`.

!!! tip "Where releases come from"
    Installers are built by CI when a `v*` tag is pushed. If no release assets
    exist yet, use the pip install instead.

## Method 3 — from source (contributors)

Clone the monorepo and install the packages in dependency order.

```bash
git clone git@github.com:ramdhavepreetam/revenant.git
cd revenant
make dev     # pip install -e each package in order: core → agent → cli
make test    # run the test suite
```

`make dev` installs `nerva-core`, `nerva-agent`, and `revenant-cli` as editable
packages so your changes are picked up live.

!!! warning "Run from the repo root"
    Revenant's data directory (`.aibot/`) resolves **relative to the current
    working directory**. Run commands from the repo root so it and any local
    config are found.

## Method 4 — Docker

Revenant needs to reach an Ollama server. The container runs the CLI; Ollama
runs on the host (or another container).

```dockerfile title="Dockerfile"
FROM python:3.11-slim
RUN pip install --no-cache-dir revenant-cli
WORKDIR /work
ENTRYPOINT ["revenant"]
```

```bash
# Build
docker build -t revenant:0.1.0 .

# Run — mount your project, point at the host's Ollama
docker run --rm -it \
  -v "$PWD:/work" \
  -e OLLAMA_HOST=http://host.docker.internal:11434 \
  revenant:0.1.0 --base-url http://host.docker.internal:11434 \
  --read-only "Summarize this project"
```

!!! note "host.docker.internal on Linux"
    On Linux, add `--add-host=host.docker.internal:host-gateway` to the
    `docker run` command so the container can reach Ollama on the host.

---

## Verify your install

```bash
revenant --help
# then a harmless read-only run against any repo:
revenant --read-only "List the top-level files and say what this project is."
```

If that returns an answer, you're set.

---

## Next steps

- [Configuration](configuration.md) — models, roles, base URL, and data dir.
- [Guides](guides/index.md) — task-oriented workflows.
