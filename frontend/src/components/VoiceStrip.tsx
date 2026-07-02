import { useStore } from '../store';

interface Props {
  voiceProfileId: string;
  autoSpeak: boolean;
  onProfileChange: (id: string) => void;
  onAutoSpeakChange: (v: boolean) => void;
  onStop: () => void;
}

export function VoiceStrip({ voiceProfileId, autoSpeak, onProfileChange, onAutoSpeakChange, onStop }: Props) {
  const { state } = useStore();
  const profileIds = Object.keys(state.voiceProfiles);

  return (
    <section className="voice-strip" aria-live="polite">
      <div>
        <strong>Voice</strong>
        <span>{state.voiceStatus}</span>
      </div>
      <div className="voice-controls">
        <label>
          <span>Profile</span>
          <select value={voiceProfileId} onChange={e => onProfileChange(e.target.value)}>
            {profileIds.map(id => <option key={id} value={id}>{id}</option>)}
          </select>
        </label>
        <label className="toggle">
          <input type="checkbox" checked={autoSpeak} onChange={e => onAutoSpeakChange(e.target.checked)} />
          <span>Auto-speak</span>
        </label>
        <button className="ghost voice-stop" type="button" onClick={onStop}>Stop</button>
      </div>
    </section>
  );
}
