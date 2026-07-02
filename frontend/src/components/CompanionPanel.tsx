import { useState } from 'react';
import { useStore } from '../store';
import { api } from '../api';
import type { CompanionProfile } from '../types';

interface Props {
  selectedCompanion: string;
  onCompanionChange: (id: string) => void;
  runtimePayload: () => object;
  onSaved: () => void;
}

export function CompanionPanel({ selectedCompanion, onCompanionChange, runtimePayload, onSaved }: Props) {
  const { state, dispatch } = useStore();
  const companions = state.profiles?.companions || {};
  const companionIds = Object.keys(companions);

  const [displayName, setDisplayName] = useState('Story Companion');
  const [rawPrompt, setRawPrompt] = useState('');
  const [compiling, setCompiling] = useState(false);
  const compiled = state.compiledCompanion;

  function applyCompanionFields(id: string) {
    const c = companions[id] as CompanionProfile | undefined;
    if (!c) return;
    setDisplayName(c.display_name || id);
    setRawPrompt(c.raw_prompt || c.persona || '');
  }

  function handleCompanionSelect(id: string) {
    onCompanionChange(id);
    applyCompanionFields(id);
  }

  async function compileCompanion() {
    if (!rawPrompt.trim()) {
      dispatch({ type: 'SHOW_TOAST', payload: 'Add a companion brief first.' });
      return;
    }
    setCompiling(true);
    try {
      const payload = await api<{ compiled: typeof compiled; companion: CompanionProfile }>(
        '/api/companions/compile',
        {
          method: 'POST',
          body: JSON.stringify({ ...runtimePayload(), display_name: displayName, raw_prompt: rawPrompt }),
        }
      );
      dispatch({ type: 'SET_COMPILED_COMPANION', payload: payload.compiled });
      const c = payload.companion || {};
      if (c.display_name) setDisplayName(c.display_name);
      if (payload.compiled?.compiler_error) {
        dispatch({ type: 'SHOW_TOAST', payload: 'Used fallback compiler; local model compile failed.' });
      }
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    } finally {
      setCompiling(false);
    }
  }

  async function saveCompanion() {
    const c = companions[selectedCompanion] as CompanionProfile | undefined;
    const selectedName = c?.display_name ?? selectedCompanion;
    const saveId = selectedName === displayName ? selectedCompanion : displayName;
    try {
      const payload = await api<{ profiles: typeof state.profiles; id: string }>('/api/companions', {
        method: 'POST',
        body: JSON.stringify({
          ...runtimePayload(),
          id: saveId,
          display_name: displayName,
          raw_prompt: rawPrompt,
          compiled: compiled,
        }),
      });
      dispatch({ type: 'SET_PROFILES', payload: payload.profiles! });
      onCompanionChange(payload.id);
      onSaved();
      dispatch({ type: 'SHOW_TOAST', payload: `Saved companion: ${payload.id}` });
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  return (
    <section className="companion-panel" aria-label="Companion setup">
      <div className="companion-toolbar">
        <label>
          <span>Companion</span>
          <select value={selectedCompanion} onChange={e => handleCompanionSelect(e.target.value)}>
            {companionIds.map(id => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <label>
          <span>Profile name</span>
          <input type="text" value={displayName} onChange={e => setDisplayName(e.target.value)} spellCheck={false} />
        </label>
        <button className="ghost companion-compile" type="button" disabled={compiling} onClick={compileCompanion}>
          {compiling ? 'Building...' : 'Preview build'}
        </button>
        <button className="primary companion-save" type="button" onClick={saveCompanion}>
          Save companion
        </button>
      </div>

      <label className="companion-prompt">
        <span>Describe your companion</span>
        <textarea
          rows={8}
          spellCheck={true}
          value={rawPrompt}
          onChange={e => setRawPrompt(e.target.value)}
          placeholder={`Write who the companion is, in plain language — for example:\n\nYou are Mira, my warm and playful girlfriend. You tease me, remember our inside jokes, and get a little shy when I compliment you. You speak casually in first person, like we're texting late at night.\n\nJust describe her — tone, how she treats me, boundaries, anything. Saving will turn this into her full profile automatically.`}
        />
        <small className="companion-hint">
          Type her personality above, then <strong>Save companion</strong> — it builds the full profile for you. (No need to fill anything else.)
        </small>
      </label>

      <div className="compiled-profile" aria-live="polite">
        <span>{compiled ? 'Profile built' : 'Profile not built yet'}</span>
        <strong>{(compiled?.archetype as string) || 'custom'}</strong>
        <span>{(compiled?.tone as string) || 'No compiled tone'}</span>
        <span>voice: {(compiled?.voice_profile as string) || 'default'}</span>
      </div>

      <details className="companion-advanced">
        <summary>Advanced: view what the profile builder extracted (read-only)</summary>
        <div className="companion-grid">
          <label>
            <span>Identity</span>
            <textarea rows={3} readOnly tabIndex={-1} value={(companions[selectedCompanion] as CompanionProfile)?.role || ''} onChange={() => {}} />
          </label>
          <label>
            <span>Behavior rules</span>
            <textarea rows={3} readOnly tabIndex={-1} value={(companions[selectedCompanion] as CompanionProfile)?.behavior || ''} onChange={() => {}} />
          </label>
          <label>
            <span>Response style</span>
            <textarea rows={3} readOnly tabIndex={-1} value={(companions[selectedCompanion] as CompanionProfile)?.response_style || ''} onChange={() => {}} />
          </label>
        </div>
      </details>
    </section>
  );
}
