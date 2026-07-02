"""Persistent Kokoro TTS worker for AIBot.

Loads the Kokoro pipeline ONCE, then serves many synthesis requests over
stdin/stdout (newline-delimited JSON). This avoids the ~5-6s model reload that a
one-shot-per-request worker pays on every call — critical when one reply is split
into several speech runs.

Protocol:
  stdin  : one JSON request per line:
           {"id":..,"text":"..","output":"/abs.wav","voice":"af_heart","speed":0.92,
            "lang_code":"a","split_pattern":".."}
  stdout : one JSON result per line:
           {"id":..,"ok":true,"output":"..","sample_rate":24000,"chunks":N}
           or {"id":..,"ok":false,"error":".."}
  Emits {"ready": true} once the pipeline is loaded.

Requests are processed serially (single local user). The pipeline is reused
across requests; only voice/speed/text vary per call.
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path
from typing import Any

SR = 24000


def _audio_from_result(result: Any):
    if hasattr(result, "audio"):
        audio = result.audio
    elif isinstance(result, tuple) and len(result) >= 3:
        audio = result[2]
    else:
        raise ValueError("Kokoro returned an unexpected audio result.")
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return audio


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    warnings.filterwarnings("ignore")

    import contextlib

    # Keep stdout pure JSON: any library banner during import/load goes to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        from kokoro import KPipeline
        import numpy as np
        import soundfile as sf
        # One pipeline per lang_code, lazily built and cached.
        pipelines: dict[str, Any] = {}

    def get_pipeline(lang_code: str, repo_id: str):
        if lang_code not in pipelines:
            with contextlib.redirect_stdout(sys.stderr):
                pipelines[lang_code] = KPipeline(lang_code=lang_code, repo_id=repo_id)
        return pipelines[lang_code]

    # Warm the default pipeline so the first real request is fast.
    get_pipeline("a", "hexgrad/Kokoro-82M")
    _emit({"ready": True, "sr": SR})

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
            text = str(req.get("text") or "").strip()
            if not text:
                raise ValueError("text is required")
            output_path = Path(str(req["output"]))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            pipeline = get_pipeline(
                str(req.get("lang_code") or "a"),
                str(req.get("repo_id") or "hexgrad/Kokoro-82M"),
            )
            with contextlib.redirect_stdout(sys.stderr):
                generator = pipeline(
                    text,
                    voice=str(req.get("voice") or "af_heart"),
                    speed=float(req.get("speed") or 1.0),
                    split_pattern=str(req.get("split_pattern") or r"(?<=[.!?])\s+|\n+"),
                )
                chunks = [_audio_from_result(r) for r in generator]
            if not chunks:
                raise ValueError("Kokoro did not generate audio.")
            silence = np.zeros(int(SR * 0.07), dtype=chunks[0].dtype)
            parts = []
            for i, chunk in enumerate(chunks):
                if i:
                    parts.append(silence)
                parts.append(chunk)
            audio = np.concatenate(parts)
            sf.write(str(output_path), audio, SR)
            _emit({"id": req_id, "ok": True, "output": str(output_path), "sample_rate": SR, "chunks": len(chunks)})
        except Exception as exc:  # noqa: BLE001
            _emit({"id": req_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
