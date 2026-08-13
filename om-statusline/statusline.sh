#!/bin/bash
input=$(cat)

MODEL=$(echo "$input" | jq -r '.model.display_name')
MODEL_ID=$(echo "$input" | jq -r '.model.id // ""' | tr '[:upper:]' '[:lower:]')
EFFORT=$(echo "$input" | jq -r '.effort.level // empty')
DIR=$(echo "$input" | jq -r '.workspace.current_dir')
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
DURATION_MS=$(echo "$input" | jq -r '.cost.total_duration_ms // 0')
SESSION_ID=$(echo "$input" | jq -r '.session_id // "nosession"')
MAIN_IN=$(echo "$input" | jq -r '.context_window.total_input_tokens // 0')
MAIN_OUT=$(echo "$input" | jq -r '.context_window.total_output_tokens // 0')

# Sub-agent tokens — $.cost.total_cost_usd (removed) is "computed client-side"
# and does not aggregate Agent()/Task() dispatches (confirmed absent from every hook,
# see subagent-token-logger.py). The accumulated file carries, per agent (re)start,
# that agent's CUMULATIVE total (not a delta): dedupe on the LAST line per agent_id
# before summing, never the raw sum of all lines, to avoid double-counting an agent
# relaunched via SendMessage.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$DIR}"
SUBAGENT_TOK_FILE="${PROJECT_DIR}/.claude/logs/subagent-tokens-${SESSION_ID}.jsonl"
SUBAGENT_TOKENS=0
if [ -f "$SUBAGENT_TOK_FILE" ]; then
    SUBAGENT_TOKENS=$(jq -s '[group_by(.agent_id)[] | (sort_by(.ts) | last) | .tokens] | add // 0' "$SUBAGENT_TOK_FILE" 2>/dev/null)
    [ -z "$SUBAGENT_TOKENS" ] && SUBAGENT_TOKENS=0
fi
TOTAL_TOKENS=$((MAIN_IN + MAIN_OUT + SUBAGENT_TOKENS))

# Rate-limit 5h/7d — anti-leak clamp (known edge case: used_percentage can leak the
# reset epoch, value >= 100000)
RL5H=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
RL7D=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
clamp_pct() {
    local v="$1"
    [ -z "$v" ] && return
    awk -v x="$v" 'BEGIN{ if (x!=x || x>=100000) print 0; else printf "%d", x }'
}
RL5H_C=$(clamp_pct "$RL5H")
RL7D_C=$(clamp_pct "$RL7D")

CYAN='\033[36m'; GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; MAGENTA='\033[35m'; BOLD='\033[1m'; RESET='\033[0m'

# Live costly-model/effort indicator — no cache blind spot here
# (unlike hooks, the status line receives an up-to-date .model.id every turn).
MODEL_LABEL="${CYAN}[$MODEL]${RESET}"
case "$MODEL_ID" in
    *opus*|*fable*)
        case "$EFFORT" in
            high|xhigh|max) MODEL_LABEL="${BOLD}${RED}[$MODEL ⚠${EFFORT}]${RESET}" ;;
            *) MODEL_LABEL="${YELLOW}[$MODEL]${RESET}" ;;
        esac
        ;;
esac

if [ "$PCT" -ge 90 ]; then BAR_COLOR="$RED"
elif [ "$PCT" -ge 70 ]; then BAR_COLOR="$YELLOW"
else BAR_COLOR="$GREEN"; fi

FILLED=$((PCT / 10)); EMPTY=$((10 - FILLED))
printf -v FILL "%${FILLED}s"; printf -v PAD "%${EMPTY}s"
BAR="${FILL// /█}${PAD// /░}"

MINS=$((DURATION_MS / 60000)); SECS=$(((DURATION_MS % 60000) / 1000))
TOKENS_FMT=$(LC_ALL=C awk -v n="$TOTAL_TOKENS" 'BEGIN{
    if (n>=1000000) printf "%.1fM", n/1000000;
    else if (n>=1000) printf "%.1fk", n/1000;
    else printf "%d", n;
}')

# Git branch + status, non-blocking: 5s TTL cache per session, async refresh
# in a detached subprocess (never a synchronous `git status` in the render).
BRANCH_LINE=""
if git rev-parse --git-dir > /dev/null 2>&1; then
    CACHE_FILE="/tmp/statusline-git-cache-${SESSION_ID}"
    CACHE_AGE=999
    if [ -f "$CACHE_FILE" ]; then
        NOW=$(date +%s)
        MTIME=$(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null || echo 0)
        CACHE_AGE=$((NOW - MTIME))
    fi
    if [ "$CACHE_AGE" -gt 5 ] && [ ! -f "${CACHE_FILE}.inflight" ]; then
        (
            touch "${CACHE_FILE}.inflight"
            BRANCH=$(git branch --show-current 2>/dev/null)
            STAGED=$(git diff --cached --numstat 2>/dev/null | wc -l | tr -d ' ')
            MODIFIED=$(git diff --numstat 2>/dev/null | wc -l | tr -d ' ')
            TMP="${CACHE_FILE}.tmp.$$"
            echo "${BRANCH}|${STAGED}|${MODIFIED}" > "$TMP" && mv "$TMP" "$CACHE_FILE"
            rm -f "${CACHE_FILE}.inflight"
        ) &
        disown 2>/dev/null
    fi
    if [ -f "$CACHE_FILE" ]; then
        IFS='|' read -r BRANCH STAGED MODIFIED < "$CACHE_FILE"
        GIT_STATUS=""
        [ -n "$STAGED" ] && [ "$STAGED" -gt 0 ] 2>/dev/null && GIT_STATUS="${GREEN}+${STAGED}${RESET}"
        [ -n "$MODIFIED" ] && [ "$MODIFIED" -gt 0 ] 2>/dev/null && GIT_STATUS="${GIT_STATUS}${YELLOW}~${MODIFIED}${RESET}"
        [ -n "$BRANCH" ] && BRANCH_LINE=" | 🌿 ${BRANCH} ${GIT_STATUS}"
    fi
fi

RL_LINE=""
[ -n "$RL5H_C" ] && RL_LINE=" | 5h:${RL5H_C}%"
[ -n "$RL7D_C" ] && RL_LINE="${RL_LINE} 7d:${RL7D_C}%"

# OMKomUnity brand mark (🕉️ + link) — most terminals render the domain
# Cmd-clickable; magenta = signature accent from the brand guidelines.
OM_BRAND="🕉️ ${MAGENTA}omkomunity.ai${RESET}"

echo -e "${MODEL_LABEL} 📁 ${DIR##*/}${BRANCH_LINE} | ${BAR_COLOR}${BAR}${RESET} ${PCT}% | ${YELLOW}🪙 ${TOKENS_FMT}${RESET} | ⏱️ ${MINS}m ${SECS}s${RL_LINE} | ${OM_BRAND}"
