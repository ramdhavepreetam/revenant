import { useStore } from '../store';
import { api } from '../api';

export interface RuntimeValues {
  backend: string;
  baseUrl: string;
  modelTag: string;
  contextMessages: number;
}

interface Props {
  values: RuntimeValues;
  onChange: (v: RuntimeValues) => void;
}

export function RuntimeBar({ values, onChange }: Props) {
  const { dispatch } = useStore();

  function set(key: keyof RuntimeValues, val: string | number) {
    onChange({ ...values, [key]: val });
  }

  async function checkRuntime() {
    try {
      const data = await api<{ model: string; ok: boolean; error?: string }>('/api/runtime/check', {
        method: 'POST',
        body: JSON.stringify({ backend: values.backend, base_url: values.baseUrl, model: values.modelTag }),
      });
      if (data.ok) {
        dispatch({ type: 'SHOW_TOAST', payload: `Runtime OK — model: ${data.model}` });
      } else {
        dispatch({ type: 'SHOW_TOAST', payload: `Runtime error: ${data.error}` });
      }
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  return (
    <section className="runtime" aria-label="Local runtime settings">
      <label>
        <span>Backend</span>
        <select value={values.backend} onChange={e => set('backend', e.target.value)}>
          <option value="ollama">Ollama</option>
          <option value="openai">OpenAI-compatible</option>
        </select>
      </label>
      <label>
        <span>Base URL</span>
        <input type="text" value={values.baseUrl} spellCheck={false}
          onChange={e => set('baseUrl', e.target.value)} />
      </label>
      <label>
        <span>Exact model tag</span>
        <input type="text" value={values.modelTag} spellCheck={false}
          onChange={e => set('modelTag', e.target.value)} />
      </label>
      <label>
        <span>Context messages</span>
        <input type="number" min={2} max={80} step={2} value={values.contextMessages}
          onChange={e => set('contextMessages', parseInt(e.target.value))} />
      </label>
      <button className="ghost runtime-check" type="button" onClick={checkRuntime}>
        Check runtime
      </button>
    </section>
  );
}
