# Companion Harness Plan

> Turning AIBot from "a chat box with a voice" into a **companion** with a robust
> conversation loop, context management, and memory. Grounded in the existing code
> (SQLite source of truth, `PersonalMemoryStore`, NervaPack recall) and 2026
> companion-architecture practice (tiered memory à la MemGPT / Mem0, compaction).

---

## 0. The two problems you named, separated

1. **"Shows half a sentence while spitting the value"** — a real **truncation bug**,
   not a memory issue. Fix first; it's small and high-impact.
2. **"Manage the context and the memory"** — the **harness** work. Bigger, staged below.

---

## 1. FIX FIRST: mid-sentence truncation (the "half sentence" bug)

**Cause (found in code):** `response_shape()` (`web_app.py:79-105`) hard-caps
`max_tokens` per turn — casual/brief replies at **160 tokens**, balanced at **320-360**.
The model hits `num_predict`/`max_tokens` and is **chopped mid-sentence**. With
`stream:False` (`local_llm_writer.py:88,110`) the user sees the whole truncated blob
at once, and the TTS then voices a half-thought.

**Three layered fixes:**
- **(a) Don't cut mid-sentence — trim back to the last complete sentence.** After
  generation, if the reply doesn't end in terminal punctuation, drop the trailing
  partial sentence before saving/displaying/voicing. Cheap, immediate, removes the
  visible "half sentence" entirely.
- **(b) Raise the brittle caps.** 160 tokens is ~120 words — too tight for a
  companion. Loosen the brief/balanced ceilings and let the *instruction* (not a hard
  token wall) shape length. Pair with a stop-aware finish.
- **(c) Stream the LLM** (`stream:true`) so text appears token-by-token and we can
  detect a natural stop. Also unlocks streaming TTS later. (Bigger change — Phase 3.)

Ship (a) now, (b) with it, (c) later.

---

## 2. Target architecture: tiered companion memory

Map the standard MemGPT/Mem0 tiers onto what AIBot already has:

| Tier | Role | AIBot today | Gap |
|---|---|---|---|
| **Working context** | system prompt + recent turns sent to the model each turn | `trim_messages(..., 18)` last-N window | No summary of older turns → they fall off a cliff |
| **Short-term / session** | running summary of the current conversation | — none | **Missing** — add rolling summary |
| **Long-term structured** | durable facts: identity, prefs, boundaries, relationship | `PersonalMemoryStore` (SQLite, 8 categories, approve flow) | Solid. Needs better recall ranking |
| **Long-term semantic** | fuzzy recall of past moments by similarity | `NervaPackMemory.recall` (Chroma) | Works; recall is whole-reply, unranked |
| **Episodic** | "what happened when" — events/scenes over time | — none | Optional later |

The two real gaps: **(A) session summarization** (so long chats don't lose the
thread when the 18-message window slides), and **(B) smarter recall assembly** (rank
+ budget what gets injected, instead of "top-5 + all active memories").

---

## 3. The conversation loop (target)

Each turn, in order:

```
1. Receive user message.
2. Retrieve context:
   a. Pinned + high-confidence structured memories (always).
   b. Semantic recall: top-K past moments relevant to THIS message.
   c. Rolling session summary (everything older than the live window).
3. Assemble working context under a token BUDGET:
   system(persona + style + companion)
   + memory block (ranked, capped)
   + session summary
   + last-N raw turns
   + user message
   -> if over budget, drop lowest-priority first (raw turns > summary > pinned).
4. Generate (streamed), finishing on a natural sentence boundary.
5. Persist turn to SQLite (source of truth).
6. Learn: extract new structured memories (existing `learn()`); queue conflicts as pending.
7. Maybe-summarize: every N turns (or when window would overflow), fold the
   oldest turns into the rolling session summary, then drop them from the live window.
8. Voice the (sentence-complete) reply.
```

Steps 2c, 3 (budget), 4 (stop-aware), and 7 (summarize) are the new harness pieces.

---

## 4. Build phases (each shippable + testable on its own)

**Phase 1 — Stop the bleeding (small, do now)**
- Sentence-boundary trim on every reply (fix §1a).
- Loosen `response_shape` caps (§1b).
- Verify: long + short replies through `/api/chat`, confirm no mid-sentence cut.

**Phase 2 — Context budgeting + ranked memory assembly**
- Add a token-budget assembler: estimate tokens, fill by priority, drop low-priority
  on overflow (replaces the blind `trim_messages(18)` + "inject everything").
- Rank recall: score memories by recency × confidence × pin × similarity; cap the
  injected block. Stops context pollution (the 2026 failure mode).

**Phase 3 — Session summarization (the real memory upgrade)**
- **Summary model: `gemma:latest`** (already in Ollama) — keeps clean, factual
  summaries separate from the RP-tuned Stheno companion model.
- Rolling summary: every N turns, summarize the oldest turns with the local LLM into
  a compact "what's happened / current dynamic" note; store on the conversation row;
  inject it; drop summarized raw turns from the live window.
- This is what lets a long companion conversation *stay coherent* instead of
  forgetting the start.

**Phase 4 — Streaming loop (smoothness + unlocks streaming TTS)**
- `stream:true` from Ollama; emit tokens to the UI; finish on natural stop.
- Feed completed sentences to the (already fast) Kokoro voice as they land.

**Phase 5 (optional) — Episodic memory + auto-approval tuning**
- Lightweight event log ("we talked about X on date Y"); recall by time/topic.
- Tune the pending/approve flow so good memories don't need manual approval.

---

## 5. Principles (from 2026 practice)

- **SQLite stays the source of truth.** Summaries/recall are derived + rebuildable.
- **Budget, don't dump.** Filter, rank, prune, summarize — never inject everything.
- **Summarize old, keep recent raw.** Recent turns verbatim; older turns compressed.
- **Finish thoughts.** Never voice or show a half sentence.
- **Everything local.** No cloud — consistent with [ADR 0001](adr/0001-offline-local-llm-interface.md).

---

## 6. Recommended order

1. **Phase 1** (truncation) — immediate, fixes the visible bug.
2. **Phase 3** (summarization) — biggest companion-feel upgrade.
3. **Phase 2** (budgeting) — makes 1 & 3 robust at scale.
4. **Phase 4** (streaming) — smoothness + streaming voice.
5. **Phase 5** — polish, when the core feels right.
