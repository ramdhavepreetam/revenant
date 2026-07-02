import { useRef } from 'react';
import { useStore } from '../store';
import { api } from '../api';

interface Props {
  onRefresh: () => void;
}

export function Topbar({ onRefresh }: Props) {
  const { state, dispatch } = useStore();
  const fmtRef = useRef<HTMLSelectElement>(null);

  const title = state.messages.length > 0
    ? (state.messages.find(m => m.role === 'user')?.content.slice(0, 60) || 'New conversation')
    : 'New conversation';

  async function exportConversation() {
    if (!state.activeConversationId) return;
    const fmt = fmtRef.current?.value || 'md';
    try {
      const data = await api<{ content: string; filename: string }>(
        `/api/conversations/${state.activeConversationId}/export?format=${fmt}`
      );
      const blob = new Blob([data.content], { type: 'text/plain' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = data.filename || `conversation.${fmt}`;
      a.click();
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  return (
    <header className="topbar">
      <div>
        <div className="eyebrow">Local session</div>
        <h2>{title}</h2>
      </div>
      <div className="actions">
        <select className="compact-select" ref={fmtRef} aria-label="Export format">
          <option value="md">Markdown</option>
          <option value="json">JSON</option>
          <option value="txt">Text</option>
        </select>
        <button className="ghost" type="button" onClick={exportConversation}>Export</button>
        <button className="ghost" type="button" onClick={onRefresh}>Refresh</button>
      </div>
    </header>
  );
}
