# om-orchestration

**A side pane that tracks several orchestrated Claude Code sessions, live.**

When you run one session that reviews the work of several others, the state lives in six
terminals you can't see at once. A status line holds one line. A Markdown file lives outside
Claude Code. This mod draws the state **beside the transcript**, in the session where you
make the decisions.

## What it shows

- counters: in progress, awaiting review, blocked, done, planned
- every live session that has not closed yet, journal or not — with its last word and its context
- one block per task, newest state first, with the session that owns it and how long it has run
- blocked and awaiting-review sections first — they are the ones that need you
- the last six journal entries

## What you can do from it

The pane is not a read-only dashboard: every decision you can take on a running wave is one
press away, and **every one of them works with buttons alone** — so it works from the phone too.

- **answer a pending question** — the options the session offered, or free text where the
  surface draws a field
- **settle a review** — validate, or send it back (with a reason where there is a field)
- **move a task** — mark it done or blocked, add a note to the journal
- **arbitrate the model before launch** — haiku, sonnet or opus on a planned task, then launch
  it in a Terminal tab (two presses: the prompt is shown before anything autonomous runs on it)
- **steer a live session** — ask it for a status point, to commit, or to close; or send it a
  free-text instruction. The pane has no messenger of its own: it asks the session hosting it,
  the orchestrator, to pass the message on
- **stop or respawn a background session**
- **rewrite the rendered output**, pull Google Chat answers, refresh

Two honest limits, verified on 2026-10-16:

- the `claude` CLI exposes `stop`, `respawn`, `logs` and `attach` for **background** sessions
  only. An interactive session is steered by instruction, not by the CLI — so the pane does not
  draw a button that would fail.
- the **mobile** app draws no `Input` and no `Select` (type `Elements`, `claude-code`), and
  `$.ui.resolve` hands back an `Input` constructor anyway: the element is dropped silently. A
  field there is a mute hole, so the pane names the gap instead and keeps every action on a
  button.

## How it works

Every session writes to one **append-only** journal, `.claude/orchestration/events.jsonl`.
The mod **reads** it on each draw, and recomputes the state from it.

What the pane writes, it writes as `PROPRIETAIRE` — never under a session's name. That is the
rule that keeps the journal honest: a session says what it did, the owner says what he decided.
Reading `done` under the name of a session that never said it would be the one costly lie here.

Append-only is the point: several sessions write at the same time, and two sessions
rewriting one state file would lose each other's events. A line half-written by another
session is skipped, never fatal.

The last event for a `(session, task)` pair wins. Without that rule a late `done` would stay
hidden behind an older `blocked` — a dashboard that lies in the most expensive direction.

## Install

```bash
# one session, no install
claude --plugin-dir ./om-orchestration

# or add this repo as a marketplace, then
/plugin install om-orchestration@the-forge
```

Requires Claude Code **v2.1.287+** in the terminal (v2.1.286+ in the Desktop app's Code tab).
Check with `claude --version`.

Then, in the session you orchestrate from:

```
/orch
```

The pane opens beside the transcript in a wide fullscreen terminal, above the prompt
otherwise. `Esc` closes it. It redraws every 2 seconds — the journal is written by *other*
sessions, so no local event announces a change; a clock is the only honest trigger.

## Writing events

The mod reads; something else writes. Any command that appends one JSON object per line works:

```json
{"ts":"2026-10-15T08:42:11+00:00","session":"S1-maillage","task":"#1","kind":"start","msg":"...","ref":""}
```

`kind` is one of `planned`, `start`, `progress`, `review-request`, `review-ok`,
`review-reject`, `done`, `blocked`, `note`.

A reference writer is included: [`orch.py`](./orch.py).

```bash
python3 orch.py log --session S1-maillage --task "#1" --kind start --msg "what you began"
```

It also renders a Markdown file and a self-contained HTML panel from the same journal, for
the surfaces where a mod can't draw (the VS Code extension's chat panel, `claude -p`, cloud
sessions). All three read one journal, so they cannot drift apart.

## Where it draws

| Surface | Hooks run | Pane draws |
| :- | :- | :- |
| `claude` in a terminal | yes | yes |
| Desktop app, Code tab | yes | yes |
| VS Code extension chat panel | yes | no — use the HTML panel |
| `claude -p`, Agent SDK, cloud | yes | no — use the HTML panel |

## Trust

This mod reads one file: `.claude/orchestration/events.jsonl`, relative to the session's
working directory. It makes no network request, starts no process, and writes nothing.

Verify before you install, as you should with any mod:

```bash
claude plugin validate ./om-orchestration
```

The `hooks:` and `calls:` lines list every event it handles and everything it asks Claude
Code to do.

## Colours

The palette is OMKomUnity's. Terminals that don't render hex fall back to their nearest
colour — which is why **no information depends on colour alone**: every state also carries a
marker (`>`, `?`, `!`, `x`, `v`, `-`) and a labelled section.

---

By [OMKomUnity](https://omkomunity.ai) · part of [the-forge](../README.md)
