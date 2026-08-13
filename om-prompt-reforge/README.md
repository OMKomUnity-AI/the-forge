# om-prompt-reforge 🕉️

A **prompt-reforging** layer for [Claude Code](https://claude.com/claude-code), in two `UserPromptSubmit` hooks. Part of [the-forge](../README.md), an open toolkit by [OMKomUnity](https://omkomunity.ai).

1. **`prompt-router.py`** — a **0-LLM** gate (regex + stdlib) that, when your prompt is vague, broad or worth challenging, injects an instruction telling Claude to run the `forge` skill first (pre-qualify, challenge, rewrite into a spec). Cost: **zero tokens, zero latency**.
2. **`prompt-enricher.py`** — on a non-trivial prompt *only*, one isolated **Haiku** call produces a **one-sentence reformulation** that makes your intent explicit, printed in **pink** prefixed with **🕉️**. It **never blocks** your prompt and needs **no API key**.

> The core idea: **the 0-LLM gate protects the LLM from itself.** The enricher only calls Haiku once the regex router has judged the prompt non-trivial. So "thanks", "git status" or "yes" cost nothing.

---

## Before / after

Type an under-specified prompt; the 🕉️ hands you back the explicit intent *before* Claude runs off on an assumption:

```
You    ▸  improve the perf

🕉️     ▸  Improve performance of an unspecified component/system by an unspecified
          metric (latency/throughput/memory/cost) without a defined baseline or
          success target.
```

**It follows your language.** Type in French, you get French back:

```
You    ▸  améliore la perf

🕉️     ▸  L'utilisateur demande une amélioration de performance sans préciser le
          système/module visé, la métrique (vitesse, mémoire, latence) ni le
          contexte projet actif.
```

*(Both are real outputs of this hook, captured under test. Like any LLM call, the wording varies slightly run to run — that's the nature of enrichment, not a bug.)* The reformulation is passed as **context** (`additionalContext`): Claude sees it, but your original message stays intact. Nothing is rewritten in your place.

---

## What you need

- **Python 3** (stdlib only — no `pip` dependencies).
- For the enricher: the **`claude` CLI** installed and authenticated (Claude Pro/Max subscription). The enricher shells out to `claude -p` and **rides on that authentication** — no API key to manage.
- The **router alone** needs nothing but Python: if you only want the 0-LLM gate, install just that.

---

## Installation

1. **Copy the files** into *your* project's `.claude/`:

   ```
   your-project/
   └── .claude/
       ├── hooks/
       │   ├── prompt-router.py      # both hooks must stay co-located:
       │   └── prompt-enricher.py    # the enricher imports classify() from the router by path
       └── skills/
           └── forge/
               └── SKILL.md
   ```

   From this repo:
   ```bash
   cp -R om-prompt-reforge/.claude/hooks/*        your-project/.claude/hooks/
   cp -R om-prompt-reforge/.claude/skills/forge   your-project/.claude/skills/
   ```

2. **Wire the hooks**: merge [`settings.example.json`](./settings.example.json) into your project's `.claude/settings.json`. Order matters — **router then enricher**:

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         { "hooks": [ { "type": "command",
           "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/prompt-router.py\"" } ] },
         { "hooks": [ { "type": "command",
           "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/prompt-enricher.py\"",
           "timeout": 30,
           "statusMessage": "Enriching the prompt (Haiku)..." } ] }
       ]
     }
   }
   ```

3. **Restart Claude Code** (hooks load at session start).

---

## Demo (test in isolation, nothing wired)

Each hook reads a `{"prompt": "..."}` JSON on stdin, so you can exercise them by hand:

```bash
cd om-prompt-reforge/.claude/hooks

# Router — trivial prompt: total silence (the prompt passes through intact)
echo '{"prompt":"thanks"}'                        | python3 prompt-router.py    # (no output)

# Router — non-trivial prompt: injects the "run forge" instruction
echo '{"prompt":"build me a caching system"}'     | python3 prompt-router.py

# Enricher — trivial prompt: 0-LLM gate, NO Haiku call
echo '{"prompt":"git status"}'                    | python3 prompt-enricher.py

