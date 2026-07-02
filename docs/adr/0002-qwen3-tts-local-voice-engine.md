# ADR 0002: Qwen3-TTS Local Voice Engine

## Status

Accepted for experimental implementation.

## Context

The current app uses Kokoro as the default low-latency local voice engine and
Chatterbox as a reference-voice option. The companion needs more natural
prosody than simple speed/rate changes can provide: mood, energy, intimacy,
and conversational pacing should affect the synthesis itself.

Qwen3-TTS is now available as local model weights and as an installable
`qwen-tts` package. The official model cards describe:

- `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`
- `Qwen/Qwen3-TTS-12Hz-0.6B-Base`
- `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`
- `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

Relevant references:

- https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice
- https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base
- https://github.com/QwenLM/Qwen3-TTS
- https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit

The official examples target CUDA first, but MLX community conversions exist
for Apple Silicon. The Qwen3-TTS API exposes a natural-language instruction
field for CustomVoice generation, which maps well to the app's existing
sentence-level delivery metadata.

## Decision

Add Qwen3-TTS as separate experimental engines:

- `qwen3_mlx` for Apple Silicon using `mlx-audio`
- `qwen3_tts` for the official Qwen/Torch API

The default experimental profile, `qwen3-serena-local`, uses the MLX engine and
`mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16`. The official Qwen/Torch
path remains available through a separate persistent worker:

- `tts_qwen3_mlx_worker.py` with `.aibot/qwen3-mlx-venv`
- `tts_qwen3_worker.py` with `.aibot/qwen3-torch-venv`

The backend keeps the same public API:

- UI/native clients still call `POST /api/tts`
- Voice selection still uses reusable local voice profiles
- The default voice remains Kokoro until Qwen3 is benchmarked locally

Delivery metadata is translated into Qwen3's natural-language `instruct`
field instead of only numeric speed:

- mood -> tone instruction
- pace -> conversational tempo instruction
- energy -> expressiveness instruction

## Consequences

Positive:

- Keeps the app offline/local once dependencies and weights are downloaded.
- Preserves the backend/UI split.
- Avoids breaking the existing Kokoro and Chatterbox paths.
- Gives the synthesizer stronger mood/prosody information than Kokoro can use.
- Keeps the large model loaded in a persistent worker.

Negative:

- First setup requires a separate venv and model download.
- Official Qwen examples are CUDA-oriented; Apple Silicon performance must be
  measured before making this the default.
- The 0.6B CustomVoice profile may still be slower than Kokoro for short live
  sentence streaming.
- MLX may become the preferred runtime later if it proves faster/stabler on
  this Mac.

## Local Probe Result

On this machine, the official `qwen-tts` Torch path installed successfully, but
the first MPS synthesis probe failed with:

```text
probability tensor contains either `inf`, `nan` or element < 0
```

The MLX path installed successfully and generated audio through `POST /api/tts`
with `engine: qwen3_mlx`.

Observed behavior:

- first Qwen3 MLX request: slow cold start because the model had to
  download/load
- second warm request: completed in under 1 second through the backend
- current default experimental profile: `qwen3-serena-local`

## Implementation Notes

Initial Apple Silicon MLX setup:

```bash
python3 -m venv .aibot/qwen3-mlx-venv
.aibot/qwen3-mlx-venv/bin/python -m pip install -U pip mlx-audio
```

Select the `qwen3-serena-local` voice profile in the UI or API.

Official Qwen/Torch setup, kept separate because `qwen-tts` pins
`transformers==4.57.3` while `mlx-audio` currently needs a newer major
`transformers` release:

```bash
python3 -m venv .aibot/qwen3-torch-venv
.aibot/qwen3-torch-venv/bin/python -m pip install -U pip qwen-tts soundfile
```

Benchmark before promoting:

- cold worker startup time
- first sentence latency
- steady-state sentence latency
- real-time factor
- memory pressure on Apple Silicon
- quality versus `luna-companion` and `aria-companion`

If Apple Silicon performance is weak, add a second engine branch using
`mlx-audio` with `mlx-community` Qwen3-TTS models and keep the official
`qwen-tts` engine for CUDA/Linux.
