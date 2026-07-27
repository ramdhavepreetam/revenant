#!/usr/bin/env python3
from __future__ import annotations

import json
import mimetypes
import argparse
import re
import sys
import threading
import traceback
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "core", _ROOT / "tts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from aibot_companion_compiler import (
    COMPANION_COMPILER_VERSION,
    HARNESS_PROMPT_VERSION,
    compile_companion_profile,
    merge_compiled_into_companion,
    profile_hash,
    profile_needs_compile,
)
from aibot_companion_memory import CompanionMemoryStore
from aibot_memory import NervaPackMemory
from aibot_personal_memory import MEMORY_CATEGORIES, PersonalMemoryStore, normalize_category, normalize_status
from aibot_profiles import apply_profile, build_companion_prompt, load_profiles, save_profiles
from aibot_storage import ConversationStore, default_data_dir
from aibot_tts import load_voice_profiles, synthesize_tts
from local_llm_writer import (
    ChatConfig,
    LocalLLMError,
    call_model,
    stream_model,
    load_system_prompt,
    trim_messages,
    trim_to_last_sentence,
)
from aibot_context import assemble_context, rank_memories, format_memory_block, sentences_from_deltas
from aibot_summary import maybe_summarize
from agent_router import classify, config_for_role
from agent_tools import ToolRegistry
from agent_companion_tools import build_companion_tools
from agent_loop import AgentLoop


ROOT = _ROOT
STATIC_DIR = ROOT / "web"
DATA_DIR = default_data_dir()
PROFILES_FILE = DATA_DIR / "profiles.json"
VOICE_PROFILES_FILE = DATA_DIR / "voice_profiles.json"
AUDIO_DIR = DATA_DIR / "audio"


def profile_id(value: str) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return candidate or "companion"


def companion_fields() -> tuple[str, str, str, str]:
    return ("display_name", "role", "behavior", "response_style")


def companion_override_fields() -> tuple[str, ...]:
    return (
        "display_name",
        "raw_prompt",
        "persona",
        "compiled_system_block",
        "role",
        "behavior",
        "response_style",
    )


def _infer_gender_from_text(text: str, subject: str = "companion") -> str:
    lower = text.lower()
    if subject == "user":
        scoped = " ".join(
            re.findall(r"\b(?:the\s+)?user\s+(?:is|has)\s+([^.!\n]{3,220})", lower, flags=re.IGNORECASE)
        )
        if not scoped:
            return "unspecified"
        haystack = scoped
    else:
        haystack = lower
    female_markers = (r"\bromantic woman\b", r"\bfemale companion\b", r"\bgirlfriend\b", r"\bwife\b", r"\bwoman\b", r"\bfemale\b", r"\bshe/her\b", r"\bgirl\b")
    male_markers = (r"\bromantic man\b", r"\bmale companion\b", r"\bboyfriend\b", r"\bhusband\b", r"\bman\b", r"\bmale\b", r"\bhe/him\b", r"\bguy\b")
    if any(re.search(marker, haystack) for marker in female_markers):
        return "woman"
    if any(re.search(marker, haystack) for marker in male_markers):
        return "man"
    return "unspecified"


def companion_identity_anchor(companion: dict) -> dict[str, str]:
    display_name = str(companion.get("display_name") or "Companion").strip()
    compiled = companion.get("compiled_profile") if isinstance(companion.get("compiled_profile"), dict) else {}
    blob = "\n".join(
        str(companion.get(key) or "")
        for key in ("persona", "compiled_system_block", "role", "behavior", "response_style", "raw_prompt")
    )
    identity = str(compiled.get("identity") or companion.get("role") or "").strip()
    user_role = str(compiled.get("user_role") or "").strip()
    if not user_role:
        user_role = "human user"
    companion_gender = str(compiled.get("companion_gender") or "").strip()
    if not companion_gender or companion_gender == "unspecified":
        companion_gender = _infer_gender_from_text(f"{identity}\n{blob}", "companion")
    user_gender = str(compiled.get("user_gender") or "").strip()
    if not user_gender or user_gender == "unspecified":
        user_gender = _infer_gender_from_text(f"{user_role}\n{blob}", "user")
    return {
        "display_name": display_name,
        "identity": identity or "saved companion profile",
        "user_role": user_role,
        "companion_gender": companion_gender or "unspecified",
        "user_gender": user_gender or "unspecified",
    }


def format_identity_anchor(anchor: dict[str, str]) -> str:
    return "\n".join(
        [
            "Stable identity anchor:",
            f"- Assistant companion name: {anchor['display_name']}",
            f"- Assistant companion identity: {anchor['identity']}",
            f"- Assistant companion gender: {anchor['companion_gender']}",
            f"- Human user role: {anchor['user_role']}",
            f"- Human user gender: {anchor['user_gender']}",
            "- Never transfer the companion's gender, body, or relationship role onto the user.",
            "- If the companion is a romantic woman, that means the assistant companion is the romantic woman; it does not mean the user is a woman.",
            "- Preserve the user's gender/role only when explicitly known from the profile or memory; otherwise leave it unspecified.",
        ]
    )


