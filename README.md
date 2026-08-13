# the-forge 🔨

Reusable building blocks for orchestrating AI agents — distilled from real [Claude Code](https://claude.com/claude-code) work, made standalone. By [OMKomUnity](https://omkomunity.ai).

> *AI orchestrated, not endured.*

This repo is a **collection**: **one folder per shareable block**, well-named and self-contained (its own README, its own `.claude/`, its own example config). Take what you need, ignore the rest.

## Components

| Folder | What it is |
|---|---|
| [**`om-prompt-reforge/`**](./om-prompt-reforge) | Two `UserPromptSubmit` hooks for Claude Code: a **0-LLM** gate that triggers the `forge` skill on vague prompts, + a **Haiku** enricher that reforges your intent into one sentence (pink 🕉️, language-preserving) — **no API key**, **never blocks**. |
| [**`om-statusline/`**](./om-statusline) | The OMKomUnity status line for Claude Code: model + effort (⚠ on costly Opus/Fable), folder, git branch/dirty, context bar, token count (main + sub-agents), session timer, 5h/7d rate-limit usage, and the 🕉️ omkomunity.ai brand mark. Pure `bash` + `jq`. |

*(More blocks will be added, one per folder.)*

## License

[MIT](./LICENSE) — © 2026 OMKomUnity.

---

Built & maintained by **[OMKomUnity](https://omkomunity.ai)**, an AI-native studio. *AI orchestrated, not endured.*
