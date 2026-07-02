import { useState } from 'react';
import { useStore } from '../store';
import { api } from '../api';
import type { PersonalMemory, MemoryTab, Episode } from '../types';

const MEMORY_CATEGORIES = [
  'preference', 'need', 'boundary', 'companion_style',
  'relationship_state', 'story_fact', 'identity_fact', 'voice_preference',
];

const TAB_CATEGORY_MAP: Record<MemoryTab, string[]> = {
  profile: ['identity_fact'],
  needs: ['preference', 'need'],
  boundaries: ['boundary'],
  relationship: ['relationship_state', 'companion_style'],
  story: ['story_fact'],
  review: [],   // pending status
  timeline: [], // episodes
};

function memoryCategoryLabel(cat: string) {
  return cat.replace(/_/g, ' ');
}

interface Props {
  companionId: string;
  onReload: () => void;
}

export function MemoryDashboard({ companionId, onReload }: Props) {
  const { state, dispatch } = useStore();
  const [newCategory, setNewCategory] = useState('preference');
  const [newContent, setNewContent] = useState('');
  const [newPinned, setNewPinned] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');

  const tab = state.activeMemoryTab;

  function filteredMemories(): PersonalMemory[] {
    if (tab === 'review') return state.memories.filter(m => m.status === 'pending');
    if (tab === 'timeline') return [];
    const cats = TAB_CATEGORY_MAP[tab] || [];
    return state.memories.filter(m => cats.includes(m.category) && m.status !== 'archived');
  }

  async function addMemory() {
    if (!newContent.trim()) return;
    try {
      await api(`/api/memories`, {
        method: 'POST',
        body: JSON.stringify({ companion_id: companionId, category: newCategory, content: newContent.trim(), pinned: newPinned }),
      });
      setNewContent('');
      setNewPinned(false);
      onReload();
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  async function updateMemory(id: string, content: string) {
    try {
      await api(`/api/memories/${id}`, { method: 'PATCH', body: JSON.stringify({ content }) });
      setEditingId(null);
      onReload();
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  async function approveMemory(id: string) {
    try {
      await api(`/api/memories/${id}/approve`, { method: 'POST' });
      onReload();
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  async function togglePin(memory: PersonalMemory) {
    try {
      await api(`/api/memories/${memory.id}/pin`, { method: 'POST', body: JSON.stringify({ pinned: !memory.pinned }) });
      onReload();
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  async function archiveMemory(id: string) {
    try {
      await api(`/api/memories/${id}/archive`, { method: 'POST' });
      onReload();
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  async function deleteMemory(id: string) {
    try {
      await api(`/api/memories/${id}`, { method: 'DELETE' });
      onReload();
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  async function rebuildIndex() {
    try {
      await api(`/api/memories/rebuild`, { method: 'POST', body: JSON.stringify({ companion_id: companionId }) });
      dispatch({ type: 'SHOW_TOAST', payload: 'Memory index rebuilt.' });
    } catch (err) {
      dispatch({ type: 'SHOW_TOAST', payload: (err as Error).message });
    }
  }

  const TABS: { key: MemoryTab; label: string; isReview?: boolean }[] = [
    { key: 'profile', label: 'Profile' },
    { key: 'needs', label: 'Needs & Preferences' },
    { key: 'boundaries', label: 'Boundaries' },
    { key: 'relationship', label: 'Relationship' },
    { key: 'story', label: 'Story Continuity' },
    { key: 'review', label: 'Review Queue', isReview: true },
    { key: 'timeline', label: 'Timeline' },
  ];

  const displayed = filteredMemories();
  const isTimeline = tab === 'timeline';
  const totalActive = state.memories.filter(m => m.status !== 'archived').length;

  return (
    <section className="memory-dashboard" aria-label="Personalization memory dashboard">
      <div className="memory-dashboard-head">
        <div>
          <span className="section-title">Personal model</span>
          <strong>{totalActive} memories</strong>
        </div>
        <div className="memory-dashboard-actions">
          <button className="ghost" type="button" onClick={rebuildIndex}>Rebuild index</button>
        </div>
      </div>

      <div className="memory-tabs" id="memoryTabs" role="tablist" aria-label="Memory categories">
        {TABS.map(t => (
          <button
            key={t.key}
            className={`memory-tab${tab === t.key ? ' active' : ''}${t.isReview ? ' review' : ''}`}
            role="tab"
            aria-selected={tab === t.key}
            type="button"
            onClick={() => dispatch({ type: 'SET_MEMORY_TAB', payload: t.key })}
          >
            {t.label}
          </button>
        ))}
      </div>

      {!isTimeline && (
        <div className="memory-create">
          <label>
            <span>Category</span>
            <select value={newCategory} onChange={e => setNewCategory(e.target.value)}>
              {MEMORY_CATEGORIES.map(c => <option key={c} value={c}>{memoryCategoryLabel(c)}</option>)}
            </select>
          </label>
          <label className="memory-create-text">
            <span>Memory</span>
            <input
              type="text" spellCheck={true}
              value={newContent}
              placeholder="Add a stable preference, need, boundary, or continuity fact"
              onChange={e => setNewContent(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addMemory(); } }}
            />
          </label>
          <label className="toggle memory-create-pin">
            <input type="checkbox" checked={newPinned} onChange={e => setNewPinned(e.target.checked)} />
            <span>Pin</span>
          </label>
          <button className="primary" type="button" onClick={addMemory}>Add memory</button>
        </div>
      )}

      <div className="memory-list">
        {isTimeline ? (
          <TimelineList episodes={state.episodes} />
        ) : displayed.length === 0 ? (
          <div className="memory-empty">No memories in this category yet.</div>
        ) : (
          displayed.map(mem => (
            <div key={mem.id} className={`memory-card${mem.status === 'pending' ? ' pending' : ''}`}>
              <div className="memory-card-head">
                <div className="memory-meta">
                  <span className="memory-badge">{memoryCategoryLabel(mem.category)}</span>
                  {mem.pinned && <span className="memory-badge">pinned</span>}
                  {mem.status === 'pending' && <span className="memory-badge pending">review</span>}
                </div>
                <div className="memory-actions">
                  {mem.status === 'pending' && (
                    <button className="ghost" type="button" onClick={() => approveMemory(mem.id)}>Approve</button>
                  )}
                  <button className="ghost" type="button" onClick={() => togglePin(mem)}>
                    {mem.pinned ? 'Unpin' : 'Pin'}
                  </button>
                  <button className="ghost" type="button" onClick={() => {
                    setEditingId(mem.id === editingId ? null : mem.id);
                    setEditContent(mem.content);
                  }}>Edit</button>
                  <button className="ghost" type="button" onClick={() => archiveMemory(mem.id)}>Archive</button>
                  <button className="ghost" type="button" onClick={() => deleteMemory(mem.id)}>Delete</button>
                </div>
              </div>
              {editingId === mem.id ? (
                <>
                  <textarea rows={3} value={editContent} onChange={e => setEditContent(e.target.value)} />
                  <div className="memory-actions">
                    <button className="primary" type="button" onClick={() => updateMemory(mem.id, editContent)}>Save</button>
                    <button className="ghost" type="button" onClick={() => setEditingId(null)}>Cancel</button>
                  </div>
                </>
              ) : (
                <div className="memory-card-content">{mem.content}</div>
              )}
              {mem.source && <div className="memory-source">{mem.source}</div>}
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function TimelineList({ episodes }: { episodes: Episode[] }) {
  if (!episodes.length) return <div className="memory-empty">No remembered moments yet.</div>;
  return (
    <>
      {episodes.map(ep => (
        <div key={ep.id} className="memory-card episode">
          <div className="memory-card-meta">
            {ep.started_at ? new Date(ep.started_at).toLocaleString() : '—'}
            {ep.turn_count ? ` · ${ep.turn_count} turns` : ''}
          </div>
          <div className="memory-card-content">{ep.summary || 'No summary.'}</div>
        </div>
      ))}
    </>
  );
}
