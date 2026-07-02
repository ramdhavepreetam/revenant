const state = {
  profiles: null,
  voiceProfiles: null,
  memories: [],
  memoryCategories: [],
  episodes: [],
  activeMemoryTab: "profile",
  compiledCompanion: null,
  conversations: [],
  activeConversationId: "",
  messages: [],
  audioCache: new Map(),
  currentAudio: null,
  voiceAbort: null,
  voiceControllers: new Set(),
  voiceQueue: [],
  voicePlaying: false,
  voiceRun: 0,
  busy: false,
};

const apiBaseUrl = (window.AIBOT_API_BASE_URL || localStorage.getItem("aibot.apiBaseUrl") || "http://127.0.0.1:8766").replace(/\/$/, "");
window.AIBOT_ACTIVE_API_BASE_URL = apiBaseUrl;

const els = {
  conversationList: document.querySelector("#conversationList"),
  conversationTitle: document.querySelector("#conversationTitle"),
  messages: document.querySelector("#messages"),
  modelProfile: document.querySelector("#modelProfile"),
  styleProfile: document.querySelector("#styleProfile"),
  companionProfile: document.querySelector("#companionProfile"),
  companionName: document.querySelector("#companionName"),
  companionPrompt: document.querySelector("#companionPrompt"),
  compileCompanionButton: document.querySelector("#compileCompanionButton"),
  companionCompileStatus: document.querySelector("#companionCompileStatus"),
  compiledArchetype: document.querySelector("#compiledArchetype"),
  compiledTone: document.querySelector("#compiledTone"),
  compiledVoice: document.querySelector("#compiledVoice"),
  companionRole: document.querySelector("#companionRole"),
  companionBehavior: document.querySelector("#companionBehavior"),
  companionResponse: document.querySelector("#companionResponse"),
  memoryProfileName: document.querySelector("#memoryProfileName"),
  memoryUserName: document.querySelector("#memoryUserName"),
  memoryCompanionName: document.querySelector("#memoryCompanionName"),
  memoryRelationship: document.querySelector("#memoryRelationship"),
  memoryCurrentDynamic: document.querySelector("#memoryCurrentDynamic"),
  memoryTonePreferences: document.querySelector("#memoryTonePreferences"),
  memoryImportantFacts: document.querySelector("#memoryImportantFacts"),
  memoryBoundaries: document.querySelector("#memoryBoundaries"),
  memoryLearnedNotes: document.querySelector("#memoryLearnedNotes"),
  personalMemoryCount: document.querySelector("#personalMemoryCount"),
  personalMemoryList: document.querySelector("#personalMemoryList"),
  memoryTabs: document.querySelector("#memoryTabs"),
  memoryCreateCategory: document.querySelector("#memoryCreateCategory"),
  memoryCreateContent: document.querySelector("#memoryCreateContent"),
  memoryCreatePinned: document.querySelector("#memoryCreatePinned"),
  addMemoryButton: document.querySelector("#addMemoryButton"),
  rebuildMemoryButton: document.querySelector("#rebuildMemoryButton"),
  generationPreset: document.querySelector("#generationPreset"),
  backend: document.querySelector("#backend"),
  baseUrl: document.querySelector("#baseUrl"),
  modelTag: document.querySelector("#modelTag"),
  contextMessages: document.querySelector("#contextMessages"),
  maxTokens: document.querySelector("#maxTokens"),
  temperature: document.querySelector("#temperature"),
  messageInput: document.querySelector("#messageInput"),
  composer: document.querySelector("#composer"),
  sendButton: document.querySelector("#sendButton"),
  newConversationButton: document.querySelector("#newConversationButton"),
  refreshButton: document.querySelector("#refreshButton"),
  exportButton: document.querySelector("#exportButton"),
  exportFormat: document.querySelector("#exportFormat"),
  checkRuntimeButton: document.querySelector("#checkRuntimeButton"),
  saveCompanionButton: document.querySelector("#saveCompanionButton"),
  saveMemoryButton: document.querySelector("#saveMemoryButton"),
  memoryStatus: document.querySelector("#memoryStatus"),
  useMemory: document.querySelector("#useMemory"),
  voiceProfile: document.querySelector("#voiceProfile"),
  autoSpeak: document.querySelector("#autoSpeak"),
  stopVoiceButton: document.querySelector("#stopVoiceButton"),
  voiceStatus: document.querySelector("#voiceStatus"),
  activeModel: document.querySelector("#activeModel"),
  connectionStatus: document.querySelector("#connectionStatus"),
};

