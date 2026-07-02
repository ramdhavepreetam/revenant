import { useEffect, useRef, useState } from 'react';
import { StoreContext, useAppReducer } from './store';
import { api, apiUrl } from './api';
import { VoiceController } from './voice';
import type { Profiles, CompanionMemory, Message, Delivery } from './types';

import { Sidebar } from './components/Sidebar';
import { Topbar } from './components/Topbar';
import { ControlsBar } from './components/ControlsBar';
import type { ControlsValues } from './components/ControlsBar';
import { CompanionPanel } from './components/CompanionPanel';
import { CompanionMemoryPanel } from './components/CompanionMemoryPanel';
import { MemoryDashboard } from './components/MemoryDashboard';
import { RuntimeBar } from './components/RuntimeBar';
import type { RuntimeValues } from './components/RuntimeBar';
import { MemoryStrip } from './components/MemoryStrip';
import { VoiceStrip } from './components/VoiceStrip';
import { MessageList } from './components/MessageList';
import { Composer } from './components/Composer';
import { AvatarStage } from './components/AvatarStage';
import { Toast } from './components/Toast';

import './styles.css';

export default function App() {
  const [state, dispatch] = useAppReducer();

  // ── controls state ────────────────────────────────────────────────────
  const [controls, setControls] = useState<ControlsValues>({
    modelProfile: '', styleProfile: '', generationPreset: '',
    maxTokens: 800, temperature: 0.85,
  });
  const [runtime, setRuntime] = useState<RuntimeValues>({
    backend: 'ollama',
    baseUrl: 'http://localhost:11434',
    modelTag: 'hf.co/RichardErkhov/Sao10K_-_L3-8B-Stheno-v3.2-gguf:Q4_K_M',
    contextMessages: 18,
  });
  const [selectedCompanion, setSelectedCompanion] = useState('');
  const [voiceProfileId, setVoiceProfileId] = useState('mira-neural');
  const [autoSpeak, setAutoSpeak] = useState(false);
  const [useMemory, setUseMemory] = useState(true);

  // ── voice controller (imperative, lives in a ref) ─────────────────────
  const voiceRef = useRef<VoiceController | null>(null);
  if (!voiceRef.current) {
    voiceRef.current = new VoiceController({
      onStatusChange: (_status, detail) => {
        dispatch({ type: 'SET_VOICE_STATUS', payload: detail || _status });
      },
      onSpeakStart: (delivery: Delivery) => {
        dispatch({ type: 'SET_SPEAKING', payload: true });
        dispatch({ type: 'SET_DELIVERY', payload: delivery });
      },
      onSpeakEnd: () => dispatch({ type: 'SET_SPEAKING', payload: false }),
    });
  }
  const voice = voiceRef.current;

  useEffect(() => {
    voice.voiceProfile = voiceProfileId;
  }, [voiceProfileId, voice]);

  // ── boot ──────────────────────────────────────────────────────────────
  useEffect(() => {
    async function boot() {
      try {
        const pd = await api<{ profiles: Profiles }>('/api/profiles');
        const profiles = (pd.profiles ?? pd) as Profiles;
        dispatch({ type: 'SET_PROFILES', payload: profiles });

        const models = Object.keys(profiles.models || {});
        const styles = Object.keys(profiles.story_styles || {});
        const presets = Object.keys(profiles.generation_presets || {});
        setControls(c => ({
          ...c,
          modelProfile: models[0] || '',
          styleProfile: styles[0] || '',
          generationPreset: presets[0] || '',
        }));

        const companions = Object.keys(profiles.companions || {});
        if (companions.length) setSelectedCompanion(companions[0]);

        const vd = await api<{ voice_profiles: Record<string, unknown> }>('/api/voice-profiles');
        const vp = (vd.voice_profiles ?? vd) as Record<string, unknown>;
        dispatch({ type: 'SET_VOICE_PROFILES', payload: vp });
        const vpKeys = Object.keys(vp);
        if (vpKeys.length) setVoiceProfileId(vpKeys[0]);

        const cd = await api<{ conversations: typeof state.conversations }>('/api/conversations');
        const convs = cd.conversations || [];
        dispatch({ type: 'SET_CONVERSATIONS', payload: convs });
        if (convs.length) await loadConversation(convs[0].id);
        else dispatch({ type: 'SET_MESSAGES', payload: [] });
      } catch (err) {
        dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
      }
    }
    boot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedCompanion) loadCompanionData(selectedCompanion);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCompanion]);

  // ── data loaders ──────────────────────────────────────────────────────
  async function loadConversation(id: string) {
    const data = await api<{ messages: Message[] }>(`/api/conversations/${id}`);
    dispatch({ type: 'SET_ACTIVE_CONVERSATION', payload: id });
    dispatch({ type: 'SET_MESSAGES', payload: data.messages || [] });
  }

  async function loadConversations() {
    const data = await api<{ conversations: typeof state.conversations }>('/api/conversations');
    dispatch({ type: 'SET_CONVERSATIONS', payload: data.conversations || [] });
  }

  async function createConversation() {
    const data = await api<{ id: string }>('/api/conversations', { method: 'POST', body: JSON.stringify({}) });
    await loadConversations();
    await loadConversation(data.id);
  }

  async function loadCompanionData(cid: string) {
    try {
      const md = await api<{ memory: CompanionMemory }>(`/api/companion-memory/${encodeURIComponent(cid)}`);
      dispatch({ type: 'SET_COMPANION_MEMORY', payload: md.memory || {} });
      await loadPersonalMemories(cid);
      await loadEpisodes(cid);
    } catch { /* companion may not exist yet */ }
  }

  async function loadPersonalMemories(cid: string) {
    const data = await api<{ memories: typeof state.memories; categories: string[] }>(
      `/api/memories?companion_id=${encodeURIComponent(cid)}&include_archived=1`
    );
    dispatch({ type: 'SET_MEMORIES', payload: { memories: data.memories || [], categories: data.categories || [] } });
  }

  async function loadEpisodes(cid: string) {
    try {
      const data = await api<{ episodes: typeof state.episodes }>(
        `/api/episodes?companion_id=${encodeURIComponent(cid)}&limit=30`
      );
      dispatch({ type: 'SET_EPISODES', payload: data.episodes || [] });
    } catch {
      dispatch({ type: 'SET_EPISODES', payload: [] });
    }
  }

  // ── runtime payload builder ───────────────────────────────────────────
  function runtimePayload() {
    return {
      backend: runtime.backend,
      base_url: runtime.baseUrl,
      model_profile: controls.modelProfile,
      style_profile: controls.styleProfile,
      generation_preset: controls.generationPreset,
      model: runtime.modelTag,
      max_tokens: controls.maxTokens,
      temperature: controls.temperature,
      context_messages: runtime.contextMessages,
      companion_id: selectedCompanion,
    };
  }

  // ── chat / streaming ──────────────────────────────────────────────────
  async function sendMessage(text: string) {
    const nextMessages: Message[] = [
      ...state.messages,
      { role: 'user', content: text },
      { role: 'assistant', content: '' },
    ];
    dispatch({ type: 'SET_MESSAGES', payload: nextMessages });
    dispatch({ type: 'SET_BUSY', payload: true });

    if (autoSpeak) voice.resetQueue();

    try {
      await streamChat(
        { ...runtimePayload(), conversation_id: state.activeConversationId, message: text, use_memory: useMemory },
        (sentence, delivery) => {
          dispatch({ type: 'APPEND_ASSISTANT_SENTENCE', payload: sentence });
          if (autoSpeak) voice.enqueue(sentence, delivery);
        }
      );
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    } finally {
      dispatch({ type: 'SET_BUSY', payload: false });
    }
  }

  async function streamChat(body: object, onSentence: (s: string, d: Delivery) => void) {
    const response = await fetch(apiUrl('/api/chat/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        const evt = JSON.parse(line) as { type: string; text?: string; delivery?: Delivery; error?: string };
        if (evt.type === 'sentence') onSentence(evt.text!, evt.delivery || {});
        else if (evt.type === 'error') throw new Error(evt.error);
      }
    }
  }

  async function speakMessage(text: string, index: number) {
    const cacheKey = `${voiceProfileId}:${index}:${text.length}:${text.slice(0, 80)}`;
    await voice.speakOnce(text, cacheKey);
  }

  async function handleRefresh() {
    await loadConversations();
    if (state.activeConversationId) await loadConversation(state.activeConversationId);
  }

  const activeModel = runtime.modelTag.split('/').pop() || runtime.modelTag;

  return (
    <StoreContext.Provider value={{ state, dispatch }}>
      <div className="shell">
        <Sidebar
          onSelectConversation={loadConversation}
          onNewConversation={createConversation}
        />

        <section className="workspace" aria-label="Writer workspace">
          <Topbar onRefresh={handleRefresh} />

          {/* Avatar stage — reserved slot for Phase 2 */}
          <AvatarStage />

          <ControlsBar values={controls} onChange={setControls} />

          <CompanionPanel
            selectedCompanion={selectedCompanion}
            onCompanionChange={setSelectedCompanion}
            runtimePayload={runtimePayload}
            onSaved={() => loadCompanionData(selectedCompanion)}
          />

          <CompanionMemoryPanel companionId={selectedCompanion} />

          <MemoryDashboard
            companionId={selectedCompanion}
            onReload={() => loadCompanionData(selectedCompanion)}
          />

          <RuntimeBar values={runtime} onChange={setRuntime} />

          <MemoryStrip
            useMemory={useMemory}
            onToggle={setUseMemory}
            activeModel={activeModel}
          />

          <VoiceStrip
            voiceProfileId={voiceProfileId}
            autoSpeak={autoSpeak}
            onProfileChange={setVoiceProfileId}
            onAutoSpeakChange={setAutoSpeak}
            onStop={() => voice.stop()}
          />

          <MessageList onSpeakMessage={speakMessage} />

          <Composer onSend={sendMessage} />
        </section>
      </div>
      <Toast />
    </StoreContext.Provider>
  );
}
