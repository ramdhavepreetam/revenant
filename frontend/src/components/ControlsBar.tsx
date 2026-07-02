import { useStore } from '../store';

export interface ControlsValues {
  modelProfile: string;
  styleProfile: string;
  generationPreset: string;
  maxTokens: number;
  temperature: number;
}

interface Props {
  values: ControlsValues;
  onChange: (v: ControlsValues) => void;
}

export function ControlsBar({ values, onChange }: Props) {
  const { state } = useStore();
  const profiles = state.profiles || {};
  const models = Object.keys(profiles.models || {});
  const styles = Object.keys(profiles.story_styles || {});
  const presets = Object.keys(profiles.generation_presets || {});

  function set(key: keyof ControlsValues, val: string | number) {
    onChange({ ...values, [key]: val });
  }

  return (
    <section className="controls" aria-label="Profiles and generation settings">
      <label>
        <span>Model</span>
        <select value={values.modelProfile} onChange={e => set('modelProfile', e.target.value)}>
          {models.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
      <label>
        <span>Style</span>
        <select value={values.styleProfile} onChange={e => set('styleProfile', e.target.value)}>
          {styles.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <label>
        <span>Preset</span>
        <select value={values.generationPreset} onChange={e => set('generationPreset', e.target.value)}>
          {presets.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
      </label>
      <label>
        <span>Max tokens</span>
        <input
          type="number" min={128} max={4096} step={64}
          value={values.maxTokens}
          onChange={e => set('maxTokens', parseInt(e.target.value))}
        />
      </label>
      <label>
        <span>Temperature</span>
        <input
          type="number" min={0} max={2} step={0.05}
          value={values.temperature}
          onChange={e => set('temperature', parseFloat(e.target.value))}
        />
      </label>
    </section>
  );
}