async function api(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function apiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return `${apiBaseUrl}${path.startsWith("/") ? path : `/${path}`}`;
}

function optionize(select, values, fallback) {
  select.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.append(option);
  });
  if (values.includes(fallback)) select.value = fallback;
  else if (values.length) select.value = values[0];
}

function renderProfiles() {
  const profiles = state.profiles || {};
  optionize(els.modelProfile, Object.keys(profiles.models || {}), "stheno-8b");
  optionize(els.styleProfile, Object.keys(profiles.story_styles || {}), "immersive-fiction");
  optionize(els.companionProfile, Object.keys(profiles.companions || {}), "story-companion");
  optionize(els.generationPreset, Object.keys(profiles.generation_presets || {}), "local-8b-14b-balanced");
  applyPresetFields();
  applyModelFields();
  applyCompanionFields();
}

function renderVoiceProfiles() {
  const voices = state.voiceProfiles?.voices || {};
  optionize(els.voiceProfile, Object.keys(voices), "orpheus-companion");
}

function applyPresetFields() {
  const preset = state.profiles?.generation_presets?.[els.generationPreset.value];
  if (!preset) return;
  els.maxTokens.value = preset.max_tokens ?? 800;
  els.temperature.value = preset.temperature ?? 0.85;
  els.contextMessages.value = preset.context_messages ?? 18;
}

function applyModelFields() {
  const model = state.profiles?.models?.[els.modelProfile.value];
  if (!model) return;
  els.backend.value = model.backend ?? "ollama";
  els.baseUrl.value = model.base_url ?? "http://localhost:11434";
  els.modelTag.value = model.model ?? els.modelProfile.value;
}

function fallbackPromptFromCompanion(companion) {
  const raw = companion?.raw_prompt || "";
  if (raw.trim()) return raw;
  const parts = [];
  if (companion?.persona) parts.push(companion.persona);
  if (companion?.role) parts.push(`Identity: ${companion.role}`);
  if (companion?.behavior) parts.push(`Behavior: ${companion.behavior}`);
  if (companion?.response_style) parts.push(`Response style: ${companion.response_style}`);
  return parts.join("\n\n");
}

function renderCompiledProfile(companion = null) {
  const compiled = companion?.compiled_profile || state.compiledCompanion?.compiled_profile || null;
  const hasCompiled = Boolean(compiled);
  els.companionCompileStatus.textContent = hasCompiled ? "Compiled profile ready" : "Profile not built yet";
  els.compiledArchetype.textContent = compiled?.archetype || "custom";
  els.compiledTone.textContent = compiled?.tone || "No compiled tone";
  els.compiledVoice.textContent = `voice: ${compiled?.voice_profile || companion?.voice_profile || "default"}`;
}

function applyCompanionFields() {
  const companion = state.profiles?.companions?.[els.companionProfile.value];
  if (!companion) return;
  state.compiledCompanion = companion.compiled_profile
    ? {
        raw_prompt: companion.raw_prompt,
        profile_hash: companion.profile_hash,
        compiler_version: companion.compiler_version,
        harness_version: companion.harness_version,
        compiled_at: companion.compiled_at,
        compiled_profile: companion.compiled_profile,
        compiled_system_block: companion.compiled_system_block || companion.persona,
        compiler_error: companion.compiler_error || "",
      }
    : null;
  els.companionName.value = companion.display_name ?? els.companionProfile.value;
  els.companionPrompt.value = fallbackPromptFromCompanion(companion);
  els.companionRole.value = companion.role ?? "";
  els.companionBehavior.value = companion.behavior ?? "";
  els.companionResponse.value = companion.response_style ?? "";
  if (companion.voice_profile && els.voiceProfile) els.voiceProfile.value = companion.voice_profile;
  if (companion.generation_preset && state.profiles?.generation_presets?.[companion.generation_preset]) {
    els.generationPreset.value = companion.generation_preset;
    applyPresetFields();
  }
  renderCompiledProfile(companion);
}

function linesToList(value) {
  return value
    .split("\n")
    .map((line) => line.replace(/^[-\s]+/, "").trim())
    .filter(Boolean);
}

function listToLines(value) {
  return Array.isArray(value) ? value.join("\n") : "";
}

