# Troubleshooting & FAQ

Common problems and fixes. If something isn't here, open an issue on the
[repository](https://github.com/ramdhavepreetam/revenant).

---

## Connection & models

??? failure "`Connection refused` / can't reach Ollama"
    Ollama isn't running or isn't at the expected URL.

    - Start it: `ollama serve`.
    - Check the URL: default is `http://localhost:11434`. Override with
      `--base-url` or `OLLAMA_HOST`.
    - From Docker, use `host.docker.internal` (see
      [Installation → Docker](installation.md#method-4-docker)).

??? failure "`model not found` / model won't load"
    The model isn't pulled.

    ```bash
    ollama pull qwen2.5-coder:7b
    ollama list          # confirm it's present
    ```

    Make sure the tag in `--model` / `profiles.json` matches exactly.

## Tool calls

??? failure "The model ignores tools or emits malformed calls"
    Some models lack a native tool template. Force the prompt-based protocol:

    ```bash
    revenant --no-native-tools --model <model> "..."
    ```

    See [Configure model routing](guides/model-routing.md).

??? question "I re-pulled a model and tool behavior changed"
    Native-tool detection is **cached per model**. After a re-pull, clear the
    cache so Revenant re-probes it (`agent_native_tools.clear_cache()`), or use
    `--no-native-tools` to force the fallback.

## Files & workspace

??? failure "Revenant can't find my files / config"
    The data directory `.aibot/` and the default workspace resolve **relative to
    the current directory**. Run from your repo root, or pass an explicit
    `--workspace PATH`.

??? question "Can Revenant touch files outside my project?"
    No. All tools are path-confined to the `--workspace` directory.

## Safety

??? question "A command I asked for was refused"
    Revenant hard-blocks destructive footguns (`rm -rf /`, fork bombs, `mkfs`)
    in **all** modes, including `--yolo`. This is intentional. See
    [Approvals & safety](guides/approvals-and-safety.md).

??? question "How do I stop being prompted for every change?"
    `--yolo` auto-approves mutations — but only use it in disposable workspaces.
    For normal work, approving each change is the safe default.

## Install & build

??? failure "PyInstaller build fails"
    Build the standalone binary in a **clean virtualenv**. A system Python with
    matplotlib/numpy in site-packages can break the build.

??? failure "macOS won't open the binary (Gatekeeper)"
    The binary is unsigned. Clear the quarantine attribute:

    ```bash
    xattr -d com.apple.quarantine /path/to/revenant
    ```

??? failure "`git push` of the docs site fails with HTTP 400"
    Retry over HTTP/1.1:

    ```bash
    git -c http.version=HTTP/1.1 push -f <remote> HEAD:gh-pages
    ```

---

## FAQ

??? question "Does Revenant send my code anywhere?"
    No. The model is your local Ollama model; the workspace and history are
    local. There is no telemetry and no API key.

??? question "Which models work best?"
    Tool-capable coders (a model with a tool template, e.g. `qwen2.5-coder:7b`).
    Models without a tool template work via the prompt-based fallback.

??? question "Is there a hosted or GUI version?"
    Revenant is a CLI. It's part of a monorepo that also contains a private
    companion app, but the public product is the command-line agent.

---

## Next steps

- [Configuration](configuration.md)
- [CLI reference](reference/cli.md)
- [Contributing](about/contributing.md)
