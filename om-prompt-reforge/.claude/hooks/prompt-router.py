#!/usr/bin/env python3
"""
prompt-router — UserPromptSubmit hook for claude-base.

What it does: inspects each prompt you submit and, when the prompt looks vague,
broad, or worth challenging, injects an instruction telling Claude to run the
`forge` discovery skill BEFORE doing any work — pre-qualify, challenge, ask the
high-leverage questions, rewrite into a spec, and route to the right skills.
When the prompt is already crisp (or trivial, or an explicit "just go"), it stays
silent and lets the prompt through untouched.

Platform note: UserPromptSubmit hooks cannot rewrite the prompt text in place
(no `updatedPrompt` field exists yet). They can only block or add context. So this
hook ADDS an orchestration instruction via additionalContext rather than editing
your words — which is actually what you want: your intent reaches Claude intact,
plus a directive to sharpen it first.

Fail-safe: any error → exit 0 with no output → the prompt proceeds normally.
The hook must never get between you and your work because of its own bug.

Contract (Claude Code hooks):
- stdin: JSON with at least {"prompt": "..."}
- stdout JSON: {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                        "additionalContext": "..."}}
- exit 0 always (we never block here; forge decides whether to proceed).
"""

import sys
import json
import re

# ---- tuning knobs ----------------------------------------------------------

# If the prompt contains any of these, the user is steering deliberately —
# never inject. (Explicit override + already invoking a skill/command.)
BYPASS = (
    r"\bjust go\b", r"\bno questions\b", r"\bskip (the )?discovery\b",
    r"\bfais (le )?direct\b", r"\bvas[- ]y\b", r"\bdirect\b",
    r"^/", r"\bforge\b",                      # already a slash-command or asking forge
)

# Trivial / fully-specified shapes → let through (cheap, unambiguous ops).
TRIVIAL = (
    r"^\s*(rename|format|lint|typo|fix the typo|run|cat|ls|git status)\b",
    r"^\s*(what|why|how|when|where|who)\b.*\?\s*$",   # a plain question
    r"^\s*(yes|no|ok|okay|oui|non|merci|thanks)\b",
)

# Signals the request is a real unit of work that benefits from discovery.
WORK_INTENT = (
    r"\b(build|create|make|implement|add|develop|design|refactor|rewrite|set up|setup)\b",
    r"\b(construis|crée|cree|fais|implémente|implemente|ajoute|développe|developpe|conçois|concois|refactor)\b",
    r"\b(feature|app|tool|system|pipeline|integration|component|service|agent|skill|workflow)\b",
    r"\b(i want|i'd like|let's|lets|can you|help me|j'aimerais|je veux|je voudrais|peux-tu|aide-moi)\b",
    r"\b(strategy|plan|approach|architecture|stratégie|strategie|architecture)\b",
)

# Vagueness markers that warrant challenge even on short prompts.
VAGUE = (
    r"\b(something|somehow|maybe|kind of|a bit|stuff|etc|or whatever)\b",
    r"\b(quelque chose|un truc|genre|peut-être|peut-etre|ou autre|etc)\b",
    r"\bbetter\b", r"\bimprove\b", r"\boptimi[sz]e\b", r"\bmieux\b", r"\baméliore\b",
)

MIN_WORDS_FOR_TRIVIAL = 6   # very short prompts are usually either trivial or vague

INJECTION = (
    "Before doing any work on this request, run the `forge` skill: pre-qualify the "
    "intent, surface and check hidden assumptions, challenge the framing and offer a "
    "smarter alternative if one exists, ask the highest-leverage questions in one "
    "batch (stop as soon as you can write verifiable success criteria — don't "
    "over-question), then rewrite the request into a compact spec and route it to "
    "the right skills (new-feature, document skills, marketing skills, context7/"
    "playwright MCP, wiki-ingest). If the request is in fact already crisp, say so "
    "in one line and proceed straight to the spec + route. Respect an explicit "
    "'just go'."
)


def _any(patterns, text):
    return any(re.search(p, text) for p in patterns)


def classify(prompt: str) -> bool:
    """Return True if forge discovery should be triggered."""
    p = prompt.strip()
    low = p.lower()

    if not p:
        return False
    if _any(BYPASS, low):
        return False
    if _any(TRIVIAL, low):
        return False

    words = len(p.split())
    vague = _any(VAGUE, low)
    work = _any(WORK_INTENT, low)

    # Real work intent → discovery pays off.
    if work:
        return True
    # Vague phrasing on anything non-trivial → worth challenging.
    if vague and words >= 3:
        return True
    # Very short, non-trivial, non-question prompts tend to under-specify.
    if words < MIN_WORDS_FOR_TRIVIAL and not low.endswith("?"):
        return True
    return False


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        prompt = data.get("prompt", "") or ""
    except Exception:
        # Can't parse input → do nothing, let the prompt through.
        return 0

    try:
        if classify(prompt):
            out = {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": INJECTION,
                }
            }
            sys.stdout.write(json.dumps(out))
    except Exception:
        # Any logic error → silent pass-through.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
