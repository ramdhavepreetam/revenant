/**
 * Imperative voice controller — kept outside React state to avoid stale-closure
 * races over voiceRun / voiceQueue. React reads status via event callbacks.
 */
import { api, apiUrl } from './api';
import type { Delivery } from './types';

export type VoiceStatus =
  | 'ready'
  | 'speaking'
  | 'preparing'
  | 'stopped'
  | 'error';

export interface VoiceEvents {
  onStatusChange: (status: VoiceStatus, detail?: string) => void;
  /** Fires when a sentence starts playing — mood/pace for avatar animation */
  onSpeakStart: (delivery: Delivery) => void;
  onSpeakEnd: () => void;
}

interface QueueJob {
  text: string;
  delivery: Delivery;
  promise: Promise<{ url: string }>;
}

export class VoiceController {
  private voiceRun = 0;
  private voiceQueue: QueueJob[] = [];
  private voicePlaying = false;
  private voiceBusy = false;
  private currentAudio: HTMLAudioElement | null = null;
  private controllers = new Set<AbortController>();
  private audioCache = new Map<string, { url: string }>();
  private events: VoiceEvents;
  voiceProfile = 'mira-neural';

  constructor(events: VoiceEvents) {
    this.events = events;
  }

  private async synthesize(text: string, delivery: Delivery = {}, signal?: AbortSignal): Promise<{ url: string }> {
    return api<{ url: string }>('/api/tts', {
      method: 'POST',
      signal,
      body: JSON.stringify({ text, voice_profile: this.voiceProfile, delivery }),
    });
  }

  resetQueue() {
    this.stop(false);
    this.voiceQueue = [];
    this.voicePlaying = false;
  }

  async enqueue(sentence: string, delivery: Delivery = {}) {
    const text = sentence.trim();
    if (!text) return;
    const controller = new AbortController();
    this.controllers.add(controller);
    const promise = this.synthesize(text, delivery, controller.signal).finally(() => {
      this.controllers.delete(controller);
    });
    promise.catch(() => {});
    const job: QueueJob = { text, delivery, promise };
    this.voiceQueue.push(job);
    if (this.voicePlaying) return;
    this.voicePlaying = true;
    this.events.onStatusChange('speaking');
    const runId = this.voiceRun;
    while (this.voiceQueue.length && runId === this.voiceRun) {
      const next = this.voiceQueue.shift()!;
      try {
        const payload = await next.promise;
        if (runId !== this.voiceRun) break;
        await this.playAudio(payload.url, next.delivery, runId);
      } catch {
        // aborted or synthesis error — skip
      }
    }
    if (runId === this.voiceRun) {
      this.voicePlaying = false;
      this.events.onStatusChange('ready');
      this.events.onSpeakEnd();
    }
  }

  async speakOnce(text: string, cacheKey: string) {
    if (!text || this.voiceBusy) return;
    this.stop(false);
    const runId = this.voiceRun;
    let timer: ReturnType<typeof setInterval> | null = null;
    let payload = this.audioCache.get(cacheKey);
    try {
      if (!payload) {
        this.voiceBusy = true;
        const started = Date.now();
        this.events.onStatusChange('preparing', 'preparing voice... 0s');
        timer = setInterval(() => {
          this.events.onStatusChange('preparing', `preparing voice... ${Math.round((Date.now() - started) / 1000)}s`);
        }, 1000);
        payload = await this.synthesize(text);
        this.audioCache.set(cacheKey, payload);
      }
    } catch {
      this.events.onStatusChange('error', 'synthesis failed');
      return;
    } finally {
      if (timer) clearInterval(timer);
      this.voiceBusy = false;
    }
    if (runId !== this.voiceRun) return;
    this.events.onStatusChange('speaking');
    this.events.onSpeakStart({});
    await this.playAudio(payload.url, {}, runId);
    if (runId === this.voiceRun) {
      this.events.onStatusChange('ready');
      this.events.onSpeakEnd();
    }
  }

  private playAudio(url: string, delivery: Delivery, runId: number): Promise<void> {
    return new Promise((resolve) => {
      const audio = new Audio(apiUrl(url));
      this.currentAudio = audio;
      this.events.onSpeakStart(delivery);
      const finish = () => {
        if (this.currentAudio === audio) this.currentAudio = null;
        resolve();
      };
      audio.addEventListener('ended', finish, { once: true });
      audio.addEventListener('error', finish, { once: true });
      audio.play().catch(resolve);
      void runId;
    });
  }

  stop(updateStatus = true) {
    this.voiceRun += 1;
    this.voiceQueue = [];
    this.voicePlaying = false;
    for (const c of this.controllers) c.abort();
    this.controllers.clear();
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio = null;
    }
    if (updateStatus) {
      this.events.onStatusChange('stopped');
      this.events.onSpeakEnd();
    }
  }
}
