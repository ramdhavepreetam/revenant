"""Persistent Chatterbox TTS worker for AIBot.

Unlike the one-shot Kokoro worker, this process loads the model once and then
serves many requests over stdin/stdout so each reply costs ~one generation pass,
not a full ~15-20s model load.

Protocol (newline-delimited JSON):
  stdin  : one JSON request per line, e.g.
           {"id": "...", "segments": [{"kind":"speech","text":"..","exaggeration":0.8,"cfg_weight":0.3},
                                       {"kind":"sfx","path":".aibot/sfx/gasp.wav","gain":0.85}],
            "output": "/abs/path/out.wav", "voice_ref": ".aibot/voices/ref_female_pickup.wav"}
  stdout : one JSON result per line, {"id":"...","ok":true,"output":"...","duration":N}
           or {"id":"...","ok":false,"error":"..."}
  A single line {"ready": true} is emitted once the model is loaded.

The model + voice conditionals are cached. Requests are processed serially
(single local user, synchronous /api/tts endpoint), so no locking is needed.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import soundfile as sf
import torch

SR = 24000


def _device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _patch_torch_load(device: str) -> None:
    # Chatterbox checkpoints are saved with CUDA tensors; force map_location so
    # they load on MPS/CPU instead of raising on a machine without CUDA.
    _orig = torch.load

    def _patched(*args, **kwargs):
        kwargs.setdefault("map_location", device)
        return _orig(*args, **kwargs)

    torch.load = _patched


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


import re

# Cap on words per generate() call. Beyond ~30 words the super-linear cost makes
# a single call dramatically slower, so very long sentences are sub-split on commas.
_MAX_WORDS = 30


def _split_sentences(text: str) -> list[str]:
    """Split text into sentence-sized chunks for fast, separate synthesis."""
    text = text.strip()
    if not text:
        return []
    # Primary split on sentence terminators, keeping the punctuation.
    parts = re.split(r"(?<=[.!?…])\s+", text)
    chunks: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part.split()) <= _MAX_WORDS:
            chunks.append(part)
            continue
        # Long run-on: sub-split on commas/semicolons, then pack to the word cap.
        buf: list[str] = []
        for piece in re.split(r"(?<=[,;:])\s+", part):
            if len(" ".join(buf + [piece]).split()) > _MAX_WORDS and buf:
                chunks.append(" ".join(buf))
                buf = [piece]
            else:
                buf.append(piece)
        if buf:
            chunks.append(" ".join(buf))
    return chunks


def _read_sfx(path: str) -> np.ndarray:
    audio, sr = sf.read(path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != SR:  # assets are pre-resampled, but stay safe
        idx = np.round(np.linspace(0, len(audio) - 1, int(len(audio) * SR / sr))).astype(int)
        audio = audio[idx]
    return audio.astype(np.float32)


def main() -> None:
    device = _device()
    _patch_torch_load(device)

    # Keep stdout pure JSON: redirect any library banners printed during import /
    # model load (e.g. "loaded PerthNet ...") to stderr.
    import contextlib

    with contextlib.redirect_stdout(sys.stderr):
        from chatterbox.tts import ChatterboxTTS
        model = ChatterboxTTS.from_pretrained(device=device)
    # Cache prepared voice conditionals so we do not re-embed the reference clip
    # on every request; keyed by reference path.
    prepared: set[str] = set()
    _emit({"ready": True, "device": device, "sr": model.sr})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _emit({"ok": False, "error": f"bad json: {exc}"})
            continue

        req_id = req.get("id")
        try:
            voice_ref = req.get("voice_ref") or None
            output = req["output"]
            segments = req.get("segments") or []
            gap = np.zeros(int(SR * float(req.get("gap_seconds", 0.16))), dtype=np.float32)

            if voice_ref and voice_ref not in prepared:
                model.prepare_conditionals(voice_ref)
                prepared.add(voice_ref)

            pieces: list[np.ndarray] = []
            t0 = time.time()
            for seg in segments:
                if seg.get("kind") == "sfx":
                    audio = _read_sfx(seg["path"]) * float(seg.get("gain", 0.85))
                    pieces.append(audio)
                    continue
                text = (seg.get("text") or "").strip()
                if not text:
                    continue
                kwargs = {
                    "exaggeration": float(seg.get("exaggeration", 0.8)),
                    "cfg_weight": float(seg.get("cfg_weight", 0.3)),
                }
                # CRITICAL PERF: Chatterbox generation cost grows super-linearly with
                # input length — synthesizing a long block in ONE call is ~2.6x slower
                # than the same text split into sentences (measured: 73s vs 28s for a
                # 5-sentence paragraph). So split each speech segment into sentences
                # and generate them individually, concatenating the audio.
                for chunk in _split_sentences(text):
                    with contextlib.redirect_stdout(sys.stderr):
                        if voice_ref and voice_ref in prepared:
                            wav = model.generate(chunk, **kwargs)
                        else:
                            wav = model.generate(chunk, audio_prompt_path=voice_ref, **kwargs)
                    pieces.append(wav.squeeze(0).cpu().numpy().astype(np.float32))
                    pieces.append(gap)

            if not pieces:
                raise ValueError("no audio produced (empty segments)")
            audio = np.concatenate([p for p in pieces if len(p)])
            sf.write(output, audio, SR)
            _emit({"id": req_id, "ok": True, "output": output, "duration": round(len(audio) / SR, 2),
                   "gen_seconds": round(time.time() - t0, 2)})
        except Exception as exc:  # noqa: BLE001 - report any failure back to caller
            _emit({"id": req_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
