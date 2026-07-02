"""Orpheus TTS generator (GGUF via llama-cpp-python + SNAC decode).

Based on the verified isaiahbjork/orpheus-tts-local scheme: the model emits
<custom_token_N> ids; every 7 form one frame mapped to SNAC's 3 codebooks; SNAC
decodes to 24 kHz audio. Standalone quality test before any app integration.

Usage: python tts_orpheus_gen.py <script.txt> <out.wav> [voice]
"""
from __future__ import annotations

import re
import sys
import time
import numpy as np
import soundfile as sf
import torch
from llama_cpp import Llama
from snac import SNAC

MODEL_PATH = ".aibot/orpheus-model/orpheus-3b-0.1-ft-q4_k_m.gguf"
SR = 24000
DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


def turn_token_into_id(token_text: str, index: int) -> int | None:
    """Parse a <custom_token_N> string into a SNAC code, per Orpheus scheme."""
    m = re.search(r"<custom_token_(\d+)>", token_text)
    if not m:
        return None
    return int(m.group(1)) - 10 - ((index % 7) * 4096)


def decode_frames(snac_model: SNAC, code_list: list[int]) -> np.ndarray:
    """Turn a flat list of codes into audio. 7 codes per frame -> 3 codebooks."""
    n_frames = len(code_list) // 7
    if n_frames == 0:
        return np.zeros(0, dtype=np.float32)
    codes_0, codes_1, codes_2 = [], [], []
    for i in range(n_frames):
        b = i * 7
        codes_0.append(code_list[b])
        codes_1.append(code_list[b + 1]); codes_1.append(code_list[b + 4])
        codes_2.append(code_list[b + 2]); codes_2.append(code_list[b + 3])
        codes_2.append(code_list[b + 5]); codes_2.append(code_list[b + 6])
    layers = [
        torch.tensor(codes_0, dtype=torch.int32, device=DEVICE).unsqueeze(0),
        torch.tensor(codes_1, dtype=torch.int32, device=DEVICE).unsqueeze(0),
        torch.tensor(codes_2, dtype=torch.int32, device=DEVICE).unsqueeze(0),
    ]
    # Guard: SNAC codes must be in [0, 4095]
    for layer in layers:
        if (layer < 0).any() or (layer > 4095).any():
            layer.clamp_(0, 4095)
    with torch.inference_mode():
        audio = snac_model.decode(layers)  # (1, 1, samples)
    return audio.squeeze().cpu().numpy().astype(np.float32)


def main() -> None:
    script_path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "orpheus_out.wav"
    voice = sys.argv[3] if len(sys.argv) > 3 else "tara"
    text = open(script_path, encoding="utf-8").read().strip().replace("\n", " ")

    t0 = time.time()
    print(f"[load] llama (Metal) + SNAC on {DEVICE}...", flush=True)
    llm = Llama(model_path=MODEL_PATH, n_ctx=8192, n_gpu_layers=-1, verbose=False)
    snac_model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval().to(DEVICE)
    print(f"[load] done in {time.time()-t0:.1f}s", flush=True)

    # Orpheus finetune-prod prompt framing (voice: text, wrapped in control tokens).
    prompt = f"<|audio|>{voice}: {text}<|eot_id|>"
    print(f"[gen] voice={voice}, ~{len(text)} chars...", flush=True)

    s = time.time()
    codes: list[int] = []
    index = 0
    for chunk in llm(prompt, max_tokens=8192, temperature=0.6, top_p=0.9,
                     repeat_penalty=1.1, stream=True):
        tok = chunk["choices"][0]["text"]
        cid = turn_token_into_id(tok, index)
        if cid is not None and cid >= 0:
            codes.append(cid)
            index += 1
    print(f"[gen] {len(codes)} codes in {time.time()-s:.1f}s ({len(codes)//7} frames)", flush=True)

    audio = decode_frames(snac_model, codes)
    if len(audio) == 0:
        print("[ERROR] no audio decoded — token format may differ", flush=True)
        sys.exit(2)
    sf.write(out, audio, SR)
    dur = len(audio) / SR
    print(f"[done] {dur:.1f}s audio -> {out} | total {time.time()-t0:.1f}s | RTF={(time.time()-s)/max(dur,0.1):.2f}x", flush=True)


if __name__ == "__main__":
    main()