function renderCompanionMemory(memory) {
  els.memoryProfileName.textContent = memory.companion_id ?? els.companionProfile.value;
  els.memoryUserName.value = memory.user_name ?? "";
  els.memoryCompanionName.value = memory.companion_name ?? "";
  els.memoryRelationship.value = memory.relationship ?? "";
  els.memoryCurrentDynamic.value = memory.current_dynamic ?? "";
  els.memoryTonePreferences.value = listToLines(memory.tone_preferences);
  els.memoryImportantFacts.value = listToLines(memory.important_facts);
  els.memoryBoundaries.value = listToLines(memory.boundaries);
  els.memoryLearnedNotes.value = listToLines(memory.learned_notes);
}

const memoryTabFilters = {
  profile: { categories: ["identity_fact", "companion_style", "voice_preference"], status: "active" },
  needs: { categories: ["preference", "need"], status: "active" },
  boundaries: { categories: ["boundary"], status: "active" },
  relationship: { categories: ["relationship_state"], status: "active" },
  story: { categories: ["story_fact"], status: "active" },
  review: { categories: [], status: "pending" },
};

function memoryCategoryLabel(value) {
  return value.replaceAll("_", " ");
}

function filteredMemories() {
  const filter = memoryTabFilters[state.activeMemoryTab] || memoryTabFilters.profile;
  return state.memories.filter((memory) => {
    if (filter.status && memory.status !== filter.status) return false;
    if (filter.categories.length && !filter.categories.includes(memory.category)) return false;
    return true;
  });
}

function memoryButton(label, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost";
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function renderMemoryDashboard() {
  const activeCount = state.memories.filter((memory) => memory.status === "active").length;
  const pendingCount = state.memories.filter((memory) => memory.status === "pending").length;
  els.personalMemoryCount.textContent = `${activeCount} active / ${pendingCount} pending`;

  els.memoryTabs.querySelectorAll(".memory-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.memoryTab === state.activeMemoryTab);
    if (button.dataset.memoryTab === "review") {
      button.textContent = pendingCount ? `Review Queue (${pendingCount})` : "Review Queue";
    }
  });

  // Timeline tab shows episodic memory ("what we've talked about"), not structured memories.
  if (state.activeMemoryTab === "timeline") {
    renderTimeline();
    return;
  }

  const memories = filteredMemories();
  els.personalMemoryList.innerHTML = "";
  if (!memories.length) {
    const empty = document.createElement("div");
    empty.className = "memory-empty";
    empty.textContent = "No memories in this section.";
    els.personalMemoryList.append(empty);
    return;
  }

  memories.forEach((memory) => {
    const card = document.createElement("article");
    card.className = `memory-card ${memory.status}`;
    card.dataset.memoryId = memory.id;

    const head = document.createElement("div");
    head.className = "memory-card-head";
    const meta = document.createElement("div");
    meta.className = "memory-meta";
    const category = document.createElement("span");
    category.className = "memory-badge";
    category.textContent = memoryCategoryLabel(memory.category);
    const status = document.createElement("span");
    status.className = `memory-badge ${memory.status}`;
    status.textContent = memory.status;
    meta.append(category, status);
    if (memory.pinned) {
      const pinned = document.createElement("span");
      pinned.className = "memory-badge";
      pinned.textContent = "pinned";
      meta.append(pinned);
    }

    const source = document.createElement("div");
    source.className = "memory-source";
    source.textContent = memory.source_conversation_id
      ? `source ${memory.source_conversation_id.slice(0, 8)}`
      : memory.source || "manual";
    head.append(meta, source);

    const editor = document.createElement("textarea");
    editor.value = memory.content;
    editor.spellcheck = true;

    const actions = document.createElement("div");
    actions.className = "memory-actions";
    if (memory.status === "pending") actions.append(memoryButton("Approve", () => approveMemory(memory.id)));
    actions.append(memoryButton("Save", () => updateMemory(memory.id, editor.value)));
    actions.append(memoryButton(memory.pinned ? "Unpin" : "Pin", () => togglePinMemory(memory)));
    actions.append(memoryButton("Archive", () => archiveMemory(memory.id)));
    actions.append(memoryButton("Delete", () => deleteMemory(memory.id)));
    if (memory.source_conversation_id) {
      actions.append(memoryButton("Open source", () => loadConversation(memory.source_conversation_id)));
    }

    card.append(head, editor, actions);
    els.personalMemoryList.append(card);
  });
}

