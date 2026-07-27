# Revenant

A local, offline **coding-agent CLI** powered by your own [Ollama](https://ollama.com)
models — a Claude-Code-style tool-calling loop that runs entirely on your machine. No cloud,
no telemetry, no API keys.

```bash
pip install revenant-cli
```

This installs the **`revenant`** command.

## Usage

```bash
# investigate (read-only)
revenant "summarize what the auth module does"

# make changes (each mutating action asks for approval)
revenant "add a /health route to the server"

# point at another repo, pick a model
revenant --workspace ~/proj --model qwen2.5-coder:7b "where is rate limiting handled?"
```

Revenant reads and searches your code (`read_file`, `glob`, `grep`), and — with your
approval — edits files and runs shell commands (`write_file`, `edit_file`, `run_bash`).
Destructive commands (`rm -rf /`, fork bombs, …) are hard-blocked even in `--yolo` mode.

## Requirements

- **Python 3.11+**
- **[Ollama](https://ollama.com) running** with a model pulled (e.g. `ollama pull qwen2.5-coder:7b`).
  Revenant talks to a local model server; it does not include an LLM.

A tool-capable model (the qwen2.5 family) is recommended — Revenant auto-detects native
tool-calling and falls back to a prompt-based protocol for models without a tool template.

## Flags

`--workspace` (repo root, confined) · `--model` · `--max-steps` · `--max-context-tokens` ·
`--read-only` · `--yolo` (auto-approve) · `--no-native-tools`.

## License

MIT. Part of the [Revenant monorepo](https://github.com/ramdhavepreetam/revenant).
Built on `nerva-core` (local-LLM layer) and `nerva-agent` (the agent engine).
