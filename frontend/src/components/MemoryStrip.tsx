import { useStore } from '../store';

interface Props {
  useMemory: boolean;
  onToggle: (v: boolean) => void;
  activeModel: string;
}

export function MemoryStrip({ useMemory, onToggle, activeModel }: Props) {
  const { state } = useStore();

  return (
    <section className="memory-strip" aria-live="polite">
      <div>
        <strong>NervaPack memory</strong>
        <span>{state.voiceStatus.startsWith('speaking') ? 'Semantic recall is active.' : 'Semantic recall is active.'}</span>
      </div>
      <div className="memory-controls">
        <label className="toggle">
          <input type="checkbox" checked={useMemory} onChange={e => onToggle(e.target.checked)} />
          <span>Use memory</span>
        </label>
        <span id="activeModel">{activeModel || 'model pending'}</span>
      </div>
    </section>
  );
}
