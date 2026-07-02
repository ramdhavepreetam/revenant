export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export interface VoiceProfile {
  engine: string;
  mood?: string;
  notes?: string;
  [key: string]: unknown;
}

export interface CompanionProfile {
  display_name?: string;
  raw_prompt?: string;
  persona?: string;
  compiled_system_block?: string;
  role?: string;
  behavior?: string;
  response_style?: string;
  voice_profile?: string;
  compiled?: CompiledCompanion;
  [key: string]: unknown;
}

export interface CompiledCompanion {
  archetype?: string;
  tone?: string;
  voice_profile?: string;
  compiler_error?: string;
  [key: string]: unknown;
}

export interface CompanionMemory {
  user_name?: string;
  companion_name?: string;
  relationship?: string;
  current_dynamic?: string;
  tone_preferences?: string[];
  important_facts?: string[];
  boundaries?: string[];
  learned_notes?: string[];
  [key: string]: unknown;
}

export interface PersonalMemory {
  id: string;
  category: string;
  content: string;
  status: string;
  pinned: boolean;
  confidence: number;
  source?: string;
  updated_at?: string;
}

export interface Episode {
  id: string;
  summary?: string;
  started_at?: string;
  ended_at?: string;
  turn_count?: number;
}

export interface Profiles {
  models?: Record<string, unknown>;
  story_styles?: Record<string, unknown>;
  generation_presets?: Record<string, unknown>;
  companions?: Record<string, CompanionProfile>;
}

export interface Delivery {
  mood?: string;
  pace?: number;
  energy?: number;
  [key: string]: unknown;
}

export type MemoryTab = 'profile' | 'needs' | 'boundaries' | 'relationship' | 'story' | 'review' | 'timeline';