function companionMemoryPayload() {
  return {
    user_name: els.memoryUserName.value.trim(),
    companion_name: els.memoryCompanionName.value.trim(),
    relationship: els.memoryRelationship.value.trim(),
    current_dynamic: els.memoryCurrentDynamic.value.trim(),
    tone_preferences: linesToList(els.memoryTonePreferences.value),
    important_facts: linesToList(els.memoryImportantFacts.value),
    boundaries: linesToList(els.memoryBoundaries.value),
    learned_notes: linesToList(els.memoryLearnedNotes.value),
  };
}

function renderConversations() {
  els.conversationList.innerHTML = "";
  if (!state.conversations.length) {
    const empty = document.createElement("div");
    empty.className = "hint";
    empty.textContent = "No saved conversations yet.";
    els.conversationList.append(empty);
    return;
  }

  state.conversations.forEach((conversation) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `conversation-item ${conversation.id === state.activeConversationId ? "active" : ""}`;
    button.innerHTML = `<strong></strong><span></span>`;
    button.querySelector("strong").textContent = conversation.title;
    button.querySelector("span").textContent = new Date(conversation.updated_at).toLocaleString();
    button.addEventListener("click", () => loadConversation(conversation.id));
    els.conversationList.append(button);
  });
}

function renderMessages() {
  els.messages.innerHTML = "";
  if (!state.messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "Start a saved local conversation. The app will recall relevant details through NervaPack as the story grows.";
    els.messages.append(empty);
    return;
  }

  state.messages.forEach((message, index) => {
    const article = document.createElement("article");
    article.className = `message ${message.role}`;
    const role = document.createElement("div");
    role.className = "message-role";
    role.textContent = message.role;
    if (message.role === "assistant") {
      const play = document.createElement("button");
      play.type = "button";
      play.className = "voice-play";
      play.textContent = "Play";
      play.title = "Generate and play local voice";
      play.addEventListener("click", () => speakMessage(message.content, index));
      role.append(play);
    }
    const content = document.createElement("div");
    content.className = "message-content";
    content.textContent = message.content;
    article.append(role, content);
    els.messages.append(article);
  });
  els.messages.scrollTop = els.messages.scrollHeight;
}

function toast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  document.body.append(node);
  setTimeout(() => node.remove(), 4200);
}

function setBusy(value) {
  state.busy = value;
  els.sendButton.disabled = value;
  els.saveCompanionButton.disabled = value;
  els.compileCompanionButton.disabled = value;
  els.saveMemoryButton.disabled = value;
  els.sendButton.textContent = value ? "Generating..." : "Generate";
  els.connectionStatus.textContent = value ? "busy" : "local";
}

async function loadProfiles() {
  state.profiles = await api("/api/profiles");
  renderProfiles();
  await loadCompanionMemory();
}

async function loadVoiceProfiles() {
  state.voiceProfiles = await api("/api/voice-profiles");
  renderVoiceProfiles();
  applyCompanionFields();
}

async function loadConversations() {
  const payload = await api("/api/conversations");
  state.conversations = payload.conversations || [];
  renderConversations();
}

async function loadConversation(id) {
  const payload = await api(`/api/conversations/${encodeURIComponent(id)}`);
  state.activeConversationId = payload.conversation.id;
  state.messages = payload.messages || [];
  els.conversationTitle.textContent = payload.conversation.title;
  renderConversations();
  renderMessages();
}

async function createConversation() {
  const payload = await api("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "New conversation" }),
  });
  state.activeConversationId = payload.conversation.id;
  state.messages = [];
  els.conversationTitle.textContent = payload.conversation.title;
  await loadConversations();
  renderMessages();
}

