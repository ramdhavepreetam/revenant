import { useStore } from '../store';
import { api } from '../api';
import type { CompanionMemory } from '../types';

interface Props {
  companionId: string;
}

function linesToList(value: string): string[] {
  return value.split('\n').map(s => s.trim()).filter(Boolean);
}

function listToLines(value: string[] | undefined): string {
  return (value || []).join('\n');
}

export function CompanionMemoryPanel({ companionId }: Props) {
  const { state, dispatch } = useStore();
  const mem = state.companionMemory;

  function set(key: keyof CompanionMemory, val: string | string[]) {
    dispatch({ type: 'SET_COMPANION_MEMORY', payload: { ...mem, [key]: val } });
  }

  async function saveMemory() {
    try {
      await api(`/api/companion-memory/${encodeURIComponent(companionId)}`, {
        method: 'POST',
        body: JSON.stringify({
          user_name: mem.user_name || '',
          companion_name: mem.companion_name || '',
          relationship: mem.relationship || '',
          current_dynamic: mem.current_dynamic || '',
          tone_preferences: mem.tone_preferences || [],
          important_facts: mem.important_facts || [],
          boundaries: mem.boundaries || [],
          learned_notes: mem.learned_notes || [],
        }),
      });
      dispatch({ type: 'SHOW_TOAST', payload: 'Memory saved.' });
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  return (
    <section className="companion-memory-panel" aria-label="Companion memory">
      <div className="memory-editor-toolbar">
        <div>
          <span className="section-title">Companion memory</span>
          <strong>{companionId}</strong>
        </div>
        <button className="ghost memory-save" type="button" onClick={saveMemory}>
          Save memory
        </button>
      </div>

      <div className="memory-editor-grid">
        <label>
          <span>User name</span>
          <input type="text" value={mem.user_name || ''} onChange={e => set('user_name', e.target.value)} />
        </label>
        <label>
          <span>Companion name</span>
          <input type="text" value={mem.companion_name || ''} onChange={e => set('companion_name', e.target.value)} />
        </label>
        <label>
          <span>Relationship</span>
          <input type="text" value={mem.relationship || ''} onChange={e => set('relationship', e.target.value)} />
        </label>
        <label>
          <span>Current dynamic</span>
          <input type="text" value={mem.current_dynamic || ''} onChange={e => set('current_dynamic', e.target.value)} />
        </label>
        <label>
          <span>Tone preferences</span>
          <textarea rows={3} value={listToLines(mem.tone_preferences)} onChange={e => set('tone_preferences', linesToList(e.target.value))} />
        </label>
        <label>
          <span>Important facts</span>
          <textarea rows={3} value={listToLines(mem.important_facts)} onChange={e => set('important_facts', linesToList(e.target.value))} />
        </label>
        <label>
          <span>Boundaries</span>
          <textarea rows={3} value={listToLines(mem.boundaries)} onChange={e => set('boundaries', linesToList(e.target.value))} />
        </label>
        <label>
          <span>Learned notes</span>
          <textarea rows={3} value={listToLines(mem.learned_notes)} onChange={e => set('learned_notes', linesToList(e.target.value))} />
        </label>
      </div>
    </section>
  );
}
