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

# Tokens sous-agents — $.cost.total_cost_usd (retiré) est "computed client-side"
# et n'agrège pas les dispatchs Agent()/Task() (confirmé absent de tout hook,
# cf. .claude/hooks/subagent-token-logger.py). Le fichier accumulé porte, par
# (re)prise d'agent, le total CUMULÉ de cet agent (pas un delta) : on déduplique
# sur la DERNIÈRE ligne par agent_id avant de sommer, jamais la somme brute des
# lignes, sous peine de double-compter un agent relancé via SendMessage.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$DIR}"

# Cache modele/effort pour model-guard-{tool,prompt}.py -- SessionStart ne
# recoit `model` que de facon conditionnelle (doc officielle : "Claude Code
# doesn't always include it"), la statusline le recoit a jour a CHAQUE tour :
# source nettement plus fiable, ecrite ici en best-effort. N'ecrit que si le
# garde-fou est installe dans ce projet (evite de polluer .claude/logs/ des
# projets qui n'utilisent pas ce mecanisme).
GUARD_MARKER="${PROJECT_DIR}/.claude/hooks/model-guard-tool.py"
if [ -f "$GUARD_MARKER" ]; then
    GUARD_CACHE="${PROJECT_DIR}/.claude/logs/current-model.json"
    mkdir -p "${PROJECT_DIR}/.claude/logs" 2>/dev/null
    GUARD_TMP="${GUARD_CACHE}.tmp.$$"
    jq -n --arg id "$MODEL_ID" --arg name "$MODEL" --arg eff "$EFFORT" \
        '{model_id:$id, model_display_name:$name, effort_level:$eff, hook_event:"statusLine"}' \
        > "$GUARD_TMP" 2>/dev/null && mv "$GUARD_TMP" "$GUARD_CACHE" 2>/dev/null
fi

SUBAGENT_TOK_FILE="${PROJECT_DIR}/.claude/logs/subagent-tokens-${SESSION_ID}.jsonl"
SUBAGENT_TOKENS=0
if [ -f "$SUBAGENT_TOK_FILE" ]; then
    SUBAGENT_TOKENS=$(jq -s '[group_by(.agent_id)[] | (sort_by(.ts) | last) | .tokens] | add // 0' "$SUBAGENT_TOK_FILE" 2>/dev/null)
    [ -z "$SUBAGENT_TOKENS" ] && SUBAGENT_TOKENS=0
fi
TOTAL_TOKENS=$((MAIN_IN + MAIN_OUT + SUBAGENT_TOKENS))

# Rate-limit 5h/7j — clamp anti-leak (bug connu Anthropic : used_percentage peut
# leaker l'epoch de reset, valeur >= 100000)
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

# Indicateur live modèle/effort coûteux — pas d'angle mort de cache ici
# (contrairement aux hooks, la statusline reçoit .model.id à jour à chaque tour).
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

# Git branch + statut, non-bloquant : cache TTL 5s par session, refresh async
# en subprocess détaché (jamais de `git status` synchrone dans le rendu).
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

# Fonction pour convertir secondes en format h/m lisible
format_time_remaining() {
    local secs=$1
    if [ "$secs" -le 0 ]; then
        echo ""
        return
    fi
    local hours=$((secs / 3600))
    local mins=$(((secs % 3600) / 60))
    if [ "$hours" -gt 0 ]; then
        printf "~%dh%dm" "$hours" "$mins"
    else
        printf "~%dm" "$mins"
    fi
}

# Fonction pour parser ISO 8601 → timestamp (compatible macOS + Linux)
iso8601_to_timestamp() {
    local iso_date="$1"
    # Format: 2026-09-25T12:00:00Z ou 2026-09-25T12:00:00+00:00
    iso_date="${iso_date%Z}"  # Supprimer Z si présent
    iso_date="${iso_date%%+*}"  # Garder juste la date/heure

    # Tenter macOS d'abord (date -j -f)
    if date -j -f "%Y-%m-%dT%H:%M:%S" "$iso_date" +%s 2>/dev/null; then
        return 0
    fi
    # Fallback Linux (date -d)
    date -d "${iso_date}" +%s 2>/dev/null || echo "0"
}