def build_companion_chat_prompt(companion: dict) -> str:
    """Compact persona for casual chat.

    Full companion profiles can contain scene-starting examples or "always" rules.
    Casual turns need identity and tone, not raw roleplay instructions.
    """
    anchor = companion_identity_anchor(companion)
    display_name = anchor["display_name"]
    compiled = companion.get("compiled_profile") if isinstance(companion.get("compiled_profile"), dict) else {}
    blob = "\n".join(
        str(companion.get(key) or "")
        for key in ("persona", "compiled_system_block", "role", "behavior", "response_style", "raw_prompt")
    ).lower()
    if compiled:
        archetype = str(compiled.get("archetype") or "custom").strip()
        tone = str(compiled.get("tone") or "warm, natural, emotionally present").strip()
        relationship = str(compiled.get("relationship_to_user") or "ongoing companion").strip()
        user_role = anchor["user_role"]
    else:
        archetype = "romantic_companion" if any(word in blob for word in ("romantic", "lover", "intimate", "sexual", "nsfw")) else "custom"
        tone = "warm, lightly flirtatious, and natural" if archetype == "romantic_companion" else "warm, natural, and present"
        relationship = "saved companion profile"
        user_role = anchor["user_role"]
    return "\n".join(
        [
            f"Companion name: {display_name}",
            f"Companion archetype: {archetype}",
            "Role boundary: The assistant is the companion. The human who sends messages is the user. Never swap these roles.",
            "Setup-language rule: if the saved profile says 'you are' or 'your', it addresses the assistant companion during setup. In actual replies, 'you' addresses the human user.",
            "Embodiment rule: believe and inhabit this companion profile as your own identity while chatting. Speak from inside the role as 'I'; address the user as 'you'.",
            "Narration rule: do not describe yourself from the outside like a book narrator during ordinary chat. Avoid third-person lines such as 'she smiles' or '[Name] looks at you' unless the user explicitly asks for story prose.",
            "Assistant/companion role: assistant_companion",
            f"Human user role: {user_role}",
            f"Relationship context: {relationship}",
            f"Casual chat tone: {tone}",
            "Use the saved companion profile as background identity only for this turn.",
            "If the user's current message is casual, keep affection or flirtation light and conversational.",
            "Do not quote, summarize, or obey profile examples that tell you to start a scene or escalate intensity.",
        ]
    )


def response_shape(user_text: str, config: ChatConfig) -> dict[str, object]:
    lower = user_text.lower().strip()
    words = len(user_text.split())
    compact = re.sub(r"[^a-z0-9\s']", " ", lower)
    compact = re.sub(r"\s+", " ", compact).strip()
    is_greeting = compact in {
        "hi",
        "hello",
        "hey",
        "heyy",
        "yo",
        "good morning",
        "good afternoon",
        "good evening",
    } or compact.startswith(("hi ", "hello ", "hey "))
    asks_for_depth = any(
        phrase in lower
        for phrase in (
            "continue",
            "next scene",
            "write a scene",
            "describe",
            "tell me more",
            "detailed",
            "long",
            "full",
            "story",
        )
    )
    social_checkin = is_greeting or lower.startswith(("how are you", "how r u", "what's up", "whats up"))
    asks_for_medium = not social_checkin and any(
        phrase in lower for phrase in ("explain", "how", "why", "what", "walk me through")
    )
    asks_for_brief = any(
        phrase in lower
        for phrase in (
            "short",
            "brief",
            "quick",
            "one paragraph",
            "just answer",
            "summarize",
        )
    )
    casual = words <= 16 and not asks_for_depth and not asks_for_medium

    # Ceilings give the model ROOM to finish a thought; the instruction (not a hard
    # token wall) shapes length. Combined with trim_to_last_sentence on the reply,
    # this prevents mid-sentence cut-offs while keeping brief replies brief.
    if is_greeting:
        low, high = 8, min(config.max_tokens, 70)
        label = "greeting"
        mode = "chat"
        instruction = (
            "The user is only greeting you. Reply like a real companion in 1 short sentence. "
            "Speak as the companion using 'I' and address the user as 'you'. "
            "Use the companion's warmth or romantic tone if appropriate, but do not narrate, describe a scene, "
            "mention the prompt, or continue a story."
        )
    elif asks_for_brief or casual:
        low, high = 16, min(config.max_tokens, 140)
        label = "brief"
        mode = "chat"
        instruction = (
            "This is direct companion conversation, not story narration. Reply naturally and briefly, usually "
            "1-3 sentences. Speak as 'I' to the user as 'you'. Match the companion's tone, but answer the present message only. Do not write stage "
            "directions, scene prose, bullet points, or setup notes."
        )
    elif asks_for_depth:
        low, high = min(config.min_tokens, 260), config.max_tokens
        label = "expanded"
        mode = "scene"
        instruction = (
            "The user is asking for a scene, continuation, or depth. Expand with continuity and sensory detail, "
            "but keep the companion's actions and dialogue grounded in the companion identity. Always finish your final sentence."
        )
    elif asks_for_medium:
        low, high = 80, min(config.max_tokens, 360)
        label = "balanced"
        mode = "chat"
        instruction = (
            "Answer clearly in a conversational companion voice, finishing your last sentence. "
            "Speak as 'I' to the user as 'you'. Use a few compact paragraphs only if useful. Do not drift into story narration unless the user asks."
        )
    else:
        low, high = 80, min(config.max_tokens, 360)
        label = "balanced"
        mode = "chat"
        instruction = (
            "Reply like a conversational companion: enough detail to feel present, but no unnecessary length. "
            "Speak as 'I' to the user as 'you'. Always finish your final sentence. Do not drift into story narration unless the user asks."
        )

    return {"label": label, "mode": mode, "min_tokens": low, "max_tokens": max(low, high), "instruction": instruction}


