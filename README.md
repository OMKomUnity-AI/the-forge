# the-forge 🔨

**Reusable building blocks for orchestrating AI agents** — distilled from real [Claude Code](https://claude.com/claude-code) work, made standalone and dependency-light. By [OMKomUnity](https://omkomunity.ai).

> *AI orchestrated, not endured.*

`the-forge` is a **collection of small, self-contained tools** for [Claude Code](https://claude.com/claude-code). **One folder = one brick**, each with its own README, its own `.claude/` payload, and its own example config. Grab the one you need, drop it into your project, ignore the rest. No framework, no build step, no runtime dependencies beyond what your machine already has.

## Contents

- [Quick start](#quick-start)
- [The bricks](#the-bricks)
- [Repository structure](#repository-structure)
- [Requirements](#requirements)
- [Installing a brick](#installing-a-brick)
- [How it works](#how-it-works)
- [FAQ](#faq)
- [Add your own brick](#add-your-own-brick)
- [License](#license)

## Quick start

Get the prompt-reforging layer running in under a minute:

```bash
# 1. Clone
git clone https://github.com/OMKomUnity-AI/the-forge.git

# 2. Copy the brick into YOUR Claude Code project
cp -R the-forge/om-prompt-reforge/.claude/hooks/*        your-project/.claude/hooks/
cp -R the-forge/om-prompt-reforge/.claude/skills/forge   your-project/.claude/skills/

# 3. Try a hook in isolation (no wiring needed) — a vague prompt gets reforged:
echo '{"prompt":"improve the perf"}' | python3 your-project/.claude/hooks/prompt-enricher.py
```

To wire it in permanently, merge the brick's `settings.example.json` into your project's `.claude/settings.json` and restart Claude Code. Exact steps live in each brick's README.

## The bricks

### 🕉️ [om-prompt-reforge](./om-prompt-reforge) — sharpen every prompt before Claude acts

Two `UserPromptSubmit` hooks. A **0-LLM regex gate** decides whether your prompt is worth sharpening; only then does an isolated **Haiku** call reforge it into one explicit sentence — with **no API key** (it rides on your `claude` CLI subscription), **never blocking**, and **language-preserving**.

```
You    ▸  improve the perf

🕉️     ▸  Improve performance of an unspecified component/system by an unspecified
          metric (latency/throughput/memory/cost) without a defined baseline or
          success target.
```

→ **[Full guide, install, and the engineering story](./om-prompt-reforge)**

### 🕉️ [om-statusline](./om-statusline) — a dense, branded status line

Everything worth having at the bottom of a Claude Code session, in pure `bash` + `jq`: model & effort (with a ⚠ cost warning on Opus/Fable), current folder, git branch/dirty count, context bar, token count (main + sub-agents), session timer, and 5h/7d rate-limit usage.

![The om-statusline in action](./om-statusline/demo.gif)

```
[Opus 4.8 ⚠max] 📁 my-project | 🌿 main +2~5 | ██░░░░░░░░ 20% | 🪙 203.7k | ⏱️ 35m 12s | 5h:30% 7d:64% | 🕉️ omkomunity.ai
```

→ **[Full guide and install](./om-statusline)**

## Repository structure

```
the-forge/
├── README.md                 ← you are here
├── LICENSE                   ← MIT
├── om-prompt-reforge/        ← brick 1: prompt reforging
│   ├── README.md
│   ├── settings.example.json
│   └── .claude/
│       ├── hooks/
│       │   ├── prompt-router.py      (0-LLM gate)
│       │   └── prompt-enricher.py    (Haiku enrichment)
│       └── skills/forge/SKILL.md
└── om-statusline/            ← brick 2: status line
    ├── README.md
    ├── settings.example.json
    └── statusline.sh
```

## Requirements

| Brick | Needs |
|---|---|
| `om-prompt-reforge` | **Python 3** (standard library only — no `pip install`). For the Haiku enrichment: the **`claude` CLI** installed and authenticated (Claude Pro/Max). The router alone needs nothing but Python. |
| `om-statusline` | `bash`, `jq`, `git`. macOS and Linux. |

No API keys. No external services. Nothing to `pip install` or `npm install`.

## Installing a brick

Every brick follows the same shape, so installation is always the same three moves:

1. **Copy** the brick's files into your project (or into your home `~/.claude/` for global tools like the status line).
2. **Merge** the brick's `settings.example.json` into the matching `settings.json` — project-level `.claude/settings.json`, or global `~/.claude/settings.json`.
3. **Restart** Claude Code so it picks up the hooks / status line.

Each brick's README gives the exact paths and a copy-paste block. Nothing is hidden or magic — the whole point is that you can read every line before you trust it.

## How it works

A few principles are shared across the bricks:

- **0-LLM first.** The prompt router is pure regex/stdlib: zero tokens, zero latency. It decides whether an LLM is even worth calling, so trivial prompts (`git status`, `thanks`) never hit a model.
- **No API key.** The enricher shells out to your authenticated `claude` CLI, so it rides on your subscription — no key to manage, no separate billing.
- **Never blocks.** Hooks are fail-safe: any error or timeout → silence, `exit 0`, your prompt proceeds untouched. A tool should never get between you and your work.
- **Language-preserving.** The reforged sentence comes back in the language you wrote in.
- **Honest about cost.** The one LLM call (the enricher, on non-trivial prompts only) adds ~5–24s and ~$0.007–0.03. Everything else is free. Each brick's README shows the measured numbers and the evaluation that shaped the design.

## FAQ

**Do I need an API key?** No. The enricher uses your `claude` CLI's existing auth. To run it *outside* Claude Code (CI, another host), each README documents an optional `ANTHROPIC_API_KEY` SDK variant.

**Does it slow down or block my prompts?** It never blocks. The router adds nothing (0-LLM). The enricher adds ~5–24s **only on non-trivial prompts**, and even then it just adds context — your original message is untouched.

**What does it cost?** Trivial prompts: nothing (the gate skips the model). Non-trivial prompts: ~$0.007–0.03 each via the `claude` CLI. The status line is free.

**Do I have to install both hooks?** No. `prompt-router` works on its own as a 0-LLM gate; `prompt-enricher` is optional and reuses the router's logic.

**Which languages does it support?** Any — the enrichment is language-preserving (French in → French out, English in → English out).

**Where does the reforged prompt go?** Into `additionalContext`. Claude sees the sharpened intent, but your original message stays exactly as you typed it.

## Add your own brick

`the-forge` is built to grow, one folder at a time. A good brick is:

- **Self-contained** — its own folder, README, and `settings.example.json`.
- **Well-named** — the folder name says what it does.
- **Dependency-light** — prefer stdlib / `bash` over frameworks.
- **Clean** — no secrets, no client data, MIT-compatible.

Open an issue or a PR. *(Heads-up: this repo has secret-scanning push protection enabled — a stray token will be rejected before it ever lands.)*

## License

[MIT](./LICENSE) — © 2026 OMKomUnity. Use it, fork it, ship it.

---

Built & maintained by **[OMKomUnity](https://omkomunity.ai)**, an AI-native studio. *AI orchestrated, not endured.*
