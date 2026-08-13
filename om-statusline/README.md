# om-statusline 🕉️

The **OMKomUnity status line** for [Claude Code](https://claude.com/claude-code) — the little bar at the bottom of your session. One `bash` script, no dependencies beyond `jq` and `git`. Part of [the-forge](../README.md), an open toolkit by [OMKomUnity](https://omkomunity.ai).

![The om-statusline in action: the bar updates live as context fills up (green to yellow to red) and the model switches to a costly Opus (with a cost warning), rendered by the real script.](./demo.gif)

```
[Opus 4.8 ⚠max] 📁 my-project | 🌿 main +2~5 | ██░░░░░░░░ 20% | 🪙 203.7k | ⏱️ 35m 12s | 5h:30% 7d:64% | 🕉️ omkomunity.ai
```

## What each segment shows

| Segment | Meaning |
|---|---|
| `[Opus 4.8 ⚠max]` | Model + reasoning effort. Cyan normally; **bold red with ⚠** when a costly model (Opus/Fable) runs at high/xhigh/max effort — a live cost guard, visual only. |
| `📁 my-project` | Current directory (basename). |
| `🌿 main +2~5` | Git branch, `+staged` `~modified`. Refreshed **asynchronously** (5s cache, detached subprocess) — never a blocking `git status` in the render. |
| `██░░░░░░░░ 20%` | Context window used. Green → yellow (≥70%) → red (≥90%). |
| `🪙 203.7k` | Total tokens: main context (+ sub-agents, see below). |
| `⏱️ 35m 12s` | Session duration. |
| `5h:30% 7d:64%` | Rate-limit usage (5-hour and 7-day windows), clamped against a known epoch-leak edge case. |
| `🕉️ omkomunity.ai` | The OMKomUnity brand mark (magenta; Cmd-clickable in most terminals). |

## Installation

```bash
# 1. Copy the script somewhere on your machine
cp om-statusline/statusline.sh ~/.claude/statusline.sh
chmod +x ~/.claude/statusline.sh
```

2. Add the `statusLine` block from [`settings.example.json`](./settings.example.json) to your **global** `~/.claude/settings.json` (it's a cross-project display preference, not a per-project hook):

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.sh"
  }
}
```

3. Restart Claude Code.

## Requirements

`bash`, `jq`, `git`. macOS and Linux (uses `stat -f`/`stat -c` fallbacks).

## Sub-agent token count (optional)

The `🪙` segment adds sub-agent tokens **only if** a `.claude/logs/subagent-tokens-<session>.jsonl` file exists — produced by a separate `subagent-token-logger.py` (`SubagentStop`) hook, **not included here**. Without it, `🪙` counts the main context; the status line is fail-safe and works either way. (Claude Code's `SubagentStop` payload carries no token field, and `$.cost.total_cost_usd` does not aggregate `Agent()`/`Task()` dispatches — hence the external logger.)

## Design notes

- **Non-blocking by construction**: the only potentially slow call (`git status`) is cached (5s TTL) and refreshed in a detached background subprocess. The render never waits on git.
- **Live model guard, no cache blind spot**: unlike hooks, the status line receives `.model.id`/`.effort.level` fresh every turn — so the ⚠ cost warning is always current.
- **Rate-limit clamp**: `used_percentage` can occasionally leak a reset epoch (a large bogus value); the script clamps anything `≥ 100000` (or `NaN`) to `0`.

---

Built & maintained by **[OMKomUnity](https://omkomunity.ai)** — *AI orchestrated, not endured.*
Licensed under [MIT](../LICENSE) — © 2026 OMKomUnity.