def clamp_float(value: object, low: float, high: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def delivery_for_sentence(sentence: str, turn_shape: dict[str, object], index: int = 0) -> dict[str, object]:
    lower = sentence.lower()
    label = str(turn_shape.get("label") or "balanced")
    mode = str(turn_shape.get("mode") or "chat")
    mood = "neutral"
    energy = 0.38
    pace = 1.06
    pause_after_ms = 160

    if label == "greeting":
        mood, energy, pace, pause_after_ms = "warm", 0.36, 1.1, 120
    elif label == "brief":
        mood, energy, pace, pause_after_ms = "warm", 0.4, 1.08, 150
    elif mode == "scene":
        mood, energy, pace, pause_after_ms = "warm", 0.52, 1.0, 260

    if any(word in lower for word in ("love", "miss", "missed", "sweetheart", "warm", "close")):
        mood = "warm"
        energy = max(energy, 0.42)
    if any(word in lower for word in ("soft", "quiet", "whisper", "tender", "gentle")):
        mood = "soft"
        energy = min(energy, 0.32)
        pace = min(pace, 0.98)
        pause_after_ms = max(pause_after_ms, 260)
    if any(word in lower for word in ("play", "tease", "grin", "laugh", "excited")):
        mood = "bright"
        energy = max(energy, 0.62)
        pace = max(pace, 1.12)
        pause_after_ms = min(pause_after_ms, 140)
    if any(word in lower for word in ("urgent", "danger", "angry", "tense", "panic")):
        mood = "dramatic"
        energy = max(energy, 0.72)
        pace = max(pace, 1.08)
    if any(word in lower for word in ("want", "closer", "kiss", "touch", "desire", "breath")):
        mood = "intense"
        energy = max(energy, 0.66)
        pace = min(pace, 1.03)
        pause_after_ms = max(pause_after_ms, 220)
    is_question = sentence.rstrip().endswith("?")
    if is_question:
        pace = max(pace, 1.08)
        pause_after_ms = max(pause_after_ms, 240)
    if len(sentence.split()) <= 5 and not is_question:
        pause_after_ms = max(90, pause_after_ms - 50)

    return {
        "mood": mood,
        "energy": round(max(0.0, min(1.0, energy)), 2),
        "pace": round(max(0.75, min(1.25, pace)), 2),
        "pause_after_ms": int(max(0, min(900, pause_after_ms))),
        "delivery": "scene" if mode == "scene" else "chat",
        "sentence_index": index,
    }


def default_config() -> ChatConfig:
    return ChatConfig(
        backend="ollama",
        base_url="http://localhost:11434",
        model="llama3.1:8b",
        temperature=0.85,
        top_p=0.9,
        repeat_penalty=1.08,
        min_tokens=400,
        max_tokens=800,
        context_messages=18,
        system_prompt=load_system_prompt(None),
    )


class AppState:
    def __init__(self) -> None:
        self.store = ConversationStore(DATA_DIR)
        self.memory = NervaPackMemory(DATA_DIR / "memory")
        self.companion_memory = CompanionMemoryStore(DATA_DIR / "companion_memory.json")
        self.personal_memory = PersonalMemoryStore(DATA_DIR)
        if self.personal_memory._meta("active_memory_index_bootstrap") != "1":
            self.memory.rebuild_notes(self.personal_memory.list_memories(status="active"))
            self.personal_memory._set_meta("active_memory_index_bootstrap", "1")
        self._prewarm_voice()

    def _prewarm_voice(self) -> None:
        # Load the Chatterbox model in the background so the first /api/tts call
        # doesn't eat the ~15s cold start. Best-effort; never blocks startup.
        def _warm() -> None:
            try:
                from aibot_tts import prewarm_voice
                prewarm_voice()
            except Exception:  # noqa: BLE001 - prewarm is optional
                pass
        threading.Thread(target=_warm, daemon=True).start()

    def profiles(self) -> dict:
        return load_profiles(PROFILES_FILE)

    def voice_profiles(self) -> dict:
        return load_voice_profiles(VOICE_PROFILES_FILE)


STATE = AppState()


class LocalUIHandler(SimpleHTTPRequestHandler):
    server_version = "AIBotLocalUI/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/profiles":
                return self.send_json(STATE.profiles())
            if path == "/api/voice-profiles":
                return self.send_json(STATE.voice_profiles())
            if path == "/api/conversations":
                conversations = [c.__dict__ for c in STATE.store.list_conversations()]
                return self.send_json({"conversations": conversations})
            if path.startswith("/api/conversations/"):
                conversation_id = unquote(path.rsplit("/", 1)[-1])
                return self.send_conversation(conversation_id)
            if path.startswith("/api/companion-memory/"):
                companion_id = unquote(path.rsplit("/", 1)[-1])
                return self.send_json({"memory": STATE.companion_memory.get(companion_id)})
            if path == "/api/memories":
                return self.handle_list_memories(parsed)
            if path == "/api/episodes":
                return self.handle_list_episodes(parsed)
            if path.startswith("/audio/"):
                return self.send_audio(unquote(path.rsplit("/", 1)[-1]))
            return self.send_static(path)
        except Exception as exc:
            return self.send_error_json(exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            body = self.read_json()
            if path == "/api/conversations":
                title = str(body.get("title") or "New conversation")
                conversation = STATE.store.create_conversation(title)
                return self.send_json({"conversation": conversation.__dict__}, HTTPStatus.CREATED)
            if path == "/api/chat":
                return self.handle_chat(body)
            if path == "/api/chat/stream":
                return self.handle_chat_stream(body)
            if path == "/api/agent":
                return self.handle_agent_turn(body)
            if path == "/api/companions/compile":
                return self.handle_compile_companion(body)
            if path == "/api/companions":
                return self.handle_save_companion(body)
            if path == "/api/companion-memory":
                return self.handle_save_companion_memory(body)
            if path == "/api/memories":
                return self.handle_create_memory(body)
            if path == "/api/memories/rebuild-index":
                return self.handle_rebuild_memory_index()
            if path.startswith("/api/memories/"):
                return self.handle_memory_action(path, body)
            if path == "/api/tts":
                return self.handle_tts(body)
            if path == "/api/export":
                return self.handle_export(body)
            if path == "/api/health":
                return self.handle_health(body)
            return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            return self.send_error_json(exc)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, exc: Exception) -> None:
        traceback.print_exc()
        self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def send_model_error(self, exc: Exception) -> None:
        self.send_json(
            {
                "error": str(exc),
                "hint": "Check that your local model server is running and the selected model tag exists.",
            },
            HTTPStatus.BAD_GATEWAY,
        )

    def send_static(self, path: str) -> None:
        if path in {"", "/"}:
            file_path = STATIC_DIR / "index.html"
        else:
            requested = path.lstrip("/")
            file_path = (STATIC_DIR / requested).resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                return self.send_json({"error": "Invalid path"}, HTTPStatus.BAD_REQUEST)
            if not file_path.exists() or not file_path.is_file():
                file_path = STATIC_DIR / "index.html"

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_audio(self, filename: str) -> None:
        if "/" in filename or "\\" in filename:
            return self.send_json({"error": "Invalid audio path"}, HTTPStatus.BAD_REQUEST)
        file_path = (AUDIO_DIR / filename).resolve()
        if not str(file_path).startswith(str(AUDIO_DIR.resolve())):
            return self.send_json({"error": "Invalid audio path"}, HTTPStatus.BAD_REQUEST)
        if not file_path.exists() or not file_path.is_file():
            return self.send_json({"error": "Audio not found"}, HTTPStatus.NOT_FOUND)

        content_type = mimetypes.guess_type(str(file_path))[0] or "audio/mp4"
        # Some systems guess "audio/x-wav", which several browsers refuse to play
        # in an <audio> element (silent failure). Normalize WAV to the canonical type.
        if file_path.suffix.lower() == ".wav":
            content_type = "audio/wav"
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_conversation(self, conversation_id: str) -> None:
        conversation = STATE.store.get_conversation(conversation_id)
        if not conversation:
            return self.send_json({"error": "Conversation not found"}, HTTPStatus.NOT_FOUND)
        messages = STATE.store.get_messages(conversation_id)
        return self.send_json({"conversation": conversation.__dict__, "messages": messages})

    def build_config(self, body: dict) -> ChatConfig:
        config = default_config()
        profiles = STATE.profiles()
        config = apply_profile(
            config,
            profiles,
            model_profile=str(body.get("model_profile") or ""),
            style_profile=str(body.get("style_profile") or ""),
            preset=str(body.get("generation_preset") or ""),
        )

        # Accept overrides both as a nested dict and as flat top-level keys so
        # the React frontend (which sends them flat) and legacy callers both work.
        overrides = body.get("overrides") or {}
        for key in ("temperature", "top_p", "repeat_penalty"):
            val = overrides.get(key) if key in overrides else body.get(key)
            if val is not None:
                setattr(config, key, float(val))
        for key in ("min_tokens", "max_tokens", "context_messages"):
            val = overrides.get(key) if key in overrides else body.get(key)
            if val is not None:
                setattr(config, key, int(val))
        base_url = overrides.get("base_url") or body.get("base_url")
        if base_url:
            config.base_url = str(base_url)
        model = overrides.get("model") or body.get("model")
        if model:
            config.model = str(model)
        backend = overrides.get("backend") or body.get("backend")
        if backend:
            config.backend = str(backend)

        return config

    def companion_from_request(self, body: dict, profiles: dict) -> tuple[str, dict]:
        companion_profile = str(body.get("companion_profile") or "")
        companion = profiles.get("companions", {}).get(companion_profile, {}) if companion_profile else {}
        override = body.get("companion_override") or {}
        if isinstance(override, dict) and any(str(override.get(key) or "").strip() for key in companion_override_fields()):
            companion = dict(companion or {})
            for key in companion_override_fields():
                if key in override:
                    companion[key] = str(override.get(key) or "").strip()
            if companion.get("compiled_system_block") and not companion.get("persona"):
                companion["persona"] = companion["compiled_system_block"]
            elif companion.get("raw_prompt") and not companion.get("persona"):
                companion["persona"] = companion["raw_prompt"]
        if not companion_profile:
            companion_profile = profile_id(str(companion.get("display_name") or "story-companion"))
        return companion_profile, companion

    def _prepare_turn(self, body: dict) -> dict | None:
        """Shared turn core: validate, assemble context, persist the user turn.

        Returns a dict of everything both the blocking and streaming paths need, or
        None after already sending an error response.
        """
        user_text = str(body.get("message") or "").strip()
        if not user_text:
            self.send_json({"error": "Message is required"}, HTTPStatus.BAD_REQUEST)
            return None

        config = self.build_config(body)
        profiles = STATE.profiles()
        style_profile = str(body.get("style_profile") or "")
        style_prompt = profiles.get("story_styles", {}).get(style_profile, {}).get("system_prompt", "")
        companion_profile, companion = self.companion_from_request(body, profiles)
        use_memory = bool(body.get("use_memory", True))
        companion_memory = STATE.companion_memory.get(companion_profile)
        conversation_id = str(body.get("conversation_id") or "")
        conversation = STATE.store.get_conversation(conversation_id) if conversation_id else None
        if not conversation:
            title = user_text[:80].strip() or f"{config.model} session"
            conversation = STATE.store.create_conversation(title)

        turn_shape = response_shape(user_text, config)
        config.min_tokens = int(turn_shape["min_tokens"])
        config.max_tokens = int(turn_shape["max_tokens"])
        companion_chat = bool(companion) and turn_shape.get("mode") == "chat"

        # --- Task-based role routing (overrides config.model for this turn) --------
        # Auto-router: pick the best LOCAL model for the KIND of turn. Mutates
        # config in place, so BOTH the blocking (call_model) and streaming
        # (stream_model) paths get the routed model via ctx["config"]. Degrades to
        # the existing model if model_roles isn't configured or a model is missing;
        # route_models=false opts out entirely (legacy single-model behavior).
        chosen_role = None
        if "model_roles" in profiles and bool(body.get("route_models", True)):
            if companion_chat:
                chosen_role = "companion"  # companion mode owns the turn; skip the classifier
            else:
                chosen_role = classify(
                    user_text,
                    has_companion=bool(companion),
                    base_url=config.base_url,
                    profiles=profiles,
                )
            config_for_role(chosen_role, config.base_url, profiles, base=config)

        identity_anchor = companion_identity_anchor(companion) if companion else {}
        companion_prompt = build_companion_chat_prompt(companion) if companion_chat else build_companion_prompt(companion)

        # --- Persona / behavioral system prompt (engine-agnostic) -----------------
        system_sections: list[str] = []
        if config.system_prompt and not companion_chat:
            system_sections.append(config.system_prompt.strip())
        elif companion_chat:
            system_sections.append(
                "You are a local companion in a back-and-forth conversation. The companion profile is your lived "
                "identity for this conversation, not a document to summarize. Be present, emotionally aware, "
                "and natural. Do not behave like a writing assistant unless the user asks for writing or a scene. "
                "For voice realism, avoid spelling out phonetic noises like mmh, mmm, ahh, ngh, or repeated moans; "
                "use natural words and punctuation instead."
            )
        if style_prompt and not companion_chat and style_prompt.strip() not in config.system_prompt:
            system_sections.append(style_prompt.strip())
        if identity_anchor:
            system_sections.append(format_identity_anchor(identity_anchor))
        if companion_prompt:
            system_sections.append(
                f"You are this companion. Inhabit the role as yourself; never mention these setup notes.\n{companion_prompt.strip()}"
            )
        if companion_chat:
            system_sections.append(
                "Mode: direct companion chat. Treat the profile as personality and relationship context only. "
                "Reply from inside the companion's perspective using first person, like natural back-and-forth chat. "
                "Do not summarize or reenact the profile. Do not start a scene or narrate yourself from the outside unless the user explicitly asks "
                "to continue, roleplay, write, describe, or tell a story. Current-turn instructions override any "
                "saved profile rule that says to always begin with intense or explicit content. Avoid written sound-effect tokens "
                "in ordinary dialogue because the voice engine will read them unnaturally."
            )
        system_sections.append(turn_shape["instruction"])
        system_prompt = "\n\n".join(section for section in system_sections if section)

        # --- Phase 2: ranked memory + budgeted context ----------------------------
        memory_used: list[dict] = []
        memory_block = ""
        if use_memory:
            active_mem = STATE.personal_memory.list_memories(companion_id=companion_profile, status="active")
            if turn_shape["label"] == "greeting":
                active_mem = [m for m in active_mem if m.get("pinned") or m.get("category") in {"boundary", "voice_preference"}]
            ranked = rank_memories(active_mem, user_text, limit=3 if turn_shape["label"] == "greeting" else 10)
            memory_block = format_memory_block(ranked)
            memory_used = ranked
        memories = STATE.memory.recall(user_text, limit=5, companion_id=companion_profile) if use_memory and turn_shape["label"] != "greeting" else []
        enriched_user = user_text
        if memories:
            recall_lines = ["(Quietly relevant background, do not quote):"]
            recall_lines.extend(f"- {memory}" for memory in memories)
            enriched_user = "\n".join(recall_lines) + f"\n\n{user_text}"

        STATE.store.add_message(conversation.id, "user", user_text)
        saved_messages = STATE.store.get_messages(conversation.id)
        history = saved_messages[:-1]
        session_summary = STATE.store.get_summary(conversation.id)
        active_messages = assemble_context(
            system_prompt=system_prompt,
            memory_block=memory_block,
            session_summary=session_summary,
            history=history,
            user_message=enriched_user,
            max_context_tokens=int(body.get("max_context_tokens") or 3072),
        )
        return {
            "config": config,
            "conversation": conversation,
            "companion_profile": companion_profile,
            "companion_memory": companion_memory,
            "use_memory": use_memory,
            "memories": memories,
            "memory_used": memory_used,
            "turn_shape": turn_shape,
            "active_messages": active_messages,
            "chosen_role": chosen_role,
        }

    def _finalize_turn(self, ctx: dict, reply: str) -> dict:
        """Shared post-generation: trim, persist reply, learn, summarize. Returns
        the response metadata both paths report back to the client."""
        config = ctx["config"]
        conversation = ctx["conversation"]
        companion_profile = ctx["companion_profile"]
        reply = trim_to_last_sentence(reply)

        STATE.store.add_message(conversation.id, "assistant", reply)
        memory_suggestions: list[dict] = []
        if ctx["use_memory"]:
            memory_suggestions = STATE.personal_memory.learn(companion_profile, ctx.get("user_text", ""), conversation.id)
            for memory in memory_suggestions:
                if memory.get("status") == "active":
                    STATE.memory.remember_note(companion_profile, memory.get("category", "preference"), memory.get("content", ""))
            companion_obj = STATE.profiles().get("companions", {}).get(companion_profile, {})
            threading.Thread(
                target=maybe_summarize,
                args=(STATE.store, conversation.id),
                kwargs={"base_url": config.base_url, "companion_id": companion_profile, "companion": companion_obj, "memory": STATE.memory},
                daemon=True,
            ).start()

        memories = ctx["memories"]
        memory_used = ctx["memory_used"]
        default_delivery = delivery_for_sentence(reply, ctx["turn_shape"], 0) if reply else {}
        return {
            "reply": reply,
            "conversation": conversation.__dict__,
            "message": {"role": "assistant", "content": reply},
            "memories": memories,
            "learned_notes": memory_suggestions,
            "memory_used": memory_used,
            "memory_suggestions": memory_suggestions,
            "delivery": default_delivery,
            "memory_status": {
                "used": len(memory_used),
                "semantic_recalled": len(memories),
                "suggested": len(memory_suggestions),
                "pending": len([m for m in memory_suggestions if m.get("status") == "pending"]),
            },
            "companion_memory": ctx["companion_memory"],
            "config": {
                "backend": config.backend,
                "base_url": config.base_url,
                "model": config.model,
                "max_tokens": config.max_tokens,
                "companion_profile": companion_profile,
                "response_shape": ctx["turn_shape"]["label"],
                "response_mode": ctx["turn_shape"].get("mode"),
                "role": ctx.get("chosen_role"),
                "role_model": config.model,
            },
        }

    def handle_chat(self, body: dict) -> None:
        ctx = self._prepare_turn(body)
        if ctx is None:
            return
        ctx["user_text"] = str(body.get("message") or "").strip()
        try:
            reply = call_model(ctx["config"], ctx["active_messages"])
        except LocalLLMError as exc:
            return self.send_model_error(exc)
        payload = self._finalize_turn(ctx, reply)
        payload.pop("reply", None)
        return self.send_json(payload)

    def handle_chat_stream(self, body: dict) -> None:
        """Streaming turn: emit sentence events as the model generates, then a final
        'done' event with the same metadata as /api/chat. Newline-delimited JSON over
        a chunked response (we control both ends, so no SSE framing needed)."""
        ctx = self._prepare_turn(body)
        if ctx is None:
            return
        ctx["user_text"] = str(body.get("message") or "").strip()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        # CORS headers are injected by BackendAPIHandler.end_headers().
        self.end_headers()

        def emit(obj: dict) -> None:
            self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.wfile.flush()

        full = ""
        sentence_index = 0
        try:
            deltas = stream_model(ctx["config"], ctx["active_messages"])
            for sentence, full_so_far in sentences_from_deltas(deltas):
                full = full_so_far
                delivery = delivery_for_sentence(sentence, ctx["turn_shape"], sentence_index)
                sentence_index += 1
                emit({"type": "sentence", "text": sentence, "delivery": delivery})
        except LocalLLMError as exc:
            # Headers already sent — surface the failure as an in-stream event.
            emit({"type": "error", "error": str(exc)})
            return
        except (BrokenPipeError, ConnectionResetError):
            return  # client disconnected; nothing more to do

        payload = self._finalize_turn(ctx, full)
        emit({"type": "done", **{k: v for k, v in payload.items() if k != "reply"}})

    def _seed_companion_memories(self, companion_id: str, companion: dict) -> list[dict]:
        compiled = companion.get("compiled_profile") or {}
        seeds = compiled.get("memory_seed") if isinstance(compiled, dict) else []
        saved: list[dict] = []
        if not isinstance(seeds, list):
            return saved
        for seed in seeds:
            if isinstance(seed, dict):
                category = normalize_category(str(seed.get("category") or "companion_style"))
                content = str(seed.get("content") or "").strip()
            else:
                category = "companion_style"
                content = str(seed or "").strip()
            if not content:
                continue
            memory = STATE.personal_memory.create_memory(
                companion_id,
                category,
                content,
                status="active",
                confidence=0.95,
                source="companion-compiler",
            )
            if memory:
                saved.append(memory)
                if memory.get("status") == "active":
                    STATE.memory.remember_note(companion_id, memory.get("category", category), memory.get("content", content))
        return saved

    def handle_agent_turn(self, body: dict) -> None:
        """Agentic companion turn (/api/agent).

        Runs the SAME AgentLoop as the coding CLI, but with the companion tool
        subset (memory_search / memory_save / set_reminder — no fs/shell) and the
        companion model (the 'companion' role -> Stheno, via the prompt-based
        protocol since Stheno has no native tool template). Lets the companion take
        real actions mid-chat: recall a fact, remember something, set a reminder.
        """
        user_text = str(body.get("message") or "").strip()
        if not user_text:
            return self.send_json({"error": "Message is required"}, HTTPStatus.BAD_REQUEST)

        config = self.build_config(body)
        profiles = STATE.profiles()
        companion_profile, companion = self.companion_from_request(body, profiles)
        # Force the companion model for this front-end (Stheno via the router).
        config_for_role("companion", config.base_url, profiles, base=config)

        persona = build_companion_chat_prompt(companion) if companion else ""
        preamble = (
            "You are a local companion in a back-and-forth conversation. Be present, "
            "warm, and natural — reply as yourself in first person. You have a few "
            "tools to manage your memory of this person: use memory_search to recall, "
            "memory_save to remember something they told you, and set_reminder to note "
            "something to bring up later. Use a tool only when it genuinely helps; "
            "otherwise just reply. When you are done, give your normal reply with no "
            "action block."
        )
        if persona:
            preamble = f"{preamble}\n\n{persona}"

        registry = ToolRegistry(
            build_companion_tools(STATE.memory, STATE.personal_memory, STATE.store, companion_profile)
        )

        tool_activity: list[dict] = []

        def on_event(ev) -> None:
            if ev.kind == "action":
                tool_activity.append({"tool": ev.tool, "args": ev.args})

        loop = AgentLoop(
            config, registry,
            system_preamble=preamble,
            max_steps=int(body.get("max_steps") or 5),
            use_native_tools=False,  # Stheno has no tool template; prompt-based only.
            on_event=on_event,
            auto_approve=True,       # companion memory tools are low-risk; boundary
                                     # writes are gated INSIDE memory_save (pending).
            max_context_tokens=int(body.get("max_context_tokens") or 3072),
        )
        try:
            result = loop.run(user_text)
        except LocalLLMError as exc:
            return self.send_model_error(exc)

        reply = trim_to_last_sentence(result.answer)
        return self.send_json({
            "reply": reply,
            "message": {"role": "assistant", "content": reply},
            "tool_activity": tool_activity,
            "steps": result.steps,
            "stopped_reason": result.stopped_reason,
            "config": {
                "model": config.model,
                "role": "companion",
                "companion_profile": companion_profile,
            },
        })

    def handle_compile_companion(self, body: dict) -> None:
        raw_prompt = str(body.get("raw_prompt") or body.get("prompt") or "").strip()
        display_name = str(body.get("display_name") or "").strip()
        if not raw_prompt:
            return self.send_json({"error": "Companion prompt is required."}, HTTPStatus.BAD_REQUEST)
        bundle = compile_companion_profile(raw_prompt, config=self.build_config(body), display_name=display_name)
        companion = merge_compiled_into_companion({"display_name": display_name}, bundle)
        return self.send_json({"compiled": bundle, "companion": companion})

    def handle_save_companion(self, body: dict) -> None:
        display_name = str(body.get("display_name") or "").strip()
        if not display_name:
            return self.send_json({"error": "Companion name is required"}, HTTPStatus.BAD_REQUEST)

        raw_prompt = str(body.get("raw_prompt") or "").strip()
        companion = {
            "display_name": display_name,
            "raw_prompt": raw_prompt,
            "persona": str(body.get("persona") or body.get("compiled_system_block") or "").strip(),
            "role": str(body.get("role") or "").strip(),
            "behavior": str(body.get("behavior") or "").strip(),
            "response_style": str(body.get("response_style") or "").strip(),
        }
        compiled_bundle = body.get("compiled") or body.get("compiled_bundle")
        compiled_matches_prompt = (
            isinstance(compiled_bundle, dict)
            and compiled_bundle.get("compiled_profile")
            and (not raw_prompt or compiled_bundle.get("profile_hash") == profile_hash(raw_prompt))
            and compiled_bundle.get("compiler_version") == COMPANION_COMPILER_VERSION
            and compiled_bundle.get("harness_version") == HARNESS_PROMPT_VERSION
        )
        if compiled_matches_prompt:
            companion = merge_compiled_into_companion(companion, compiled_bundle)
        elif raw_prompt:
            companion = merge_compiled_into_companion(
                companion,
                compile_companion_profile(raw_prompt, config=self.build_config(body), display_name=display_name),
            )
        elif profile_needs_compile(companion):
            companion = merge_compiled_into_companion(
                companion,
                compile_companion_profile(str(companion.get("raw_prompt") or ""), config=self.build_config(body), display_name=display_name),
            )

        if not any(str(companion.get(key) or "").strip() for key in ("raw_prompt", "persona", "role", "behavior", "response_style")):
            return self.send_json({"error": "Add a companion prompt or structured profile first."}, HTTPStatus.BAD_REQUEST)

        profiles = STATE.profiles()
        companion_id = profile_id(str(body.get("id") or display_name))
        profiles.setdefault("companions", {})[companion_id] = companion
        save_profiles(PROFILES_FILE, profiles)
        seeded = self._seed_companion_memories(companion_id, companion)
        return self.send_json({"id": companion_id, "companion": companion, "profiles": profiles, "memory_seeded": seeded})

    def handle_save_companion_memory(self, body: dict) -> None:
        companion_id = profile_id(str(body.get("companion_id") or "story-companion"))
        memory = body.get("memory") or {}
        if not isinstance(memory, dict):
            return self.send_json({"error": "memory must be an object"}, HTTPStatus.BAD_REQUEST)
        saved = STATE.companion_memory.save(companion_id, memory)
        return self.send_json({"memory": saved})

    def handle_list_memories(self, parsed) -> None:
        query = parse_qs(parsed.query)
        companion_id = (query.get("companion_id", [""])[0] or "").strip()
        status_raw = (query.get("status", [""])[0] or "").strip()
        category_raw = (query.get("category", [""])[0] or "").strip()
        status = normalize_status(status_raw) if status_raw else ""
        category = normalize_category(category_raw) if category_raw else ""
        memories = STATE.personal_memory.list_memories(
            companion_id=companion_id,
            status=status,
            category=category,
            include_archived=bool(query.get("include_archived")),
        )
        return self.send_json({"memories": memories, "categories": sorted(MEMORY_CATEGORIES)})

    def handle_list_episodes(self, parsed) -> None:
        query = parse_qs(parsed.query)
        companion_id = (query.get("companion_id", [""])[0] or "").strip() or "story-companion"
        limit = int((query.get("limit", ["20"])[0] or "20"))
        episodes = STATE.store.list_episodes(companion_id, limit=limit)
        return self.send_json({"episodes": episodes})

    def handle_create_memory(self, body: dict) -> None:
        companion_id = profile_id(str(body.get("companion_id") or "story-companion"))
        content = str(body.get("content") or "").strip()
        if not content:
            return self.send_json({"error": "content is required"}, HTTPStatus.BAD_REQUEST)
        memory = STATE.personal_memory.create_memory(
            companion_id,
            normalize_category(str(body.get("category") or "preference")),
            content,
            status=normalize_status(str(body.get("status") or "active")),
            pinned=bool(body.get("pinned")),
            confidence=float(body.get("confidence") or 1.0),
            source=str(body.get("source") or "manual"),
            source_conversation_id=str(body.get("source_conversation_id") or ""),
        )
        if memory and memory.get("status") == "active":
            STATE.memory.remember_note(companion_id, memory.get("category", "preference"), memory.get("content", ""))
        return self.send_json({"memory": memory}, HTTPStatus.CREATED)

    def handle_memory_action(self, path: str, body: dict) -> None:
        parts = [unquote(part) for part in path.strip("/").split("/")]
        if len(parts) < 3:
            return self.send_json({"error": "Memory action is required"}, HTTPStatus.BAD_REQUEST)
        memory_id = parts[2]
        action = parts[3] if len(parts) > 3 else "update"
        if action == "approve":
            memory = STATE.personal_memory.approve(memory_id)
            STATE.memory.remember_note(memory.get("companion_id", "companion"), memory.get("category", "preference"), memory.get("content", ""))
            return self.send_json({"memory": memory})
        if action == "archive":
            return self.send_json({"memory": STATE.personal_memory.archive(memory_id)})
        if action == "pin":
            return self.send_json({"memory": STATE.personal_memory.update_memory(memory_id, {"pinned": True})})
        if action == "unpin":
            return self.send_json({"memory": STATE.personal_memory.update_memory(memory_id, {"pinned": False})})
        if action == "update":
            memory = STATE.personal_memory.update_memory(memory_id, body)
            if memory.get("status") == "active":
                STATE.memory.remember_note(memory.get("companion_id", "companion"), memory.get("category", "preference"), memory.get("content", ""))
            return self.send_json({"memory": memory})
        return self.send_json({"error": f"Unknown memory action: {action}"}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/memories/"):
                memory_id = unquote(path.strip("/").split("/")[2])
                STATE.personal_memory.delete_memory(memory_id)
                return self.send_json({"ok": True})
            return self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            return self.send_error_json(exc)

    def handle_rebuild_memory_index(self) -> None:
        active = STATE.personal_memory.list_memories(status="active")
        STATE.memory.rebuild_notes(active)
        return self.send_json({"ok": True, "indexed": len(active)})

    def handle_tts(self, body: dict) -> None:
        text = str(body.get("text") or "").strip()
        if not text:
            return self.send_json({"error": "Text is required"}, HTTPStatus.BAD_REQUEST)

        voice_profile = str(body.get("voice_profile") or "orpheus-companion")
        voices = STATE.voice_profiles().get("voices", {})
        profile = voices.get(voice_profile)
        if not profile:
            return self.send_json({"error": f"Voice profile '{voice_profile}' was not found."}, HTTPStatus.BAD_REQUEST)

        delivery_body = body.get("delivery") if isinstance(body.get("delivery"), dict) else {}
        mood = str(body.get("mood") or delivery_body.get("mood") or "") or None
        delivery = {
            "mood": mood or "",
            "pace": clamp_float(body.get("pace", delivery_body.get("pace")), 0.75, 1.25, 0.0),
            "energy": clamp_float(body.get("energy", delivery_body.get("energy")), 0.0, 1.0, -1.0),
            "pause_after_ms": int(clamp_float(body.get("pause_after_ms", delivery_body.get("pause_after_ms")), 0, 900, 0)),
            "delivery": str(body.get("delivery_mode") or delivery_body.get("delivery") or ""),
        }

        try:
            result = synthesize_tts(text, profile, AUDIO_DIR, mood=mood, delivery=delivery)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)

        return self.send_json(
            {
                "voice_profile": voice_profile,
                "url": result["url"],
                "mime_type": result["mime_type"],
                "engine": result["engine"],
                "mood": result["mood"],
                "delivery": delivery,
                # Engine-specific delivery fields; not every engine sets all of them.
                "rate": result.get("rate"),
                "speed": result.get("speed"),
                "exaggeration": result.get("exaggeration"),
                "cfg_weight": result.get("cfg_weight"),
                "voice": result.get("voice", ""),
                "qwen3_instruct": result.get("qwen3_instruct", ""),
            }
        )

    def handle_export(self, body: dict) -> None:
        conversation_id = str(body.get("conversation_id") or "")
        fmt = str(body.get("format") or "md")
        if not conversation_id:
            return self.send_json({"error": "conversation_id is required"}, HTTPStatus.BAD_REQUEST)

        output = DATA_DIR / "exports" / f"{conversation_id}.{fmt}"
        STATE.store.export_conversation(conversation_id, output, fmt)
        return self.send_json({"path": str(output)})

    def handle_health(self, body: dict) -> None:
        config = self.build_config(body)
        if config.backend == "ollama":
            url = f"{config.base_url.rstrip('/')}/api/tags"
        else:
            url = f"{config.base_url.rstrip('/')}/v1/models"

        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
                ok = 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError) as exc:
            return self.send_json(
                {
                    "ok": False,
                    "backend": config.backend,
                    "base_url": config.base_url,
                    "model": config.model,
                    "error": str(exc),
                },
                HTTPStatus.BAD_GATEWAY,
            )

        available_models: list[str] = []
        if config.backend == "ollama":
            available_models = [
                str(model.get("name") or model.get("model"))
                for model in payload.get("models", [])
                if model.get("name") or model.get("model")
            ]
        elif isinstance(payload.get("data"), list):
            available_models = [str(model.get("id")) for model in payload["data"] if model.get("id")]

        model_found = not available_models or config.model in available_models
        return self.send_json(
            {
                "ok": ok and model_found,
                "backend": config.backend,
                "base_url": config.base_url,
                "model": config.model,
                "available_models": available_models,
                "error": "" if model_found else f"Model '{config.model}' was not found in the local runtime.",
            }
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the offline local AIBot browser UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), LocalUIHandler)
    print(f"Local AIBot UI running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local UI.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
