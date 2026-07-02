import { createContext, useContext, useReducer } from 'react';
import type { Dispatch } from 'react';
import type {
  Conversation, Message, Profiles, CompanionMemory,
  PersonalMemory, Episode, CompiledCompanion, MemoryTab, Delivery,
} from './types';

export interface AppState {
  profiles: Profiles | null;
  voiceProfiles: Record<string, unknown>;
  conversations: Conversation[];
  activeConversationId: string;
  messages: Message[];
  memories: PersonalMemory[];
  memoryCategories: string[];
  episodes: Episode[];
  activeMemoryTab: MemoryTab;
  compiledCompanion: CompiledCompanion | null;
  companionMemory: CompanionMemory;
  busy: boolean;
  toast: string;
  voiceStatus: string;
  /** Current delivery metadata from the latest streamed sentence — for avatar */
  currentDelivery: Delivery;
  /** True while audio is playing — for avatar animation */
  isSpeaking: boolean;
}

const defaultCompanionMemory: CompanionMemory = {
  user_name: '', companion_name: '', relationship: '', current_dynamic: '',
  tone_preferences: [], important_facts: [], boundaries: [], learned_notes: [],
};

export const initialState: AppState = {
  profiles: null,
  voiceProfiles: {},
  conversations: [],
  activeConversationId: '',
  messages: [],
  memories: [],
  memoryCategories: [],
  episodes: [],
  activeMemoryTab: 'profile',
  compiledCompanion: null,
  companionMemory: defaultCompanionMemory,
  busy: false,
  toast: '',
  voiceStatus: 'Local speech is ready.',
  currentDelivery: {},
  isSpeaking: false,
};

export type Action =
  | { type: 'SET_PROFILES'; payload: Profiles }
  | { type: 'SET_VOICE_PROFILES'; payload: Record<string, unknown> }
  | { type: 'SET_CONVERSATIONS'; payload: Conversation[] }
  | { type: 'SET_ACTIVE_CONVERSATION'; payload: string }
  | { type: 'SET_MESSAGES'; payload: Message[] }
  | { type: 'APPEND_ASSISTANT_SENTENCE'; payload: string }
  | { type: 'SET_MEMORIES'; payload: { memories: PersonalMemory[]; categories: string[] } }
  | { type: 'SET_EPISODES'; payload: Episode[] }
  | { type: 'SET_MEMORY_TAB'; payload: MemoryTab }
  | { type: 'SET_COMPILED_COMPANION'; payload: CompiledCompanion | null }
  | { type: 'SET_COMPANION_MEMORY'; payload: CompanionMemory }
  | { type: 'SET_BUSY'; payload: boolean }
  | { type: 'SHOW_TOAST'; payload: string }
  | { type: 'SET_VOICE_STATUS'; payload: string }
  | { type: 'SET_DELIVERY'; payload: Delivery }
  | { type: 'SET_SPEAKING'; payload: boolean };

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'SET_PROFILES': return { ...state, profiles: action.payload };
    case 'SET_VOICE_PROFILES': return { ...state, voiceProfiles: action.payload };
    case 'SET_CONVERSATIONS': return { ...state, conversations: action.payload };
    case 'SET_ACTIVE_CONVERSATION': return { ...state, activeConversationId: action.payload };
    case 'SET_MESSAGES': return { ...state, messages: action.payload };
    case 'APPEND_ASSISTANT_SENTENCE': {
      const msgs = [...state.messages];
      const last = msgs[msgs.length - 1];
      if (last?.role === 'assistant') {
        msgs[msgs.length - 1] = {
          ...last,
          content: last.content ? last.content + ' ' + action.payload : action.payload,
        };
      }
      return { ...state, messages: msgs };
    }
    case 'SET_MEMORIES': return { ...state, memories: action.payload.memories, memoryCategories: action.payload.categories };
    case 'SET_EPISODES': return { ...state, episodes: action.payload };
    case 'SET_MEMORY_TAB': return { ...state, activeMemoryTab: action.payload };
    case 'SET_COMPILED_COMPANION': return { ...state, compiledCompanion: action.payload };
    case 'SET_COMPANION_MEMORY': return { ...state, companionMemory: action.payload };
    case 'SET_BUSY': return { ...state, busy: action.payload };
    case 'SHOW_TOAST': return { ...state, toast: action.payload };
    case 'SET_VOICE_STATUS': return { ...state, voiceStatus: action.payload };
    case 'SET_DELIVERY': return { ...state, currentDelivery: action.payload };
    case 'SET_SPEAKING': return { ...state, isSpeaking: action.payload };
    default: return state;
  }
}

interface StoreCtx {
  state: AppState;
  dispatch: Dispatch<Action>;
}

export const StoreContext = createContext<StoreCtx>({ state: initialState, dispatch: () => {} });

export function useStore() {
  return useContext(StoreContext);
}

export function useAppReducer() {
  return useReducer(reducer, initialState);
}
