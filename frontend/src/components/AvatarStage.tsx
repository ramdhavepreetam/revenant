import { useStore } from '../store';

export function AvatarStage() {
  const { state } = useStore();
  const mood = (state.currentDelivery.mood as string) || '';

  return (
    <div className={`avatar-stage${state.isSpeaking ? ' speaking' : ''}`}>
      <div className="avatar-placeholder">
        <svg width="56" height="56" viewBox="0 0 56 56" fill="none">
          <circle cx="28" cy="20" r="12" stroke="currentColor" strokeWidth="1.5" />
          <path d="M8 52c0-11.046 8.954-20 20-20s20 8.954 20 20" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <span>Avatar · Phase 2</span>
      </div>
      <div className="avatar-speaking-ring" />
      {mood && <div className="avatar-mood-badge">{mood}</div>}
    </div>
  );
}