async function sendMessage(event) {
  event.preventDefault();
  const text = els.messageInput.value.trim();
  if (!text || state.busy) return;

  const userMessage = { role: "user", content: text };
  state.messages.push(userMessage);
  renderMessages();
  els.messageInput.value = "";
  setBusy(true);

  // Live assistant message that fills in sentence-by-sentence as the stream arrives.
  const assistantMessage = { role: "assistant", content: "" };
  state.messages.push(assistantMessage);
  renderMessages();
  const speakLive = els.autoSpeak.checked;
  if (speakLive) resetVoiceQueue();

  try {
    const done = await streamChat(
      {
        ...selectedRuntimePayload(),
        conversation_id: state.activeConversationId,
        message: text,
        use_memory: els.useMemory.checked,
      },
      (sentence, delivery = {}) => {
        // Progressive render + progressive voice.
        assistantMessage.content += (assistantMessage.content ? " " : "") + sentence;
        renderMessages();
        if (speakLive) enqueueVoice(sentence, delivery);
      }
    );

    state.activeConversationId = done.conversation.id;
    els.conversationTitle.textContent = done.conversation.title;
    // Replace the streamed text with the server's final (trimmed) version.
    assistantMessage.content = done.message.content;
    els.memoryStatus.textContent = els.useMemory.checked
      ? `${done.memory_status?.semantic_recalled ?? 0} recalled, ${done.memory_status?.suggested ?? 0} learned.`
      : "Semantic recall is off for this turn.";
    if (done.companion_memory) renderCompanionMemory(done.companion_memory);
    if (done.memory_suggestions?.length) await loadPersonalMemories();
    els.activeModel.textContent = `${done.config.backend}:${done.config.model}`;
    renderMessages();
    await loadConversations();
  } catch (error) {
    state.messages.pop(); // remove the empty/partial assistant message
    renderMessages();
    toast(error.message);
  } finally {
    setBusy(false);
  }
}

// --- Streaming chat client: reads newline-delimited JSON events ---------------
async function streamChat(body, onSentence) {
  const response = await fetch(apiUrl("/api/chat/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let donePayload = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      const evt = JSON.parse(line);
      if (evt.type === "sentence") onSentence(evt.text, evt.delivery || {});
      else if (evt.type === "error") throw new Error(evt.error);
      else if (evt.type === "done") donePayload = evt;
    }
  }
  if (!donePayload) throw new Error("Stream ended without completion.");
  return donePayload;
}

// --- Sequential voice queue: play sentence clips back-to-back without overlap --
function resetVoiceQueue() {
  stopVoice(false);
  state.voiceQueue = [];
  state.voicePlaying = false;
}

async function enqueueVoice(sentence, delivery = {}) {
  if (!sentence.trim()) return;
  const text = sentence.trim();
  const job = { text, delivery, promise: synthesizeVoice(text, delivery) };
  job.promise.catch(() => {});
  state.voiceQueue.push(job);
  if (state.voicePlaying) return;
  state.voicePlaying = true;
  els.voiceStatus.textContent = "speaking...";
  const runId = state.voiceRun;
  while (state.voiceQueue.length && runId === state.voiceRun) {
    const next = state.voiceQueue.shift();
    try {
      const payload = await next.promise;
      if (runId !== state.voiceRun) break;
      await new Promise((resolve) => {
        const audio = new Audio(apiUrl(payload.url));
        state.currentAudio = audio;
        const finish = () => {
          if (state.currentAudio === audio) state.currentAudio = null;
          resolve();
        };
        audio.addEventListener("ended", finish, { once: true });
        audio.addEventListener("error", finish, { once: true });
        audio.play().catch(resolve);
      });
      const pauseMs = Number(payload.delivery?.pause_after_ms ?? next.delivery?.pause_after_ms ?? 0);
      if (pauseMs > 0 && runId === state.voiceRun) {
        await new Promise((resolve) => setTimeout(resolve, Math.min(900, pauseMs)));
      }
    } catch (err) {
      if (err.name === "AbortError") break;
      // Skip a failed sentence rather than stalling the whole queue.
    }
  }
  state.voicePlaying = false;
  if (state.currentAudio === null) els.voiceStatus.textContent = "voice ready";
}

async function synthesizeVoice(text, delivery = {}) {
  const controller = new AbortController();
  state.voiceAbort = controller;
  state.voiceControllers.add(controller);
  try {
    return await api("/api/tts", {
      method: "POST",
      signal: controller.signal,
      body: JSON.stringify({
        text,
        voice_profile: els.voiceProfile.value,
        delivery,
      }),
    });
  } finally {
    state.voiceControllers.delete(controller);
    if (state.voiceAbort === controller) state.voiceAbort = null;
  }
}

function stopVoice(updateStatus = true) {
  state.voiceRun += 1;
  state.voiceQueue = [];
  state.voicePlaying = false;
  for (const controller of state.voiceControllers) controller.abort();
  state.voiceControllers.clear();
  if (state.voiceAbort) {
    state.voiceAbort.abort();
    state.voiceAbort = null;
  }
  if (state.currentAudio) {
    state.currentAudio.pause();
    state.currentAudio.currentTime = 0;
    state.currentAudio = null;
  }
  if (updateStatus) els.voiceStatus.textContent = "voice stopped";
}

