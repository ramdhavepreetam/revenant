# Deployment

Revenant is a **local CLI**, not a hosted service — "deployment" means getting the
`revenant` command onto a machine that can reach an Ollama server. This page
covers distribution, packaging, and the release pipeline.

---

## Distribution channels

| Channel | Best for | See |
|---------|----------|-----|
| **PyPI** (`pip install revenant-cli`) | Developers with Python | [Installation → pip](installation.md#method-1-pip-pipx-recommended) |
| **Standalone installers** (`.dmg` / `.exe`) | Users without Python | [Installation → binary](installation.md#method-2-standalone-binary-no-python-required) |
| **From source** (`make dev`) | Contributors | [Installation → source](installation.md#method-3-from-source-contributors) |
| **Docker** | Reproducible / CI runs | [Installation → Docker](installation.md#method-4-docker) |

## Publishing to PyPI

The three public packages are versioned together. PyPI releases are **immutable** —
you cannot overwrite a published version, so bump versions for any change.

```bash
# 1. Bump `version` in each public pyproject.toml
#    (nerva-core, nerva-agent, revenant-cli)

# 2. Build all three
for p in nerva-core nerva-agent revenant-cli; do
  python -m build packages/$p --outdir dist_pypi
done

# 3. Upload
python -m twine upload \
  dist_pypi/nerva_core-<v>* \
  dist_pypi/nerva_agent-<v>* \
  dist_pypi/revenant_cli-<v>*
```

!!! danger "Published versions are permanent"
    A version on PyPI can never be replaced. To ship a fix, bump the version and
    release again.

## Building installers via CI

Pushing a `v*` tag triggers the installer build, which attaches a macOS `.dmg`
and a Windows `.exe` to a GitHub Release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

!!! warning "Build in an isolated environment"
    Build the standalone binary in a **clean virtualenv**. A system Python with
    heavy packages (matplotlib/numpy) can break the PyInstaller build.

## Publishing the documentation

The docs site is served by **GitHub Pages** from a public repository. Build the
site and push the rendered HTML to that repo's `gh-pages` branch:

```bash
mkdocs build -f mkdocs.material.yml   # renders to ./site
# push ./site to the docs repo's gh-pages branch, then enable Pages
```

!!! tip "Push transfer errors"
    If a `git push` of the built site fails with `HTTP 400 / unexpected
    disconnect while reading sideband packet`, retry with
    `git -c http.version=HTTP/1.1 push …`.

---

## Next steps

- [Installation](installation.md)
- [Troubleshooting](troubleshooting.md)
- [Contributing](about/contributing.md)