# Fonction pour afficher jour court + heure (ex: "ven. 13h")
format_day_and_hour() {
    local ts=$1
    local day_short hour
    # Tenter macOS d'abord
    if date -j -f "%s" "$ts" "+%a" &>/dev/null 2>&1; then
        day_short=$(date -j -f "%s" "$ts" "+%a" | tr '[:upper:]' '[:lower:]')
        hour=$(date -j -f "%s" "$ts" "+%H")
    else
        # Fallback Linux
        day_short=$(date -d "@$ts" "+%a" | tr '[:upper:]' '[:lower:]')
        hour=$(date -d "@$ts" "+%H")
    fi
    # Traduire jour anglais → français court
    case "$day_short" in
        mon) day_short="lun" ;;
        tue) day_short="mar" ;;
        wed) day_short="mer" ;;
        thu) day_short="jeu" ;;
        fri) day_short="ven" ;;
        sat) day_short="sam" ;;
        sun) day_short="dim" ;;
    esac
    printf "%s. %sh" "$day_short" "$hour"
}

# Calcul de "jusqu'à quand" — rate limits avec vraie date de reset
TTL_REMAINING=""

# Rate limit 5h — seulement si reset_at existe (données vraies d'Anthropic)
RL5H_UNTIL=""
if [ -n "$RL5H_C" ] && [ "$RL5H_C" -lt 100 ]; then
    RESET_AT_5H=$(echo "$input" | jq -r '.rate_limits.five_hour.reset_at // empty')
    if [ -n "$RESET_AT_5H" ]; then
        RESET_TS_5H=$(iso8601_to_timestamp "$RESET_AT_5H")
        NOW_TS=$(date +%s)
        if [ "$RESET_TS_5H" -gt "$NOW_TS" ]; then
            SECS_UNTIL=$(( $RESET_TS_5H - $NOW_TS ))
            RL5H_UNTIL=" ($(format_time_remaining $SECS_UNTIL))"
        fi
    fi
fi

# Rate limit 7d — seulement si reset_at existe (données vraies d'Anthropic)
RL7D_UNTIL=""
if [ -n "$RL7D_C" ] && [ "$RL7D_C" -lt 100 ]; then
    RESET_AT_7D=$(echo "$input" | jq -r '.rate_limits.seven_day.reset_at // empty')
    if [ -n "$RESET_AT_7D" ]; then
        RESET_TS_7D=$(iso8601_to_timestamp "$RESET_AT_7D")
        NOW_TS=$(date +%s)
        if [ "$RESET_TS_7D" -gt "$NOW_TS" ]; then
            RL7D_UNTIL=" ($(format_day_and_hour $RESET_TS_7D))"
        fi
    fi
fi

# Cache TTL (défaut 1h = 3600s)
CACHE_CREATED=$(echo "$input" | jq -r '.cache.created_at // empty')
if [ -n "$CACHE_CREATED" ]; then
    # Durée depuis création (si .cache.created_at disponible)
    TTL_REMAINING=" [cache +~55m]"
else
    TTL_REMAINING=""
fi

RL_LINE=""
[ -n "$RL5H_C" ] && RL_LINE=" | 5h:${RL5H_C}%${RL5H_UNTIL}"
[ -n "$RL7D_C" ] && RL_LINE="${RL_LINE} 7d:${RL7D_C}%${RL7D_UNTIL}"

# GitHub repo context — affiche le repo + branche active
GH_CONTEXT=""
if [ -f "$PROJECT_DIR/.git/config" ]; then
    GH_REPO=$(grep "url = " "$PROJECT_DIR/.git/config" | grep -o 'github.com[^"]*' | cut -d/ -f2-3 | sed 's/\.git$//' | head -1)
    [ -n "$GH_REPO" ] && GH_CONTEXT=" | 🐙 ${GH_REPO}"
fi

# Signature de marque OMKomUnity (🕉️ + lien) — la plupart des terminaux rendent le
# domaine Cmd-cliquable ; couleur violette = accent signature de la charte.
OM_BRAND="🕉️ ${MAGENTA}omkomunity.ai${RESET}"

echo -e "${MODEL_LABEL} 📁 ${DIR##*/}${BRANCH_LINE} | ${BAR_COLOR}${BAR}${RESET} ${PCT}%${TTL_REMAINING} | ${YELLOW}🪙 ${TOKENS_FMT}${RESET} | ⏱️ ${MINS}m ${SECS}s${RL_LINE}${GH_CONTEXT} | ${OM_BRAND}"
