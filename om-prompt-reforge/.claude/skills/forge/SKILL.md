---
name: forge
description: >-
  Pre-qualify, challenge, and sharpen a request before any work begins, then
  rewrite it into a crisp spec and route it to the right skills. ALWAYS use this
  the moment the user expresses an intent, idea, feature, or task that is vague,
  ambiguous, broad, or worth challenging — anything starting with "I want to…",
  "let's build…", "can you make…", "I'm thinking about…", "help me with…", or any
  request whose success criteria, scope, or approach are not already explicit.
  Also invoked automatically by the prompt-router hook for non-trivial prompts.
  Skip only for trivial, fully-specified one-liners.
---

# forge

The front door for every non-trivial request. Its job is to make sure Claude never
runs off on a wrong assumption — the single most common agent failure. It turns a
fuzzy intent into a sharp, routed, ready-to-execute spec.

forge does NOT do the work itself. It clarifies, challenges, and hands off.

## When to run vs skip

Run for: features, builds, refactors, designs, content, anything multi-step, and
anything where scope/criteria/approach aren't already stated.

Skip for: trivial, fully-specified asks ("rename X to Y", "what does this error
mean", "format this file"). Match rigor to stakes — don't grill someone over a typo.

## The loop

### Phase 1 — Read intent & score clarity
Restate the request in one sentence as you understand it. Then silently assess:
- Is the **goal** clear and singular, or fuzzy / multiple?
- Are **success criteria** stated or missing?
- Is the **scope** bounded or open-ended?
- Are there **hidden assumptions** that, if wrong, waste hours?
- Is the **stated approach** actually the best one, or worth challenging?

If everything is already crisp → say so, produce the spec (Phase 4), and route
(Phase 5). Don't manufacture questions to look thorough.

### Phase 2 — Challenge & counter-propose (the smart part)
Before asking questions, apply judgment:
- **Challenge the framing.** If the user asked for X but Y likely serves the real
  goal better, say so with a one-line rationale. Don't just obey.
- **Surface assumptions** you'd otherwise make silently, and check them.
- **Offer a smarter alternative** when you see one ("you asked for a cron job, but
  an event trigger would be simpler and more reliable — want that instead?").
- **Name tradeoffs** the user may not have considered (cost, lock-in, complexity).
This is where forge earns its keep: a better question or a better idea beats a
faster wrong answer.

### Phase 3 — Socratic discovery loop (with a stop condition)
Ask the **highest-leverage questions first**, ideally one focused batch rather than
a slow drip. Cover only what actually changes the work: goal, constraints, inputs/
outputs, edge cases, définition of done, non-goals.

**Stop condition — this is critical.** Stop asking when you can write verifiable
success criteria and name the approach. Do NOT chase a "perfect" discovery past the
point of usefulness. When you judge it good-enough-to-start, say:
"I have enough to proceed — here's the spec. Tell me if anything's off." Let the
user short-circuit at any time with "just go".

### Phase 4 — Rewrite into a spec
Emit a compact, structured brief:
```
## Goal
<one sentence>

## Success criteria (verifiable)
- <criterion>
- <criterion>

## Scope
In:  <what's included>
Out: <explicit non-goals>

## Approach
<the chosen approach + one line on why, incl. any challenge accepted>

## Assumptions
- <surfaced assumption the user confirmed or didn't object to>

## Open risks / follow-ups
- <thing to watch>
```
This spec is the rewritten, enriched version of the original prompt — the artifact
that actually gets executed.

### Phase 5 — Route to skills & capabilities
Map the spec to what should execute it, and say so explicitly:
- Deliverable = a shipped change → hand to **new-feature** (branch → TDD → PR).
- Bug → reproduce-first via the debugging discipline, then new-feature.
- Doc/deck/sheet → the relevant document skill.
- Marketing asset → the marketing skills (consulting-quality, campaign-brief, content-factory, martech-calendar…).
- Needs live library docs → flag the **context7** MCP.
- Needs browser vérification → flag the **playwright** MCP loop.
- Produced a durable décision → persist via **wiki-ingest** on the way out.
Propose the route, then execute it (or the framework owns the build, e.g. Superpowers).

### Phase 6 — Orchestration contract (only when the spec warrants delegating to agents)
The coordinator delegates; it does not do everything itself — but delegation is
**conditional**, not automatic (benchmark 2026-08-08,
`docs/superpowers/specs/2026-08-08-orchestration-fleet-design.md`):

- **Small, well-scoped change → do it inline (solo).** Measured: a solo Sonnet ships
  a small feature for ~$0.07/20s, tests green. Do NOT spin up a fleet for a 20-line change.
- **Transversal / risky change → delegate.** Then, and only then:
  - **explore** → `researcher` (Haiku — quality-parity with Sonnet, 3× cheaper).
  - **plan** → `architect` (Sonnet) — but only when the decomposition/impact isn't
    obvious (an architect run costs ~$0.27/2min). Otherwise plan inline.
  - **implement** → `implementer` (Sonnet). **review** → `reviewer`/`reviewer-sonnet` (Sonnet).
  - Never an Opus subagent (CLAUDE.md §6). The benchmark found no quality gain from Opus-architect.
- **Goal-driven delegation.** Every dispatch carries **verifiable success criteria +
  a verification step** — not just a task. No agent reports "done" without fresh proof
  (Iron Law, `diamant-code`). The coordinator checks the proof, it doesn't take "done" on trust.
- **Empty async result ≠ failure — resume before concluding.** An `Agent()` dispatch that
  hits its `maxTurns` cap mid-exploration (still issuing tool calls, no final text yet) can
  notify with an empty result — silently, no error. Proven live 2026-08-08 (backfill JSONL
  e2e run): `researcher` and `architect` both did this at `maxTurns:6`. Before treating a
  dispatch as `BLOCKED` or re-running it from scratch, `SendMessage` the same agent asking
  for its final structured output — recovered cleanly 2/2 times. `maxTurns` was raised
  (researcher 12, architect 10) but the recovery step stays as a safety net.
- **Parallelize only genuinely independent tasks; never two agents on the same file.**
- **Correction cycle = `diamant-loop`** (bounded: ledger-counted iterations, `max-iter`,
  human escalation at floor). **Never** drive it with a fixed-interval `/loop` (runaway risk,
  `diamant-loop/SKILL.md`).
- **Apply the Diamant discipline at each checkpoint** (thesis → antithesis → synthesis) — this
  IS the "directeur/analyste" layer; no extra agent, the coordinator carries it.
- **Stop if critical info is missing; confirm before any destructive/prod action.**

## Interaction style
Direct, not deferential. Challenge ideas on merit. One tight batch of questions
beats twenty trickled out. Never pad with praise. The user's time is the budget.

## Output contract
End Phase 1–3 having either (a) asked the minimal questions, or (b) declared the
request already clear. Always finish a completed forge with the spec + the chosen
route, then proceed.