# Enricher — non-trivial prompt: real Haiku call (5-24s), pink 🕉️ reformulation
echo '{"prompt":"improve the perf"}'              | python3 prompt-enricher.py
```

The last call consumes your subscription (≈ a few cents) and takes a few seconds: that's the enricher's real behavior.

---

## Model & refresh

The Haiku model is **configurable via an environment variable**, with a pinned default for reproducibility:

```bash
# Default if unset: claude-haiku-4-5-20251001
export FORGE_ENRICHER_MODEL="claude-haiku-4-5-20251001"
```

When a newer Haiku ships, point the variable at it (or change the default at the top of `prompt-enricher.py`). Prefer a **dated** identifier over a floating alias if you want stable reformulations over time.

### SDK variant (optional, portable outside Claude Code)

By default the enricher shells out to `claude -p`: **zero key**, but it requires the `claude` CLI to be installed and logged in. To run the enricher **where the CLI doesn't exist** (CI, another host, another agent), replace the subprocess call with a direct Anthropic SDK call authenticated via `ANTHROPIC_API_KEY`. Assumed trade-off: that key is **billed separately** from the subscription (it does not ride on it). Documented as a variant, not implemented here — the zero-key default covers Claude Code usage.

---

## The engineering story

This hook is the product of an evaluation that **refuted its own starting intuition**, then qualified it.

**The intuition**: "you need an LLM to understand a prompt better than a regex can." Six configurations were compared over a corpus of 14 synthetic cases + 10 sampled real prompts:

| Config | Mechanism | Score | Latency | LLM calls |
|---|---|---|---|---|
| **V0** | **0-LLM** regex router | **12/14** | **0 s** | **0** |
| V1 | solo LLM clarifier | 12/14 | 10.4 s | 14/14 |
| V2 | chained (router + V1) | 10/14 | 10.4 s | 14/14 |
| V3 | strict clarifier | 9/14 | 9.2 s | 14/14 |
| V4 | permissive clarifier | 13/14 | 8.3 s | 14/14 |
| V5 | sequential hybrid | 12/14 | ~4.5 s | 6/14 |

**The measured verdict**: the 0-LLM router (V0) **matches or beats 4 of the 5 LLM variants**. The only one that edges past it (V4) does so by **+1 case** at the price of **+8.3 s on every prompt** and a systematic LLM call. Over 10 real prompts, the LLM variant never usefully diverged from the router — it only added latency.

**The rebound**: the metric under test (block / pass) was *incomplete*. The real value of an LLM here isn't the binary decision — it's the **quality of the reformulation** itself, which the 0-LLM cannot produce by construction (it only has a trigger toward `forge`, no "reformulation" field).

**The mechanism that unlocked it**: rather than a "bare" `claude -p` (which requires `ANTHROPIC_API_KEY` and breaks subscription auth), an **inline ephemeral agent** — `--agents '{...}' --allowedTools "" --settings '{"disableAllHooks":true}'`, `maxTurns:1`, `effort:low`. Result: no reloading of session hooks, no risk of the model *executing* the task instead of reforging it, and above all **it consumes the subscription, no separate API billing**. Validated 21/21 with no timeout.

**The retained design** — the one in this repo: **0-LLM gate (router) THEN non-blocking Haiku enrichment (enricher).** You capture the value (the reformulation) without the risk (never a false block, zero call on trivial prompts). It's the synthesis of a thesis refuted by measurement, not a reflex "put an LLM everywhere".

---

## Assumed limits

- **Latency**: +5 to 24 s on **non-trivial** prompts (a floor inherent to the model, measured over 21 calls). **0 s** on trivial prompts (0-LLM gate).
- **Non-determinism**: the reformulation varies slightly from one call to the next — expected LLM behavior.
- **Cost**: ≈ $0.007–0.03 per non-trivial prompt via `claude -p` (the subprocess reloads the CLI overhead). Trivial prompts cost nothing.
- **Dependency**: the enricher requires the `claude` CLI. The router is pure stdlib and works everywhere.
- **Fail-safe**: any hook error or timeout → silence, `exit 0`, prompt unchanged. A hook must never get between you and your work.

---

## Adapt `forge` to your ecosystem

The `forge` skill and the router's injected instruction reference skills from the original ecosystem (`new-feature`, `wiki-ingest`, `context7`/`playwright` MCP…). These are **examples of routing targets**. In a minimal setup, `forge` naturally stops at the **spec** (Phase 4) — replace the list with your own skills, or leave it: `forge` degrades gracefully.

---

Built & maintained by **[OMKomUnity](https://omkomunity.ai)** — *AI orchestrated, not endured.*
Licensed under [MIT](../LICENSE) — © 2026 OMKomUnity.
