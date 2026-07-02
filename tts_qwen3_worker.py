from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


_MODEL: Any | None = None
_MODEL_KEY: tuple[str, str] | None = None


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def choose_runtime(device: str) -> tuple[str, Any, str | None]:
    import torch

    requested = (device or "auto").strip().lower()
    if requested == "cuda" or (requested == "auto" and torch.cuda.is_available()):
        return "cuda:0", torch.bfloat16, "flash_attention_2"
    if requested == "mps" or (requested == "auto" and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()):
        return "mps", torch.float16, None
    return "cpu", torch.float32, None


def load_qwen3_model(model_id: str, device: str) -> Any:
    global _MODEL, _MODEL_KEY

    key = (model_id, device)
    if _MODEL is not None and _MODEL_KEY == key:
        return _MODEL

    import torch
    from qwen_tts import Qwen3TTSModel

    device_map, dtype, attn_implementation = choose_runtime(device)
    kwargs: dict[str, Any] = {
        "device_map": device_map,
        "dtype": dtype,
    }
    if attn_implementation:
        kwargs["attn_implementation"] = attn_implementation

    model = Qwen3TTSModel.from_pretrained(model_id, **kwargs)
    if device_map == "mps" and hasattr(model, "to"):
        try:
            model = model.to("mps")
        except Exception:
            pass
    if hasattr(torch, "mps") and device_map == "mps":
        try:
            torch.mps.empty_cache()
        except Exception:
            pass

    _MODEL = model
    _MODEL_KEY = key
    return model


def synthesize(payload: dict[str, Any]) -> dict[str, Any]:
    import soundfile as sf

    text = str(payload.get("text") or "").strip()
    output = Path(str(payload.get("output") or "")).expanduser()
    if not text:
        raise ValueError("Text is required.")
    if not output:
        raise ValueError("Output path is required.")

    model_id = str(payload.get("model_id") or "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
    language = str(payload.get("language") or "English")
    speaker = str(payload.get("speaker") or "Serena")
    instruct = str(payload.get("instruct") or "Use a natural conversational voice.")
    device = str(payload.get("device") or "auto")

    model = load_qwen3_model(model_id, device)
    output.parent.mkdir(parents=True, exist_ok=True)

    if hasattr(model, "generate_custom_voice"):
        wavs, sample_rate = model.generate_custom_voice(
            text=text,
            language=language,
            speaker=speaker,
            instruct=instruct,
        )
    else:
        raise ValueError("Installed qwen-tts package does not expose generate_custom_voice().")

    if not wavs:
        raise ValueError("Qwen3-TTS returned no audio.")
    sf.write(str(output), wavs[0], sample_rate)
    return {
        "ok": True,
        "output": str(output),
        "sample_rate": sample_rate,
        "speaker": speaker,
        "model_id": model_id,
    }


def main() -> None:
    emit({"ready": True, "engine": "qwen3_tts"})
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            emit(synthesize(payload))
        except Exception as exc:
            emit({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    main()