function setVoiceBusy(busy) {
  state.voiceBusy = busy;
  document.querySelectorAll(".voice-play").forEach((btn) => {
    btn.disabled = busy;
    btn.classList.toggle("is-busy", busy);
  });
}

async function speakMessage(text, messageIndex) {
  if (!text) return;
  // Ignore re-clicks while a synth is in flight — prevents stacking requests
  // that serialize behind one another in the single TTS worker.
  if (state.voiceBusy) return;
  stopVoice(false);
  const runId = state.voiceRun;
  const cacheKey = `${els.voiceProfile.value}:${messageIndex}:${text.length}:${text.slice(0, 80)}`;
  let timer = null;
  try {
    let payload = state.audioCache.get(cacheKey);
    if (!payload) {
      setVoiceBusy(true);
      const started = Date.now();
      els.voiceStatus.textContent = "preparing voice... 0s";
      timer = setInterval(() => {
        els.voiceStatus.textContent = `preparing voice... ${Math.round((Date.now() - started) / 1000)}s`;
      }, 1000);
      payload = await synthesizeVoice(text);
      state.audioCache.set(cacheKey, payload);
    }
    if (timer) { clearInterval(timer); timer = null; }
    setVoiceBusy(false);
    if (runId !== state.voiceRun) return;
    const audio = new Audio(apiUrl(payload.url));
    state.currentAudio = audio;
    audio.addEventListener("ended", () => {
      if (state.currentAudio === audio) {
        state.currentAudio = null;
        els.voiceStatus.textContent = "voice ready";
      }
    });
    els.voiceStatus.textContent = `${payload.voice_profile} / ${payload.mood}`;
    await audio.play();
  } catch (error) {
    if (timer) clearInterval(timer);
    setVoiceBusy(false);
    if (runId !== state.voiceRun) return;
    if (error.name === "AbortError") {
      els.voiceStatus.textContent = "voice stopped";
      return;
    }
    els.voiceStatus.textContent = "voice error";
    toast(error.message);
  }
}

async function exportConversation() {
  if (!state.activeConversationId) {
    toast("Open or create a conversation first.");
    return;
  }
  const payload = await api("/api/export", {
    method: "POST",
    body: JSON.stringify({ conversation_id: state.activeConversationId, format: els.exportFormat.value }),
  });
  toast(`Exported to ${payload.path}`);
}

function companionOverridePayload() {
  const rawPrompt = els.companionPrompt.value.trim();
  const compiled = state.compiledCompanion;
  return {
    display_name: els.companionName.value.trim(),
    raw_prompt: rawPrompt,
    persona: compiled?.compiled_system_block || "",
    compiled_system_block: compiled?.compiled_system_block || "",
    role: els.companionRole.value.trim(),
    behavior: els.companionBehavior.value.trim(),
    response_style: els.companionResponse.value.trim(),
  };
}

function selectedRuntimePayload() {
  return {
    model_profile: els.modelProfile.value,
    style_profile: els.styleProfile.value,
    companion_profile: els.companionProfile.value,
    companion_override: companionOverridePayload(),
    generation_preset: els.generationPreset.value,
    overrides: {
      backend: els.backend.value,
      base_url: els.baseUrl.value.trim(),
      model: els.modelTag.value.trim(),
      max_tokens: Number(els.maxTokens.value),
      temperature: Number(els.temperature.value),
      context_messages: Number(els.contextMessages.value),
    },
  };
}

async function compileCompanion() {
  const rawPrompt = els.companionPrompt.value.trim();
  if (!rawPrompt) {
    toast("Add a companion brief first.");
    return null;
  }
  const previous = els.companionCompileStatus.textContent;
  els.compileCompanionButton.disabled = true;
  els.companionCompileStatus.textContent = "Building profile...";
  try {
    const payload = await api("/api/companions/compile", {
      method: "POST",
      body: JSON.stringify({
        ...selectedRuntimePayload(),
        display_name: els.companionName.value.trim(),
        raw_prompt: rawPrompt,
      }),
    });
    state.compiledCompanion = payload.compiled;
    const companion = payload.companion || {};
    els.companionName.value = companion.display_name || els.companionName.value;
    els.companionRole.value = companion.role || "";
    els.companionBehavior.value = companion.behavior || "";
    els.companionResponse.value = companion.response_style || "";
    if (companion.voice_profile && els.voiceProfile) els.voiceProfile.value = companion.voice_profile;
    renderCompiledProfile(companion);
    if (payload.compiled?.compiler_error) {
      toast("Used fallback compiler; local model compile failed.");
    }
    return payload.compiled;
  } catch (error) {
    els.companionCompileStatus.textContent = previous || "Profile not built yet";
    toast(error.message);
    return null;
  } finally {
    els.compileCompanionButton.disabled = state.busy;
  }
}

