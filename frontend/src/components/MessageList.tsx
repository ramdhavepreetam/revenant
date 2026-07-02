import { useEffect, useRef } from 'react';
import { useStore } from '../store';
import type { Message } from '../types';

interface Props {
  onSpeakMessage: (text: string, index: number) => void;
}

export function MessageList({ onSpeakMessage }: Props) {
  const { state } = useStore();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [state.messages]);

  if (state.messages.length === 0) {
    return (
      <section className="messages" aria-label="Conversation messages">
        <p className="empty">Begin a new scene, or resume a saved conversation.</p>
      </section>
    );
  }

  return (
    <section className="messages" aria-label="Conversation messages">
      {state.messages.map((msg: Message, i: number) => (
        <div key={i} className={`message ${msg.role}`}>
          <div className="message-role">
            {msg.role === 'assistant' ? 'companion' : 'you'}
            {msg.role === 'assistant' && (
              <button
                className="voice-play"
                type="button"
                onClick={() => onSpeakMessage(msg.content, i)}
              >
                ▶ Speak
              </button>
            )}
          </div>
          <div className="message-content">{msg.content}</div>
        </div>
      ))}
      <div ref={bottomRef} />
    </section>
  );
}
