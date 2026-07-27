"""Task-based multi-model routing for the local agent harness.

Instead of one model doing everything, route each turn to the best LOCAL Ollama
model for the KIND of work:

    code       -> a code-trained, tool-capable model (qwen2.5:7b)
    language   -> a stronger general model for discussion/reasoning (qwen2.5:14b)
    companion  -> the persona / roleplay model (Stheno-8B)
    summary    -> a small factual model (gemma) -- see aibot_summary.py
    router     -> the tiny classifier itself (qwen2.5:7b, constrained output)

Roles map to model-profile names via profiles["model_roles"], which in turn
resolve to {backend, base_url, model} via profiles["models"] -- so endpoint data
is never duplicated (see core/aibot_profiles.py).

Design mirrors aibot_summary.py: build a task-specific ChatConfig and call the
existing local `call_model`. Everything stays offline (ADR 0001) -- no cloud SDK.

Reusable by BOTH the web companion path (apps/web_app.py) and the future coding
agent loop (ADR 0003): this module imports ONLY local_llm_writer, never web_app.
"""
from __future__ import annotations

import re

from nerva_core.local_llm_writer import ChatConfig, call_model, LocalLLMError

# Roles the auto-router may CHOOSE among for a plain chat turn.
ROUTER_ROLES = ("code", "language", "companion")
# Role name used when classification fails or is ambiguous. Overridable via the
# profiles' model_roles["fallback"] entry; this is the hard default.
DEFAULT_FALLBACK = "language"

DEFAULT_BASE_URL = "http://localhost:11434"

# Per-role generation defaults, used only when building a FRESH ChatConfig
# (base=None) -- e.g. the coding agent loop. When mutating an existing turn
# config (base=<ChatConfig>), only model/backend/base_url are touched so the
# turn's already-computed tokens/temperature/system_prompt are preserved.
_ROLE_GEN_DEFAULTS: dict[str, dict[str, float | int]] = {
    "code": {"temperature": 0.2, "top_p": 0.9, "repeat_penalty": 1.05,
             "min_tokens": 64, "max_tokens": 1024, "context_messages": 24},
    "language": {"temperature": 0.5, "top_p": 0.9, "repeat_penalty": 1.08,
                 "min_tokens": 80, "max_tokens": 800, "context_messages": 18},
    "companion": {"temperature": 0.85, "top_p": 0.9, "repeat_penalty": 1.08,
                  "min_tokens": 60, "max_tokens": 520, "context_messages": 18},
    "summary": {"temperature": 0.3, "top_p": 0.9, "repeat_penalty": 1.05,
                "min_tokens": 60, "max_tokens": 300, "context_messages": 64},
}
_FALLBACK_GEN = _ROLE_GEN_DEFAULTS["language"]


def _resolve_model_data(role: str, profiles: dict) -> dict | None:
    """role -> profiles['model_roles'][role] (a profile name) -> profiles['models'][name].

    Returns the {backend, base_url, model} dict, or None if the role isn't mapped
    or its model profile is missing.
    """
    roles = profiles.get("model_roles") if isinstance(profiles, dict) else None
    if not isinstance(roles, dict):
        return None
    profile_name = roles.get(role)
    if not profile_name:
        return None
    model_data = profiles.get("models", {}).get(profile_name)
    return model_data if isinstance(model_data, dict) else None


def config_for_role(
    role: str,
    base_url: str,
    profiles: dict,
    *,
    base: ChatConfig | None = None,
) -> ChatConfig | None:
    """Resolve a role to a ChatConfig.

    - base=<ChatConfig>: MUTATE IN PLACE only backend/base_url/model and return it.
      Preserves the caller's tokens/temperature/system_prompt (used per web turn).
    - base=None: build a FRESH ChatConfig seeded with per-role generation defaults
      (used by the coding agent loop). Returns None if the role can't be resolved.

    Never raises. On an unresolved role: returns `base` unchanged (or None), so
    callers transparently fall back to their existing model.
    """
    model_data = _resolve_model_data(role, profiles)

    if base is not None:
        if model_data:
            base.backend = model_data.get("backend", base.backend)
            base.base_url = model_data.get("base_url", base.base_url)
            base.model = model_data.get("model", base.model)
        return base

    if not model_data:
        return None

    gen = _ROLE_GEN_DEFAULTS.get(role, _FALLBACK_GEN)
    return ChatConfig(
        backend=model_data.get("backend", "ollama"),
        base_url=model_data.get("base_url", base_url),
        model=model_data.get("model", ""),
        temperature=float(gen["temperature"]),
        top_p=float(gen["top_p"]),
        repeat_penalty=float(gen["repeat_penalty"]),
        min_tokens=int(gen["min_tokens"]),
        max_tokens=int(gen["max_tokens"]),
        context_messages=int(gen["context_messages"]),
        system_prompt="",
    )


def _router_config(base_url: str, profiles: dict) -> ChatConfig | None:
    """ChatConfig for the classifier: the 'router' role model, constrained to emit
    a single word. Returns None if the router model can't be resolved."""
    model_data = _resolve_model_data("router", profiles)
    if not model_data:
        return None
    return ChatConfig(
        backend=model_data.get("backend", "ollama"),
        base_url=model_data.get("base_url", base_url),
        model=model_data.get("model", ""),
        temperature=0.0,
        top_p=0.9,
        repeat_penalty=1.0,
        min_tokens=1,
        max_tokens=4,
        context_messages=1,
        system_prompt="",
    )


# --- Heuristic pre-filter --------------------------------------------------