async function saveCompanion() {
  const companion = companionOverridePayload();
  const selected = state.profiles?.companions?.[els.companionProfile.value];
  const selectedName = selected?.display_name ?? els.companionProfile.value;
  const saveId = selectedName === companion.display_name ? els.companionProfile.value : companion.display_name;
  try {
    const payload = await api("/api/companions", {
      method: "POST",
      body: JSON.stringify({
        ...selectedRuntimePayload(),
        id: saveId,
        ...companion,
        compiled: state.compiledCompanion,
      }),
    });
    state.profiles = payload.profiles;
    renderProfiles();
    els.companionProfile.value = payload.id;
    applyCompanionFields();
    await loadCompanionMemory();
    toast(`Saved companion: ${payload.id}`);
  } catch (error) {
    toast(error.message);
  }
}

async function loadCompanionMemory() {
  if (!els.companionProfile.value) return;
  const payload = await api(`/api/companion-memory/${encodeURIComponent(els.companionProfile.value)}`);
  renderCompanionMemory(payload.memory);
  await loadPersonalMemories();
}

async function loadPersonalMemories() {
  if (!els.companionProfile.value) return;
  const payload = await api(`/api/memories?companion_id=${encodeURIComponent(els.companionProfile.value)}&include_archived=1`);
  state.memories = payload.memories || [];
  state.memoryCategories = payload.categories || [];
  renderMemoryDashboard();
}

async function loadEpisodes() {
  if (!els.companionProfile.value) return;
  try {
    const payload = await api(`/api/episodes?companion_id=${encodeURIComponent(els.companionProfile.value)}&limit=30`);
    state.episodes = payload.episodes || [];
  } catch (err) {
    state.episodes = [];
  }
  renderMemoryDashboard();
}

function renderTimeline() {
  els.personalMemoryCount.textContent = `${(state.episodes || []).length} remembered moments`;
  els.personalMemoryList.innerHTML = "";
  const episodes = state.episodes || [];
  if (!episodes.length) {
    const empty = document.createElement("div");
    empty.className = "memory-empty";
    empty.textContent = "No remembered moments yet. They build up as you talk — every ~12 turns a moment is summarized here.";
    els.personalMemoryList.append(empty);
    return;
  }
  episodes.forEach((ep) => {
    const card = document.createElement("article");
    card.className = "memory-card episode";
    const when = document.createElement("div");
    when.className = "memory-card-meta";
    const date = (ep.created_at || "").slice(0, 16).replace("T", " ");
    when.textContent = date;
    const body = document.createElement("div");
    body.className = "memory-card-content";
    body.textContent = ep.summary || "";
    card.append(when, body);
    els.personalMemoryList.append(card);
  });
}

async function saveCompanionMemory() {
  if (!els.companionProfile.value) {
    toast("Select a companion first.");
    return;
  }
  try {
    const payload = await api("/api/companion-memory", {
      method: "POST",
      body: JSON.stringify({
        companion_id: els.companionProfile.value,
        memory: companionMemoryPayload(),
      }),
    });
    renderCompanionMemory(payload.memory);
    toast("Saved companion memory.");
  } catch (error) {
    toast(error.message);
  }
}

async function addMemory() {
  const content = els.memoryCreateContent.value.trim();
  if (!content) {
    toast("Add memory text first.");
    return;
  }
  try {
    await api("/api/memories", {
      method: "POST",
      body: JSON.stringify({
        companion_id: els.companionProfile.value,
        category: els.memoryCreateCategory.value,
        content,
        pinned: els.memoryCreatePinned.checked,
        status: "active",
      }),
    });
    els.memoryCreateContent.value = "";
    els.memoryCreatePinned.checked = false;
    await loadPersonalMemories();
    toast("Added memory.");
  } catch (error) {
    toast(error.message);
  }
}

