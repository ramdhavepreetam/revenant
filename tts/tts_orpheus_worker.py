"""Persistent Orpheus TTS worker for AIBot (GGUF via llama-cpp-python + SNAC).

Loads llama (Metal) + SNAC ONCE, then serves many synthesis requests over
stdin/stdout (newline-delimited JSON). Orpheus emits <custom_token_N> ids; every
7 form a frame mapped to SNAC's 3 codebooks; SNAC decodes to 24 kHz audio.

Protocol:
  stdin  : {"id":..,"text":"..","output":"/abs.wav","voice":"tara",
            "temperature":0.6,"top_p":0.9,"repeat_penalty":1.1}
  stdout : {"id":..,"ok":true,"output":"..","duration":N} | {"id":..,"ok":false,"error":".."}
  Emits {"ready": true} once models are loaded.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from llama_cpp import Llama
from snac import SNAC

MODEL_PATH = str(Path(__file__).resolve().parent / ".aibot/orpheus-model/orpheus-3b-0.1-ft-q4_k_m.gguf")
SR = 24000
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

_LLM: Llama | None = None
_SNAC: SNAC | None = None


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def turn_token_into_id(token_text: str, index: int) -> int | None:
    m = re.search(r"<custom_token_(\d+)>", token_text)
    if not m:
        return None
    return int(m.group(1)) - 10 - ((index % 7) * 4096)


def decode_frames(code_list: list[int]) -> np.ndarray:
    n_frames = len(code_list) // 7
    if n_frames == 0:
        return np.zeros(0, dtype=np.float32)
    codes_0, codes_1, codes_2 = [], [], []
    for i in range(n_frames):
        b = i * 7
        codes_0.append(code_list[b])
        codes_1 += [code_list[b + 1], code_list[b + 4]]
        codes_2 += [code_list[b + 2], code_list[b + 3], code_list[b + 5], code_list[b + 6]]
    layers = [
        torch.tensor(codes_0, dtype=torch.int32, device=DEVICE).unsqueeze(0),
        torch.tensor(codes_1, dtype=torch.int32, device=DEVICE).unsqueeze(0),
        torch.tensor(codes_2, dtype=torch.int32, device=DEVICE).unsqueeze(0),
    ]
    for layer in layers:
        layer.clamp_(0, 4095)
    with torch.inference_mode():
        audio = _SNAC.decode(layers)
    return audio.squeeze().cpu().numpy().astype(np.float32)


def synthesize(req: dict) -> dict:
    text = str(req.get("text") or "").strip()
    if not text:
        raise ValueError("text is required")
    output = Path(str(req["output"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    voice = str(req.get("voice") or "tara")
    prompt = f"<|audio|>{voice}: {text}<|eot_id|>"

    codes: list[int] = []
    index = 0
    for chunk in _LLM(
        prompt,
        max_tokens=int(req.get("max_tokens") or 8192),
        temperature=float(req.get("temperature") or 0.6),
        top_p=float(req.get("top_p") or 0.9),
        repeat_penalty=float(req.get("repeat_penalty") or 1.1),
        stream=True,
    ):
        cid = turn_token_into_id(chunk["choices"][0]["text"], index)
        if cid is not None and cid >= 0:
            codes.append(cid)
            index += 1

    audio = decode_frames(codes)
    if len(audio) == 0:
        raise ValueError("Orpheus produced no audio (token format mismatch?)")
    sf.write(str(output), audio, SR)
    return {"id": req.get("id"), "ok": True, "output": str(output), "duration": round(len(audio) / SR, 2)}


def main() -> None:
    global _LLM, _SNAC
    import contextlib
    # Keep stdout pure JSON: model-load chatter (llama/ggml/HF) goes to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        _LLM = Llama(model_path=MODEL_PATH, n_ctx=8192, n_gpu_layers=-1, verbose=False)
        _SNAC = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval().to(DEVICE)
    emit({"ready": True, "engine": "orpheus", "device": DEVICE, "sr": SR})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            emit({"ok": False, "error": f"bad json: {exc}"})
            continue
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = synthesize(req)
            emit(result)
        except Exception as exc:  # noqa: BLE001
            emit({"id": req.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