# Obvious coding signals: keywords, common file extensions, code fences.
_CODE_WORD_RE = re.compile(
    r"\b(def|class|import|traceback|stack ?trace|refactor|compile|debug|"
    r"function|variable|regex|api|endpoint|null ?pointer|segfault|exception|"
    r"git|commit|merge|rebase|pytest|unit ?test|npm|pip install)\b"
)
_CODE_PATH_RE = re.compile(
    r"\S+\.(py|js|ts|tsx|jsx|rs|go|java|c|cpp|h|rb|php|sh|json|ya?ml|toml|md|html|css|sql)\b"
)
_CODE_FENCE_RE = re.compile(r"```")
_CODE_WRITE_RE = re.compile(
    r"\b(write|fix|add|implement|create|generate|optimi[sz]e|refactor)\b.{0,30}"
    r"\b(code|function|method|class|script|test|bug|error|module|route|endpoint)\b"
)

# Obvious discussion/reasoning signals.
_LANG_RE = re.compile(
    r"\b(explain|why|what is|what's the|difference between|reason about|"
    r"discuss|your opinion|what do you think|pros and cons|compare|thoughts on)\b"
)


def _heuristic_role(user_text: str, *, has_companion: bool) -> str | None:
    """Fast, zero-cost classification. Returns a confident role, or None to defer
    to the LLM classifier.

    Companion turns short-circuit to 'companion' unless the text is an obvious
    code request (so a companion asking to write code still gets code) -- but the
    web path forces 'companion' before ever calling this; this guard is for the
    reusable/agent-loop path.
    """
    lower = user_text.lower()
    compact = re.sub(r"\s+", " ", lower).strip()

    is_code = bool(
        _CODE_FENCE_RE.search(user_text)
        or _CODE_PATH_RE.search(lower)
        or _CODE_WORD_RE.search(compact)
        or _CODE_WRITE_RE.search(compact)
    )

    if has_companion:
        return "code" if is_code else "companion"

    if is_code:
        return "code"
    if _LANG_RE.search(compact):
        return "language"
    return None


def _normalize_role(raw: str) -> str:
    """Reduce a model's reply to a single role token, or the fallback."""
    if not raw:
        return DEFAULT_FALLBACK
    token = ""
    for word in re.findall(r"[a-z]+", raw.lower()):
        if word in ROUTER_ROLES:
            token = word
            break
    return token or DEFAULT_FALLBACK


def _fallback_role(profiles: dict) -> str:
    roles = profiles.get("model_roles") if isinstance(profiles, dict) else None
    if isinstance(roles, dict):
        fb = roles.get("fallback")
        if fb in ROUTER_ROLES:
            return fb
    return DEFAULT_FALLBACK


def classify(
    user_text: str,
    *,
    has_companion: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    profiles: dict | None = None,
) -> str:
    """Classify a turn into a role. Cheap heuristic first; a single constrained
    LLM call only for genuinely ambiguous turns. Any failure degrades to the
    fallback role -- this never raises and never breaks a chat turn.
    """
    profiles = profiles or {}
    fallback = _fallback_role(profiles)

    role = _heuristic_role(user_text, has_companion=has_companion)
    if role is not None:
        return role

    config = _router_config(base_url, profiles)
    if config is None:
        return fallback

    prompt = [
        {
            "role": "system",
            "content": (
                "You are a routing classifier. Decide what KIND of turn the user "
                "message is and reply with EXACTLY ONE word, no punctuation, no "
                "explanation.\n"
                "- code      : the user wants you to WRITE, EDIT, DEBUG, or read actual "
                "code/source files, run commands, or fix a specific program error. "
                "There is real code, a file, a stack trace, or a concrete implementation task.\n"
                "- language  : discussion, explanation, reasoning, brainstorming, advice, "
                "or thinking through ideas or trade-offs. This includes talking ABOUT software, "
                "architecture, or design at a conceptual level when NO code is being written.\n"
                "- companion : casual, emotional, personal, or roleplay conversation.\n"
                "When unsure between code and language, choose language unless the user is "
                "clearly asking you to produce or change actual code.\n"
                "Examples:\n"
                "'write a function to parse json' -> code\n"
                "'fix the crash in server.py' -> code\n"
                "'help me think through the trade-offs of my design' -> language\n"
                "'what's the difference between a queue and a stack' -> language\n"
                "'i had a rough day' -> companion\n"
                "Reply with one word only: code, language, or companion."
            ),
        },
        {"role": "user", "content": user_text[:400]},
    ]
    try:
        raw = call_model(config, prompt)
    except (LocalLLMError, Exception):  # noqa: BLE001 - never break the turn
        return fallback
    role = _normalize_role(raw)
    # If the model returned something outside the set, _normalize_role already
    # returned DEFAULT_FALLBACK; prefer the profile-configured fallback instead.
    return role if role in ROUTER_ROLES else fallback


def warm_role(role: str, base_url: str, profiles: dict) -> None:
    """OPTIONAL: fire-and-forget preload of a role's model so a subsequent real
    call doesn't pay the ~1-3s Ollama load. Best-effort; swallows all errors.

    Intended to be run on a daemon thread (like the summary/prewarm threads in
    web_app.py). Sends a 1-token request that triggers Ollama to resident-load
    the model, then returns.
    """
    config = config_for_role(role, base_url, profiles, base=None)
    if config is None:
        return
    config.max_tokens = 1
    config.min_tokens = 1
    try:
        call_model(config, [{"role": "user", "content": "ok"}])
    except (LocalLLMError, Exception):  # noqa: BLE001
        pass
