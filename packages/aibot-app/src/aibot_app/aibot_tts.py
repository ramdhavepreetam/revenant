from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any


DEFAULT_VOICE_PROFILES: dict[str, dict[str, Any]] = {
    "mira-neural": {
        "engine": "kokoro",
        "voice": "af_heart",
        "speed": 0.95,
        "mood": "soft",
        "lang_code": "a",
        "mood_voices": {
            "soft": "af_heart",
            "bright": "af_bella",
            "calm": "af_sarah",
            "dramatic": "am_adam",
        },
        "notes": "Kokoro neural voice. Warm default for local companion replies.",
    },
    "mira-bright-neural": {
        "engine": "kokoro",
        "voice": "af_bella",
        "speed": 1.04,
        "mood": "bright",
        "lang_code": "a",
        "notes": "Kokoro neural voice with a lighter, quicker delivery.",
    },
    "mira-calm-neural": {
        "engine": "kokoro",
        "voice": "af_sarah",
        "speed": 0.88,
        "mood": "calm",
        "lang_code": "a",
        "notes": "Kokoro neural voice for slower and quieter replies.",
    },
    "system-fallback": {
        "engine": "macos_say",
        "voice": "Samantha",
        "rate": 145,
        "mood": "calm",
        "notes": "Emergency macOS system voice fallback. Less natural than Kokoro.",
    },
    # --- Orpheus companion (DEFAULT) ---------------------------------------------
    # Emotional voice with REAL in-voice non-verbal sounds (<gasp>/<sigh>/<groan>/
    # <giggle>) from inline tags — the "feels alive" voice. ~1.6x realtime (slower
    # than Kokoro); streaming hides it. New voice = change `voice` (tara/leah/jess/
    # mia/zoe female, leo/dan/zac male).
    "orpheus-companion": {
        "engine": "orpheus",
        "voice": "tara",
        "mood": "warm",
        "temperature": 0.6,
        "top_p": 0.9,
        "repeat_penalty": 1.1,
        "notes": "Default 21+ companion voice. Orpheus tara — emotional timbre + "
                 "in-voice gasp/sigh/groan from *action beats*. No splicing, one voice.",
    },
    # --- Kokoro companion (fast fallback) ----------------------------------------
    # Fast (~0.3x realtime), smooth, natural. Speed-only mood. Kept as a fast
    # fallback / alternative; Orpheus is the default for emotional range.
    "luna-companion": {
        "engine": "kokoro",
        "voice": "af_heart",
        "speed": 1.06,
        "mood": "warm",
        "lang_code": "a",
        # NO mood_voices: the companion is ONE consistent voice. Mood changes only
        # delivery PACING (mood_speed), never the speaker — otherwise a streamed
        # reply would switch voices sentence-to-sentence and sound like several people.
        "mood_speed": {
            "soft": 0.98,
            "calm": 1.0,
            "warm": 1.06,
            "neutral": 1.08,
            "bright": 1.12,
            "intense": 1.03,
            "dramatic": 1.05,
        },
        "sfx_enabled": True,
        "notes": "Default 21+ companion voice. Kokoro af_heart — ONE consistent voice; "
                 "mood adjusts pacing only. Cue-triggered intimate SFX.",
    },
    # --- Chatterbox voice avatars -------------------------------------------------
    # A new avatar is just another entry here: drop a 5-10s speaking reference clip
    # into .aibot/voices/ and point voice_ref at it. No code change required.
    # mood_delivery maps a detected mood -> (exaggeration, cfg_weight). Lower
    # cfg_weight = slower, more deliberate/intimate pacing.
    "aria-companion": {
        "engine": "chatterbox",
        "voice_ref": ".aibot/voices/ref_female_pickup.wav",
        "mood": "warm",
        "exaggeration": 0.85,
        "cfg_weight": 0.30,
        "mood_delivery": {
            "soft": [0.6, 0.20],
            "calm": [0.65, 0.22],
            "warm": [0.85, 0.30],
            "neutral": [0.8, 0.32],
            "bright": [1.0, 0.40],
            "intense": [1.1, 0.28],
            "dramatic": [1.15, 0.30],
        },
        "sfx_enabled": True,
        "notes": "Female romantic companion. Cloned from a speaking reference clip; "
                 "supports cue-triggered intimate SFX. Default 21+ companion voice.",
    },
    # --- Qwen3-TTS experimental local engine -------------------------------------
    # Higher quality prosody/control path. Requires the optional .aibot/qwen3-tts-venv
    # environment with qwen-tts installed. Kept off the default path until locally
    # benchmarked on this Mac.
    "qwen3-serena-local": {
        "engine": "qwen3_mlx",
        "model_id": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16",
        "speaker": "Serena",
        "language": "English",
        "lang_code": "en",
        "mood": "warm",
        "qwen3_instruct": "Speak as a warm, natural companion in direct conversation. Use human pacing, gentle emotion, and short responsive phrasing unless the text clearly needs more.",
        "mood_instructions": {
            "soft": "Use a quiet, close, tender voice with smaller pauses.",
            "calm": "Use an even, relaxed voice with steady pacing.",
            "warm": "Use a warm, affectionate voice with natural conversational rhythm.",
            "neutral": "Use a natural conversational voice.",
            "bright": "Use a lighter, playful, smiling voice.",
            "intense": "Use a closer, breathier, emotionally focused voice without overacting.",
            "dramatic": "Use more urgency and tension while keeping it conversational.",
        },
        "timeout_seconds": 420,
        "notes": "Experimental Qwen3-TTS MLX CustomVoice profile for Apple Silicon; intended for quality benchmarking against Kokoro/Chatterbox.",
    },
}


