# om-orchestration

**A side pane that tracks several orchestrated Claude Code sessions, live.**

When you run one session that reviews the work of several others, the state lives in six
terminals you can't see at once. A status line holds one line. A Markdown file lives outside
Claude Code. This mod draws the state **beside the transcript**, in the session where you
make the decisions.

## What it shows

- counters: in progress, awaiting review, blocked, done, planned
- one block per task, newest state first, with the session that owns it and how long it has run
- blocked and awaiting-review sections first — they are the ones that need you
- the last six journal entries

## How it works

Every session writes to one **append-only** journal, `.claude/orchestration/events.jsonl`.
The mod only ever **reads** it, and recomputes the state on each draw.

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
