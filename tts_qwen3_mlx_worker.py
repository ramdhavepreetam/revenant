from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any


_MODEL: Any | None = None
_MODEL_ID: str | None = None


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def load_mlx_model(model_id: str) -> Any:
    global _MODEL, _MODEL_ID
    if _MODEL is not None and _MODEL_ID == model_id:
        return _MODEL

    from mlx_audio.tts.utils import load_model

    _MODEL = load_model(model_id)
    _MODEL_ID = model_id
    return _MODEL


def synthesize(payload: dict[str, Any]) -> dict[str, Any]:
    from mlx_audio.tts.generate import generate_audio

    text = str(payload.get("text") or "").strip()
    output = Path(str(payload.get("output") or "")).expanduser()
    if not text:
        raise ValueError("Text is required.")
    if not output:
        raise ValueError("Output path is required.")

    model_id = str(payload.get("model_id") or "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16")
    speaker = str(payload.get("speaker") or "Serena")
    instruct = str(payload.get("instruct") or "Use a natural conversational voice.")
    lang_code = str(payload.get("lang_code") or "en")
    ref_audio = str(payload.get("ref_audio") or "").strip() or None
    ref_text = str(payload.get("ref_text") or "").strip() or None
    temperature = float(payload.get("temperature") or 0.7)
    max_tokens = int(payload.get("max_tokens") or 1200)

    model = load_mlx_model(model_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_prefix = f"{output.stem}_mlx"
    tmp_path = output.parent / f"{tmp_prefix}.wav"
    tmp_path.unlink(missing_ok=True)
    output.unlink(missing_ok=True)

    generate_audio(
        text=text,
        model=model,
        voice=speaker,
        instruct=instruct,
        lang_code=lang_code,
        ref_audio=ref_audio,
        ref_text=ref_text,
        output_path=str(output.parent),
        file_prefix=tmp_prefix,
        audio_format="wav",
        join_audio=True,
        play=False,
        verbose=False,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    if not tmp_path.exists():
        raise ValueError("Qwen3-TTS MLX did not create an audio file.")
    shutil.move(str(tmp_path), str(output))
    return {
        "ok": True,
        "output": str(output),
        "speaker": speaker,
        "model_id": model_id,
    }


def main() -> None:
    emit({"ready": True, "engine": "qwen3_mlx"})
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            emit(synthesize(payload))
        except Exception as exc:
            emit({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    main()