def ensure_voice_profiles(path: Path | str) -> Path:
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    if not profile_path.exists():
        profile_path.write_text(json.dumps({"voices": DEFAULT_VOICE_PROFILES}, indent=2), encoding="utf-8")
    return profile_path


def load_voice_profiles(path: Path | str) -> dict[str, Any]:
    profile_path = ensure_voice_profiles(path)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    voices = data.setdefault("voices", {})
    changed = False
    for key, value in DEFAULT_VOICE_PROFILES.items():
        if key not in voices:
            voices[key] = value
            changed = True
    luna = voices.get("luna-companion")
    if isinstance(luna, dict):
        defaults = DEFAULT_VOICE_PROFILES["luna-companion"]
        for key in ("speed", "mood_speed"):
            if luna.get(key) != defaults[key]:
                luna[key] = defaults[key]
                changed = True
    qwen3 = voices.get("qwen3-serena-local")
    if isinstance(qwen3, dict):
        defaults = DEFAULT_VOICE_PROFILES["qwen3-serena-local"]
        for key in ("engine", "model_id", "speaker", "language", "lang_code", "qwen3_instruct", "mood_instructions", "notes"):
            if qwen3.get(key) != defaults[key]:
                qwen3[key] = defaults[key]
                changed = True
    if changed:
        profile_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def clean_tts_text(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    # Drop *asterisk action beats* entirely (e.g. "*nuzzles your neck*") so the
    # voice never narrates its own stage directions. Only the spoken dialogue
    # between beats is kept. (In the SFX path build_segments has already removed
    # these, so this is a no-op there; in the non-SFX path it's the safety net.)
    text = re.sub(r"\*[^*]+\*", " ", text)
    text = re.sub(r"[*_#>`]+", "", text)  # any stray leftover markdown chars
    text = re.sub(r"\s+", " ", text).strip()
    return text[:5000]


VOICE_FILLER_PATTERN = re.compile(
    r"\b(?:m+hm+|m{2,}h*|h?m{2,}|ah{2,}|uh{2,}|ng+h+|n+g+h+)\b",
    flags=re.IGNORECASE,
)


def prepare_spoken_text(text: str) -> str:
    """Voice-only cleanup for model text that should not be spoken literally.

    Local RP models often emit phonetic reaction tokens like "mmh" or "ngh".
    TTS reads those as letters/syllables, which sounds artificial. Keep the chat
    transcript intact, but remove those tokens from speech.
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(
        r"\((?:[^)]*\b(?:sighs?|gasps?|laughs?|giggles?|moans?|breathes?|whispers?|pants?|shudders?)\b[^)]*)\)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b[oO]h{2,}\b", "oh", text)
    text = VOICE_FILLER_PATTERN.sub(" ", text)
    text = clean_tts_text(text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r"(?:\s*[,.!?;:]){2,}", ".", text)
    text = re.sub(r"^\s*[,.!?;:]+\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:5000]


# Cue -> SFX mapping for Chatterbox. RP models (e.g. Stheno) emit freeform
# *asterisk action beats* like "*a contented sigh escapes me*", not fixed tokens,
# so we keyword-match the CONTENT of each span. Earlier rules win. A span that
# matches a sound becomes that SFX; a span that matches nothing is an action beat
# and is DROPPED from speech (the companion should not read its own stage
# directions aloud). Ordered most-specific first.
SFX_CUE_RULES: list[tuple[str, str]] = [
    (r"\bwet|slick|slid(e|ing)|rub(bing|s)?\b", ".aibot/sfx/wet.wav"),
    (r"\bmoan|whimper|whine\b", ".aibot/sfx/moan_omg.wav"),
    (r"\bgasp|sharp breath|inhal\b", ".aibot/sfx/gasp.wav"),
    (r"\bsigh|exhal|breath|pant|shudder|shiver\b", ".aibot/sfx/moan_raspy.wav"),
]


def map_span_to_sfx(span_text: str) -> str | None:
    """Return an SFX path if an *action beat* implies a sound, else None (drop it)."""
    lowered = span_text.lower()
    for pattern, path in SFX_CUE_RULES:
        if re.search(pattern, lowered):
            return path
    return None


def build_segments(text: str, exaggeration: float, cfg_weight: float, sfx_enabled: bool) -> list[dict]:
    """Turn a raw reply into an ordered list of speech / sfx segments.

    *...* spans become SFX (if cue-matched and enabled) or are dropped; the prose
    between them is spoken. Markdown/formatting is stripped from spoken spans only.
    """
    segments: list[dict] = []
    last = 0
    for match in re.finditer(r"\*([^*]+)\*", text):
        spoken = text[last:match.start()]
        _append_speech(segments, spoken, exaggeration, cfg_weight)
        span = match.group(1).strip()
        sfx = map_span_to_sfx(span) if sfx_enabled else None
        if sfx:
            # SFX assets are loudness-matched ~4 dB under speech; this gain trims
            # them a little further so reaction sounds sit under the dialogue.
            segments.append({"kind": "sfx", "path": sfx, "gain": 0.7})
        # else: action beat with no sound -> dropped from speech entirely
        last = match.end()
    _append_speech(segments, text[last:], exaggeration, cfg_weight)
    return segments


def _append_speech(segments: list[dict], raw: str, exaggeration: float, cfg_weight: float) -> None:
    spoken = prepare_spoken_text(raw)
    if spoken:
        segments.append({
            "kind": "speech",
            "text": spoken,
            "exaggeration": exaggeration,
            "cfg_weight": cfg_weight,
        })


def detect_mood(text: str) -> str:
    lower = text.lower()
    if any(word in lower for word in ("moan", "gasp", "breath", "desire", "want you", "closer", "kiss", "touch", "skin")):
        return "intense"
    if any(word in lower for word in ("tired", "sad", "hurt", "afraid", "lonely", "softly", "quiet", "whisper")):
        return "soft"
    if any(word in lower for word in ("excited", "happy", "playful", "laugh", "grin", "tease")):
        return "bright"
    if any(word in lower for word in ("angry", "tense", "danger", "urgent", "panic")):
        return "dramatic"
    if any(word in lower for word in ("calm", "slow", "breathe", "rest")):
        return "calm"
    if any(word in lower for word in ("love", "missed", "warm", "hold", "close")):
        return "warm"
    return "neutral"


def apply_mood(profile: dict[str, Any], mood: str) -> dict[str, Any]:
    adjusted = dict(profile)
    if str(adjusted.get("engine") or "") == "chatterbox":
        delivery = adjusted.get("mood_delivery") or {}
        pair = delivery.get(mood) or delivery.get(adjusted.get("mood") or "warm")
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            adjusted["exaggeration"] = float(pair[0])
            adjusted["cfg_weight"] = float(pair[1])
        adjusted["mood"] = mood
        return adjusted
    if str(adjusted.get("engine") or "") in {"qwen3_tts", "qwen3_mlx"}:
        adjusted["mood"] = mood
        return adjusted
    if str(adjusted.get("engine") or "") == "kokoro":
        # Explicit per-mood speed table wins; otherwise fall back to nudges.
        mood_speed = adjusted.get("mood_speed") or {}
        if isinstance(mood_speed, dict) and mood_speed.get(mood) is not None:
            adjusted["speed"] = float(mood_speed[mood])
        else:
            base_speed = float(adjusted.get("speed") or 1.0)
            if mood == "soft":
                adjusted["speed"] = min(base_speed, 0.95)
            elif mood == "bright":
                adjusted["speed"] = max(base_speed, 1.04)
            elif mood == "dramatic":
                adjusted["speed"] = max(base_speed, 1.02)
            elif mood == "calm":
                adjusted["speed"] = min(base_speed, 0.9)
        mood_voices = adjusted.get("mood_voices") or {}
        if isinstance(mood_voices, dict) and mood_voices.get(mood):
            adjusted["voice"] = mood_voices[mood]
    else:
        base_rate = int(adjusted.get("rate") or 170)
        if mood == "soft":
            adjusted["rate"] = min(base_rate, 160)
        elif mood == "bright":
            adjusted["rate"] = max(base_rate, 185)
        elif mood == "dramatic":
            adjusted["rate"] = max(base_rate, 175)
        elif mood == "calm":
            adjusted["rate"] = min(base_rate, 150)
    adjusted["mood"] = mood
    return adjusted


def apply_delivery(profile: dict[str, Any], delivery: dict[str, Any] | None) -> dict[str, Any]:
    if not delivery:
        return dict(profile)
    adjusted = dict(profile)
    pace = delivery.get("pace")
    energy = delivery.get("energy")
    try:
        pace_value = float(pace)
    except (TypeError, ValueError):
        pace_value = 0.0
    try:
        energy_value = float(energy)
    except (TypeError, ValueError):
        energy_value = -1.0

    engine = str(adjusted.get("engine") or "")
    if engine == "kokoro":
        if pace_value > 0:
            adjusted["speed"] = max(0.75, min(1.25, pace_value))
    elif engine == "chatterbox":
        if energy_value >= 0:
            adjusted["exaggeration"] = max(0.35, min(1.25, 0.45 + energy_value * 0.75))
            adjusted["cfg_weight"] = max(0.18, min(0.45, 0.42 - energy_value * 0.18))
    elif engine == "macos_say":
        if pace_value > 0:
            adjusted["rate"] = int(max(120, min(220, 170 * pace_value)))
    return adjusted


class _PersistentWorker:
    """Lazily-started, long-lived TTS subprocess (loads model once, serves many).

    Generic over which venv + worker script to run, so both the Chatterbox and
    Kokoro engines reuse the same proven IPC: newline-delimited JSON over
    stdin/stdout, a hard per-request timeout, and kill+restart on hang/death.
    """

    def __init__(self, name: str, venv_dir: str, worker_script: str) -> None:
        self.name = name
        self.venv_dir = venv_dir
        self.worker_script = worker_script
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _start(self) -> subprocess.Popen:
        project_dir = Path.cwd()  # repo root (where the app is launched)
        python_path = project_dir / ".aibot" / self.venv_dir / "bin" / "python"
        worker_path = Path(__file__).resolve().parent / self.worker_script
        if not python_path.exists():
            raise ValueError(f"{self.name} venv was not found at .aibot/{self.venv_dir}.")
        if not worker_path.exists():
            raise ValueError(f"{self.name} worker ({self.worker_script}) was not found.")
        env = os.environ.copy()
        env.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        proc = subprocess.Popen(
            [str(python_path), str(worker_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(project_dir),
        )
        # Block until the model finishes loading. Library banners may hit stdout
        # during load, so skip anything that is not our JSON protocol line.
        ready = self._read_json(proc, timeout=120)
        if not ready.get("ready"):
            raise ValueError(f"{self.name} worker failed to start.")
        return proc

    def _read_json(self, proc: subprocess.Popen, timeout: float) -> dict:
        """Read lines until a valid JSON object is found, with a hard deadline.

        readline() has no native timeout, so read on a daemon thread and join
        against the deadline. A stuck generation must not block the worker (and,
        via the lock, every later request) forever — the caller kills + restarts.
        """
        result: dict = {}
        error: dict = {}

        def _reader() -> None:
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        error["e"] = ValueError(f"{self.name} worker stopped responding.")
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        result["v"] = json.loads(line)
                        return
                    except json.JSONDecodeError:
                        continue  # stray library stdout (progress, banners) — skip it
            except Exception as exc:  # noqa: BLE001
                error["e"] = exc

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError(f"{self.name} synthesis exceeded {timeout:.0f}s.")
        if "e" in error:
            raise error["e"]
        return result["v"]

    def request(self, payload: dict, timeout: int = 300) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = self._start()
            proc = self._proc
            try:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
                return self._read_json(proc, timeout=timeout)
            except (ValueError, BrokenPipeError, TimeoutError):
                # Worker died or hung; kill it so the next request starts fresh
                # instead of stacking behind a wedged process.
                self._kill()
                raise

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None

    def prewarm(self) -> None:
        """Start the worker (load the model) ahead of the first user request."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = self._start()


_CHATTERBOX = _PersistentWorker("Chatterbox", "chatterbox-venv", "tts_chatterbox_worker.py")
_KOKORO = _PersistentWorker("Kokoro", "tts-venv", "tts_kokoro_worker.py")
_QWEN3 = _PersistentWorker("Qwen3-TTS", "qwen3-torch-venv", "tts_qwen3_worker.py")
_QWEN3_MLX = _PersistentWorker("Qwen3-TTS-MLX", "qwen3-mlx-venv", "tts_qwen3_mlx_worker.py")
_ORPHEUS = _PersistentWorker("Orpheus", "orpheus-venv", "tts_orpheus_worker.py")


# Orpheus produces non-verbal sounds IN ITS OWN VOICE from inline tags (no splicing).
# Map *action-beat* content -> a supported tag, or drop the beat (it's narration).
# Supported tags: <gasp> <sigh> <groan> <laugh> <chuckle> <giggle> <yawn> <cough> <sniffle>.
ORPHEUS_TAG_RULES: list[tuple[str, str]] = [
    (r"\bgasp|sharp breath|inhal\b", "<gasp>"),
    (r"\bmoan|whimper|whine|groan\b", "<groan>"),      # closest to a moan Orpheus has
    (r"\bsigh|exhal|breath|pant|shudder|shiver\b", "<sigh>"),
    (r"\blaugh|giggl|chuckl|grin\b", "<giggle>"),
    (r"\byawn|tired|sleepy\b", "<yawn>"),
]


def map_span_to_orpheus_tag(span_text: str) -> str | None:
    lowered = span_text.lower()
    for pattern, tag in ORPHEUS_TAG_RULES:
        if re.search(pattern, lowered):
            return tag
    return None


def orpheus_text_with_tags(raw_text: str) -> str:
    """Turn a reply into Orpheus input: dialogue kept, *action beats* converted to
    inline sound tags where they imply a sound, else dropped. Markdown stripped."""
    out: list[str] = []
    last = 0
    for m in re.finditer(r"\*([^*]+)\*", raw_text):
        spoken = raw_text[last:m.start()]
        if spoken.strip():
            out.append(clean_tts_text(spoken))
        tag = map_span_to_orpheus_tag(m.group(1))
        if tag:
            out.append(tag)
        last = m.end()
    tail = raw_text[last:]
    if tail.strip():
        out.append(clean_tts_text(tail))
    return re.sub(r"\s+", " ", " ".join(p for p in out if p)).strip()


def synthesize_orpheus(raw_text: str, profile: dict[str, Any], audio_dir: Path) -> dict[str, Any]:
    """Orpheus voice: emotional timbre + in-voice sounds from inline tags."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    text = orpheus_text_with_tags(raw_text)
    if not text:
        raise ValueError("Nothing speakable in the reply after cue parsing.")
    output_path = audio_dir / f"{uuid.uuid4().hex}.wav"
    result = _ORPHEUS.request(
        {
            "id": output_path.stem,
            "text": text,
            "output": str(output_path),
            "voice": str(profile.get("voice") or "tara"),
            "temperature": float(profile.get("temperature") or 0.6),
            "top_p": float(profile.get("top_p") or 0.9),
            "repeat_penalty": float(profile.get("repeat_penalty") or 1.1),
        },
        timeout=int(profile.get("timeout_seconds") or 300),
    )
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "Orpheus synthesis failed."))
    if not output_path.exists():
        raise ValueError("Orpheus worker completed without creating audio.")
    return {"path": output_path, "url": f"/audio/{output_path.name}", "mime_type": "audio/wav"}


def prewarm_voice() -> None:
    """Warm the default-engine model ahead of first use. Orpheus is the default
    companion voice now (slow to load), so warm it; ignore failures (best-effort)."""
    try:
        _ORPHEUS.prewarm()
    except Exception:  # noqa: BLE001 - prewarm is best-effort, never block boot
        pass


def synthesize_chatterbox(raw_text: str, profile: dict[str, Any], audio_dir: Path) -> dict[str, Any]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    project_dir = Path.cwd()  # repo root (where the app is launched)
    voice_ref = str(profile.get("voice_ref") or "").strip()
    voice_ref_abs = str((project_dir / voice_ref)) if voice_ref else None
    if voice_ref_abs and not Path(voice_ref_abs).exists():
        raise ValueError(f"Voice reference clip not found: {voice_ref}")

    exaggeration = float(profile.get("exaggeration") or 0.8)
    cfg_weight = float(profile.get("cfg_weight") or 0.3)
    sfx_enabled = bool(profile.get("sfx_enabled", True))
    segments = build_segments(raw_text, exaggeration, cfg_weight, sfx_enabled)
    if not segments:
        raise ValueError("Nothing speakable in the reply after cue parsing.")
    # Resolve sfx paths to absolute for the worker's cwd safety.
    for seg in segments:
        if seg.get("kind") == "sfx":
            seg["path"] = str(project_dir / seg["path"])

    output_path = audio_dir / f"{uuid.uuid4().hex}.wav"
    result = _CHATTERBOX.request(
        {
            "id": output_path.stem,
            "segments": segments,
            "output": str(output_path),
            "voice_ref": voice_ref_abs,
        },
        timeout=int(profile.get("timeout_seconds") or 300),
    )
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "Chatterbox synthesis failed."))
    if not output_path.exists():
        raise ValueError("Chatterbox worker completed without creating audio.")
    return {
        "path": output_path,
        "url": f"/audio/{output_path.name}",
        "mime_type": "audio/wav",
    }


def synthesize_kokoro(text: str, profile: dict[str, Any], audio_dir: Path) -> dict[str, Any]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    output_path = audio_dir / f"{uuid.uuid4().hex}.wav"
    # Persistent worker: model loads once, so a reply split into several speech
    # runs no longer pays a fresh ~5-6s model load per run.
    result = _KOKORO.request(
        {
            "id": output_path.stem,
            "text": text,
            "output": str(output_path),
            "voice": str(profile.get("voice") or "af_heart"),
            "speed": float(profile.get("speed") or 1.0),
            "lang_code": str(profile.get("lang_code") or "a"),
            "split_pattern": str(profile.get("split_pattern") or r"(?<=[.!?])\s+|\n+"),
        },
        timeout=int(profile.get("timeout_seconds") or 120),
    )
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "Kokoro synthesis failed."))
    if not output_path.exists():
        raise ValueError("Kokoro worker completed without creating audio.")
    return {
        "path": output_path,
        "url": f"/audio/{output_path.name}",
        "mime_type": "audio/wav",
    }


def synthesize_kokoro_with_sfx(raw_text: str, profile: dict[str, Any], audio_dir: Path) -> dict[str, Any]:
    """Kokoro voice + cue-triggered SFX. Parses *action beats* (cue->SFX or drop),
    synthesizes the spoken runs with Kokoro, and splices SFX inline. One WAV out."""
    import numpy as np
    import soundfile as sf

    audio_dir.mkdir(parents=True, exist_ok=True)
    project_dir = Path.cwd()  # repo root (where the app is launched)
    SR = 24000
    gap = np.zeros(int(SR * 0.04), dtype=np.float32)

    # Reuse the engine-agnostic cue parser (exaggeration/cfg unused for Kokoro).
    segments = build_segments(raw_text, 0.0, 0.0, sfx_enabled=True)
    if not segments:
        raise ValueError("Nothing speakable in the reply after cue parsing.")

    pieces: list = []
    for seg in segments:
        if seg.get("kind") == "sfx":
            sfx_path = project_dir / seg["path"]
            data, sr = sf.read(str(sfx_path))
            if getattr(data, "ndim", 1) > 1:
                data = data.mean(axis=1)
            pieces.append(data.astype(np.float32) * float(seg.get("gain", 0.7)))
            continue
        spoken = seg.get("text", "").strip()
        if not spoken:
            continue
        # Synthesize this speech run with the fast Kokoro worker.
        part = synthesize_kokoro(spoken, profile, audio_dir)
        data, sr = sf.read(str(part["path"]))
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        pieces.append(data.astype(np.float32))
        pieces.append(gap)
        # clean up the per-run temp file; we re-stitch into one output
        try:
            Path(part["path"]).unlink(missing_ok=True)
        except Exception:
            pass

    if not pieces:
        raise ValueError("Kokoro produced no audio.")
    audio = np.concatenate([p for p in pieces if len(p)])
    output_path = audio_dir / f"{uuid.uuid4().hex}.wav"
    sf.write(str(output_path), audio, SR)
    return {
        "path": output_path,
        "url": f"/audio/{output_path.name}",
        "mime_type": "audio/wav",
    }


def qwen3_instruction(profile: dict[str, Any], mood: str, delivery: dict[str, Any] | None) -> str:
    parts: list[str] = []
    base = str(profile.get("qwen3_instruct") or "").strip()
    if base:
        parts.append(base)
    mood_instructions = profile.get("mood_instructions") or {}
    if isinstance(mood_instructions, dict):
        mood_instruction = str(mood_instructions.get(mood) or "").strip()
        if mood_instruction:
            parts.append(mood_instruction)

    delivery = delivery or {}
    try:
        pace = float(delivery.get("pace") or 0)
    except (TypeError, ValueError):
        pace = 0
    try:
        energy = float(delivery.get("energy") or -1)
    except (TypeError, ValueError):
        energy = -1

    if pace >= 1.12:
        parts.append("Keep the pace quick and lively, like a real back-and-forth chat.")
    elif 0 < pace <= 0.92:
        parts.append("Slow slightly, but avoid long audiobook-style pauses.")
    if energy >= 0.7:
        parts.append("Add more vocal energy and expression without sounding theatrical.")
    elif 0 <= energy <= 0.25:
        parts.append("Keep the delivery intimate and low-energy.")

    return " ".join(parts).strip() or "Use a natural conversational voice."


def synthesize_qwen3_tts(text: str, profile: dict[str, Any], audio_dir: Path, mood: str, delivery: dict[str, Any] | None) -> dict[str, Any]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    clean_text = prepare_spoken_text(text)
    if not clean_text:
        raise ValueError("Text is required for voice generation.")
    output_path = audio_dir / f"{uuid.uuid4().hex}.wav"
    instruct = qwen3_instruction(profile, mood, delivery)
    result = _QWEN3.request(
        {
            "id": output_path.stem,
            "text": clean_text,
            "output": str(output_path),
            "model_id": str(profile.get("model_id") or "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"),
            "language": str(profile.get("language") or "English"),
            "speaker": str(profile.get("speaker") or "Serena"),
            "device": str(profile.get("device") or "auto"),
            "instruct": instruct,
        },
        timeout=int(profile.get("timeout_seconds") or 420),
    )
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "Qwen3-TTS synthesis failed."))
    if not output_path.exists():
        raise ValueError("Qwen3-TTS worker completed without creating audio.")
    return {
        "path": output_path,
        "url": f"/audio/{output_path.name}",
        "mime_type": "audio/wav",
        "instruct": instruct,
    }


def synthesize_qwen3_mlx(text: str, profile: dict[str, Any], audio_dir: Path, mood: str, delivery: dict[str, Any] | None) -> dict[str, Any]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    clean_text = prepare_spoken_text(text)
    if not clean_text:
        raise ValueError("Text is required for voice generation.")
    output_path = audio_dir / f"{uuid.uuid4().hex}.wav"
    instruct = qwen3_instruction(profile, mood, delivery)
    project_dir = Path.cwd()  # repo root (where the app is launched)
    ref_audio = str(profile.get("ref_audio") or "").strip()
    ref_audio_abs = str(project_dir / ref_audio) if ref_audio else ""
    if ref_audio_abs and not Path(ref_audio_abs).exists():
        raise ValueError(f"Voice reference clip not found: {ref_audio}")
    result = _QWEN3_MLX.request(
        {
            "id": output_path.stem,
            "text": clean_text,
            "output": str(output_path),
            "model_id": str(profile.get("model_id") or "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16"),
            "language": str(profile.get("language") or "English"),
            "lang_code": str(profile.get("lang_code") or "en"),
            "speaker": str(profile.get("speaker") or "Serena"),
            "instruct": instruct,
            "temperature": float(profile.get("temperature") or 0.7),
            "max_tokens": int(profile.get("max_tokens") or 1200),
            "ref_audio": ref_audio_abs,
            "ref_text": str(profile.get("ref_text") or ""),
        },
        timeout=int(profile.get("timeout_seconds") or 420),
    )
    if not result.get("ok"):
        raise ValueError(str(result.get("error") or "Qwen3-TTS MLX synthesis failed."))
    if not output_path.exists():
        raise ValueError("Qwen3-TTS MLX worker completed without creating audio.")
    return {
        "path": output_path,
        "url": f"/audio/{output_path.name}",
        "mime_type": "audio/wav",
        "instruct": instruct,
    }


def synthesize_qwen3_mlx_with_sfx(raw_text: str, profile: dict[str, Any], audio_dir: Path,
                                  mood: str, delivery: dict[str, Any] | None) -> dict[str, Any]:
    """Qwen3-TTS voice + cue-triggered SFX. Qwen3 has NO native non-verbal sounds,
    so we reuse the engine-agnostic cue parser: *action beats* become spliced SFX
    WAVs (gasp/moan/sigh) or are dropped, and the spoken runs are synthesized by
    Qwen3 (which keeps its instruct-based emotional tone). One WAV out. Qwen3 and the
    SFX assets are both 24 kHz, so no resampling is needed (with a safety guard)."""
    import numpy as np
    import soundfile as sf

    audio_dir.mkdir(parents=True, exist_ok=True)
    project_dir = Path.cwd()  # repo root (where the app is launched)
    SR = 24000
    gap = np.zeros(int(SR * 0.04), dtype=np.float32)

    segments = build_segments(raw_text, 0.0, 0.0, sfx_enabled=True)
    if not segments:
        raise ValueError("Nothing speakable in the reply after cue parsing.")

    def _read_mono(path: str) -> np.ndarray:
        data, sr = sf.read(str(path))
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        data = data.astype(np.float32)
        if sr != SR:  # safety: resample to match Qwen3 output if an asset differs
            idx = np.round(np.linspace(0, len(data) - 1, int(len(data) * SR / sr))).astype(int)
            data = data[idx]
        return data

    pieces: list = []
    last_instruct = ""
    for seg in segments:
        if seg.get("kind") == "sfx":
            audio = _read_mono(project_dir / seg["path"]) * float(seg.get("gain", 0.7))
            pieces.append(audio)
            continue
        spoken = seg.get("text", "").strip()
        if not spoken:
            continue
        # Synthesize this speech run with Qwen3 (keeps cloned voice + instruct tone).
        part = synthesize_qwen3_mlx(spoken, profile, audio_dir, mood, delivery)
        last_instruct = part.get("instruct", last_instruct)
        pieces.append(_read_mono(part["path"]))
        pieces.append(gap)
        try:
            Path(part["path"]).unlink(missing_ok=True)
        except Exception:
            pass

    if not pieces:
        raise ValueError("Qwen3-TTS produced no audio.")
    audio = np.concatenate([p for p in pieces if len(p)])
    output_path = audio_dir / f"{uuid.uuid4().hex}.wav"
    sf.write(str(output_path), audio, SR)
    return {
        "path": output_path,
        "url": f"/audio/{output_path.name}",
        "mime_type": "audio/wav",
        "instruct": last_instruct,
    }


def synthesize_macos_say(text: str, profile: dict[str, Any], audio_dir: Path) -> dict[str, Any]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_id = uuid.uuid4().hex
    text_path = audio_dir / f"{audio_id}.txt"
    aiff_path = audio_dir / f"{audio_id}.aiff"
    wav_path = audio_dir / f"{audio_id}.wav"

    text_path.write_text(text, encoding="utf-8")
    say_cmd = ["say", "-f", str(text_path), "-o", str(aiff_path), "-r", str(int(profile.get("rate") or 170))]
    voice = str(profile.get("voice") or "").strip()
    if voice:
        say_cmd[1:1] = ["-v", voice]

    subprocess.run(say_cmd, check=True, timeout=180)
    try:
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff_path), str(wav_path)],
            check=True,
            timeout=180,
        )
        output_path = wav_path
        mime_type = "audio/wav"
        aiff_path.unlink(missing_ok=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        output_path = aiff_path
        mime_type = "audio/aiff"
    finally:
        text_path.unlink(missing_ok=True)

    return {
        "path": output_path,
        "url": f"/audio/{output_path.name}",
        "mime_type": mime_type,
    }


def synthesize_tts(
    text: str,
    profile: dict[str, Any],
    audio_dir: Path,
    mood: str | None = None,
    delivery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    engine = str(profile.get("engine") or "macos_say")

    # Chatterbox parses *action beats* itself (some become SFX, some are dropped),
    # so it needs the RAW text. Mood is detected on raw text so cue words count.
    if engine == "chatterbox":
        if not (text or "").strip():
            raise ValueError("Text is required for voice generation.")
        detected_mood = mood or detect_mood(text)
        adjusted_profile = apply_delivery(apply_mood(profile, detected_mood), delivery)
        result = synthesize_chatterbox(text, adjusted_profile, audio_dir)
        result.update({
            "engine": engine,
            "mood": detected_mood,
            "exaggeration": adjusted_profile.get("exaggeration"),
            "cfg_weight": adjusted_profile.get("cfg_weight"),
            "voice": adjusted_profile.get("voice_ref", ""),
        })
        return result

    if engine == "qwen3_tts":
        if not (text or "").strip():
            raise ValueError("Text is required for voice generation.")
        detected_mood = mood or detect_mood(text)
        adjusted_profile = apply_delivery(apply_mood(profile, detected_mood), delivery)
        result = synthesize_qwen3_tts(text, adjusted_profile, audio_dir, detected_mood, delivery)
        result.update({
            "engine": engine,
            "mood": detected_mood,
            "voice": adjusted_profile.get("speaker", ""),
            "qwen3_instruct": result.get("instruct", ""),
        })
        return result

    if engine == "orpheus":
        # Orpheus needs RAW text: *action beats* become inline <gasp>/<sigh>/<groan>
        # tags voiced in its own voice; emotion is in the model, so no mood knob needed.
        if not (text or "").strip():
            raise ValueError("Text is required for voice generation.")
        detected_mood = mood or detect_mood(text)
        result = synthesize_orpheus(text, profile, audio_dir)
        result.update({
            "engine": engine,
            "mood": detected_mood,
            "voice": profile.get("voice", "tara"),
        })
        return result

    if engine == "qwen3_mlx":
        if not (text or "").strip():
            raise ValueError("Text is required for voice generation.")
        detected_mood = mood or detect_mood(text)
        adjusted_profile = apply_delivery(apply_mood(profile, detected_mood), delivery)
        # With SFX enabled, parse *action beats* (cue -> spliced gasp/moan WAV, or
        # dropped) since Qwen3 has no native non-verbal sounds; needs RAW text.
        if profile.get("sfx_enabled"):
            result = synthesize_qwen3_mlx_with_sfx(text, adjusted_profile, audio_dir, detected_mood, delivery)
        else:
            result = synthesize_qwen3_mlx(text, adjusted_profile, audio_dir, detected_mood, delivery)
        result.update({
            "engine": engine,
            "mood": detected_mood,
            "voice": adjusted_profile.get("speaker", ""),
            "qwen3_instruct": result.get("instruct", ""),
        })
        return result

    # Kokoro with SFX enabled also needs RAW text so *action beats* are parsed
    # (cue -> SFX, or dropped) instead of being read aloud. Mood from raw text.
    if engine == "kokoro" and profile.get("sfx_enabled"):
        if not (text or "").strip():
            raise ValueError("Text is required for voice generation.")
        detected_mood = mood or detect_mood(text)
        adjusted_profile = apply_delivery(apply_mood(profile, detected_mood), delivery)
        result = synthesize_kokoro_with_sfx(text, adjusted_profile, audio_dir)
        result.update({
            "engine": engine,
            "mood": detected_mood,
            "speed": adjusted_profile.get("speed"),
            "voice": adjusted_profile.get("voice", ""),
        })
        return result

    clean_text = prepare_spoken_text(text)
    if not clean_text:
        raise ValueError("Text is required for voice generation.")

    detected_mood = mood or detect_mood(clean_text)
    adjusted_profile = apply_delivery(apply_mood(profile, detected_mood), delivery)
    if engine == "kokoro":
        result = synthesize_kokoro(clean_text, adjusted_profile, audio_dir)
    elif engine == "macos_say":
        result = synthesize_macos_say(clean_text, adjusted_profile, audio_dir)
    else:
        raise ValueError(f"Voice engine '{engine}' is not installed yet.")
    result.update(
        {
            "engine": engine,
            "mood": detected_mood,
            "rate": adjusted_profile.get("rate"),
            "speed": adjusted_profile.get("speed"),
            "voice": adjusted_profile.get("voice", ""),
        }
    )
    return result
