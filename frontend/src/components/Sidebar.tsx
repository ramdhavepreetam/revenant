import { useStore } from '../store';
import { api } from '../api';
import type { Conversation } from '../types';

interface Props {
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
}

export function Sidebar({ onSelectConversation, onNewConversation }: Props) {
  const { state } = useStore();

  return (
    <aside className="sidebar" aria-label="Conversations">
      <div className="brand">
        <div>
          <h1>AIBot</h1>
          <p>offline local writer</p>
        </div>
        <span className="status">local</span>
      </div>

      <button className="primary full" type="button" onClick={onNewConversation}>
        New conversation
      </button>

      <div className="section-title">Saved</div>
      <div className="conversation-list">
        {state.conversations.map((c: Conversation) => (
          <button
            key={c.id}
            className={`conversation-item${c.id === state.activeConversationId ? ' active' : ''}`}
            type="button"
            onClick={() => onSelectConversation(c.id)}
          >
            <strong>{c.title || 'Untitled'}</strong>
            <span>{new Date(c.updated_at).toLocaleDateString()}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}

// Exported helper used by App.tsx
export async function fetchConversations() {
  const data = await api<{ conversations: Conversation[] }>('/api/conversations');
  return data.conversations || [];
}
