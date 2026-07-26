/**
 * Imperative voice controller — kept outside React state to avoid stale-closure
 * races over voiceRun / voiceQueue. React reads status via event callbacks.
 *
 * Prefetch loop: synthesis for sentence N+1 fires the moment sentence N starts
 * playing, so audio is ready (or nearly ready) before playback needs it.
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
  /** Synthesis promise — fired immediately when the job is enqueued */
  promise: Promise<{ url: string }>;
  /** Preloaded Audio element — created as soon as synthesis resolves */
  audio?: HTMLAudioElement;
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

  /**
   * Preload an Audio element as soon as the synthesis URL is known so the
   * browser can buffer the file before playback begins.
   */
  private preloadAudio(url: string): HTMLAudioElement {
    const audio = new Audio(apiUrl(url));
    audio.preload = 'auto';
    audio.load();
    return audio;
  }

  resetQueue() {
    this.stop(false);
    this.voiceQueue = [];
    this.voicePlaying = false;
  }

  enqueue(sentence: string, delivery: Delivery = {}) {
    const text = sentence.trim();
    if (!text) return;

    // Fire synthesis immediately — don't wait for the previous sentence to finish.
    const controller = new AbortController();
    this.controllers.add(controller);
    const promise = this.synthesize(text, delivery, controller.signal).then(payload => {
      // As soon as we have the URL, start buffering the audio file.
      if (this.voiceRun === runId) {
        job.audio = this.preloadAudio(payload.url);
      }
      return payload;
    }).finally(() => {
      this.controllers.delete(controller);
    });
    promise.catch(() => {});

    // Capture voiceRun at enqueue time so preload can check for cancellation.
    const runId = this.voiceRun;
    const job: QueueJob = { text, delivery, promise };
    this.voiceQueue.push(job);

    if (this.voicePlaying) return;
    this.voicePlaying = true;
    this.events.onStatusChange('speaking');
    this.drainQueue(this.voiceRun);
  }

  private async drainQueue(runId: number) {
    while (this.voiceQueue.length && runId === this.voiceRun) {
      const current = this.voiceQueue.shift()!;

      let payload: { url: string };
      try {
        payload = await current.promise;
      } catch {
        continue; // synthesis aborted or failed — skip to next
      }
      if (runId !== this.voiceRun) break;

      // Kick off prefetch for next sentence the moment current synthesis resolves.
      // (It may already be in flight if the stream is fast — no-op in that case.)
      this.prefetchNext(runId);

      const audio = current.audio ?? this.preloadAudio(payload.url);
      await this.playAudio(audio, current.delivery, runId);

      // As soon as playback starts (not ends), kick off the next prefetch again.
      // drainQueue already called prefetchNext above — this covers the case where
      // audio was already preloaded before the loop reached it.
    }

    if (runId === this.voiceRun) {
      this.voicePlaying = false;
      this.events.onStatusChange('ready');
      this.events.onSpeakEnd();
    }
  }

  /**
   * Ensure the next job in the queue has its synthesis promise started.
   * Since synthesis is fired at enqueue time, this mainly triggers preloading
   * for the job after the one currently playing.
   */
  private prefetchNext(runId: number) {
    const next = this.voiceQueue[0];
    if (!next || next.audio) return;
    // Attach a preload step to the existing promise if it hasn't resolved yet.
    next.promise.then(payload => {
      if (runId === this.voiceRun && !next.audio) {
        next.audio = this.preloadAudio(payload.url);
      }
    }).catch(() => {});
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
    const audio = this.preloadAudio(payload.url);
    await this.playAudio(audio, {}, runId);
    if (runId === this.voiceRun) {
      this.events.onStatusChange('ready');
      this.events.onSpeakEnd();
    }
  }

  private playAudio(audio: HTMLAudioElement, delivery: Delivery, runId: number): Promise<void> {
    return new Promise((resolve) => {
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