async function updateMemory(id, content) {
  try {
    await api(`/api/memories/${encodeURIComponent(id)}/update`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    await loadPersonalMemories();
    toast("Saved memory.");
  } catch (error) {
    toast(error.message);
  }
}

async function approveMemory(id) {
  try {
    await api(`/api/memories/${encodeURIComponent(id)}/approve`, { method: "POST", body: "{}" });
    await loadPersonalMemories();
    toast("Approved memory.");
  } catch (error) {
    toast(error.message);
  }
}

async function togglePinMemory(memory) {
  const action = memory.pinned ? "unpin" : "pin";
  try {
    await api(`/api/memories/${encodeURIComponent(memory.id)}/${action}`, { method: "POST", body: "{}" });
    await loadPersonalMemories();
  } catch (error) {
    toast(error.message);
  }
}

async function archiveMemory(id) {
  try {
    await api(`/api/memories/${encodeURIComponent(id)}/archive`, { method: "POST", body: "{}" });
    await loadPersonalMemories();
    toast("Archived memory.");
  } catch (error) {
    toast(error.message);
  }
}

async function deleteMemory(id) {
  try {
    await api(`/api/memories/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadPersonalMemories();
    toast("Deleted memory.");
  } catch (error) {
    toast(error.message);
  }
}

async function rebuildMemoryIndex() {
  try {
    const payload = await api("/api/memories/rebuild-index", { method: "POST", body: "{}" });
    toast(`Rebuilt index: ${payload.indexed} memories.`);
  } catch (error) {
    toast(error.message);
  }
}

async function checkRuntime() {
  try {
    els.connectionStatus.textContent = "check";
    const payload = await api("/api/health", {
      method: "POST",
      body: JSON.stringify(selectedRuntimePayload()),
    });
    els.connectionStatus.textContent = payload.ok ? "ready" : "error";
    els.activeModel.textContent = `${payload.backend}:${payload.model}`;
    if (payload.ok) {
      toast(`Runtime ready: ${payload.base_url}`);
    } else {
      const available = payload.available_models?.length ? ` Available: ${payload.available_models.join(", ")}` : "";
      toast(`${payload.error || "Runtime check failed."}${available}`);
    }
  } catch (error) {
    els.connectionStatus.textContent = "error";
    toast(error.message);
  }
}

async function boot() {
  try {
    await loadProfiles();
    await loadVoiceProfiles();
    await loadConversations();
    if (state.conversations[0]) {
      await loadConversation(state.conversations[0].id);
    } else {
      renderMessages();
    }
  } catch (error) {
    toast(error.message);
    els.connectionStatus.textContent = "error";
  }
}

els.composer.addEventListener("submit", sendMessage);
els.newConversationButton.addEventListener("click", createConversation);
els.refreshButton.addEventListener("click", async () => {
  await loadConversations();
  if (state.activeConversationId) await loadConversation(state.activeConversationId);
});
els.exportButton.addEventListener("click", exportConversation);
els.checkRuntimeButton.addEventListener("click", checkRuntime);
els.compileCompanionButton.addEventListener("click", compileCompanion);
els.saveCompanionButton.addEventListener("click", saveCompanion);
els.saveMemoryButton.addEventListener("click", saveCompanionMemory);
els.addMemoryButton.addEventListener("click", addMemory);
els.rebuildMemoryButton.addEventListener("click", rebuildMemoryIndex);
els.memoryTabs.addEventListener("click", (event) => {
  const button = event.target.closest("[data-memory-tab]");
  if (!button) return;
  state.activeMemoryTab = button.dataset.memoryTab;
  if (state.activeMemoryTab === "timeline") {
    loadEpisodes();  // fetch fresh episodes, then renders
  } else {
    renderMemoryDashboard();
  }
});
els.stopVoiceButton.addEventListener("click", () => stopVoice());
els.generationPreset.addEventListener("change", applyPresetFields);
els.modelProfile.addEventListener("change", applyModelFields);
els.companionProfile.addEventListener("change", async () => {
  applyCompanionFields();
  await loadCompanionMemory();
});
els.companionPrompt.addEventListener("input", () => {
  state.compiledCompanion = null;
  els.companionCompileStatus.textContent = "Profile changed; rebuild before saving for best results";
  els.compiledArchetype.textContent = "custom";
  els.compiledTone.textContent = "Waiting for build";
  els.compiledVoice.textContent = "voice: default";
});
els.messageInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    els.composer.requestSubmit();
  }
});

boot();
