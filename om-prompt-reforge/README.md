# om-prompt-reforge 🕉️

Une couche de **reforge de prompt** pour [Claude Code](https://claude.com/claude-code), en deux hooks `UserPromptSubmit` :

1. **`prompt-router.py`** — un *gate* **0-LLM** (regex + stdlib) qui, quand votre prompt est vague, large ou discutable, injecte une instruction demandant à Claude de lancer d'abord le skill `forge` (pré-qualifier, challenger, réécrire en spec). Coût : **zéro token, zéro latence**.
2. **`prompt-enricher.py`** — sur un prompt non-trivial *seulement*, un appel **Haiku** isolé produit une **reformulation en une phrase** qui rend l'intention explicite, affichée en **rose** préfixée du **🕉️**. Il **ne bloque jamais** votre prompt et **ne demande aucune clé API**.

> Le principe : **le gate 0-LLM protège le LLM de lui-même.** L'enricher n'appelle Haiku que si le routeur regex a d'abord jugé le prompt non-trivial. « merci », « git status » ou « oui » ne coûtent donc rien.

---

## Before / after

Vous tapez un prompt sous-spécifié ; le 🕉️ vous renvoie l'intention explicitée *avant* que Claude ne parte sur une hypothèse :

```
Vous  ▸  améliore la perf

🕉️    ▸  Améliore la performance (vitesse d'exécution, latence, throughput, ou
         coût infra) du système/code/pipeline [spécifier la cible], en priorisant
         les gains mesurables et sans régression fonctionnelle.
```

*(Sortie réelle de ce hook, capturée au test. Comme tout appel LLM, la formulation varie légèrement d'un run à l'autre — c'est la nature de l'enrichissement, pas un bug.)*

La reformulation part **en contexte** (`additionalContext`) : Claude la voit, la traite, mais votre message original reste intact. Rien n'est réécrit à votre place.

---

## Ce qu'il vous faut

- **Python 3** (stdlib uniquement — aucune dépendance `pip`).
- Pour l'enricher : le **CLI `claude`** installé et authentifié (abonnement Claude Pro/Max). L'enricher fait un `claude -p` en sous-processus et **ride sur cette authentification** — pas de clé API à gérer.
- Le **routeur seul** ne requiert rien d'autre que Python : si vous ne voulez que le gate 0-LLM, n'installez que lui.

---

## Installation

1. **Copiez les fichiers** dans le `.claude/` de *votre* projet :

   ```
   votre-projet/
   └── .claude/
       ├── hooks/
       │   ├── prompt-router.py      # les deux hooks doivent rester co-localisés :
       │   └── prompt-enricher.py    # l'enricher importe classify() du routeur par chemin
       └── skills/
           └── forge/
               └── SKILL.md
   ```

   Depuis ce dépôt :
   ```bash
   cp -R om-prompt-reforge/.claude/hooks/*        votre-projet/.claude/hooks/
   cp -R om-prompt-reforge/.claude/skills/forge   votre-projet/.claude/skills/
   ```

2. **Câblez les hooks** : fusionnez [`settings.example.json`](./settings.example.json) dans le `.claude/settings.json` de votre projet. L'ordre est impératif — **routeur puis enricher** :

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         { "hooks": [ { "type": "command",
           "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/prompt-router.py\"" } ] },
         { "hooks": [ { "type": "command",
           "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/prompt-enricher.py\"",
           "timeout": 30,
           "statusMessage": "Enrichissement du prompt (Haiku)..." } ] }
       ]
     }
   }
   ```

3. **Redémarrez Claude Code** (les hooks se chargent au démarrage de session).

---

## Démo (test en isolation, sans rien câbler)

Chaque hook lit un JSON `{"prompt": "..."}` sur stdin. On peut donc les exercer à la main :

```bash
cd om-prompt-reforge/.claude/hooks

# Routeur — prompt trivial : silence total (le prompt passe intact)
echo '{"prompt":"merci"}'                        | python3 prompt-router.py    # (aucune sortie)

# Routeur — prompt non-trivial : injecte l'instruction "lance forge"
echo '{"prompt":"construis-moi un système de cache"}' | python3 prompt-router.py

# Enricher — prompt trivial : gate 0-LLM, AUCUN appel Haiku
echo '{"prompt":"git status"}'                   | python3 prompt-enricher.py

# Enricher — prompt non-trivial : appel Haiku réel (5-24 s), reformulation rose 🕉️
echo '{"prompt":"améliore la perf"}'             | python3 prompt-enricher.py
```

Le dernier appel consomme votre abonnement (≈ quelques centimes) et prend quelques secondes : c'est le comportement réel de l'enricher.

---

## Modèle & refresh

Le modèle Haiku est **paramétrable par variable d'environnement**, avec un défaut épinglé pour la reproductibilité :

```bash
# Défaut si non défini : claude-haiku-4-5-20251001
export FORGE_ENRICHER_MODEL="claude-haiku-4-5-20251001"
```

Quand un Haiku plus récent sort, pointez la variable dessus (ou changez le défaut en tête de `prompt-enricher.py`). Gardez un identifiant **daté** plutôt qu'un alias flottant si vous voulez des reformulations stables dans le temps.

### Variante SDK (optionnelle, portable hors Claude Code)

Par défaut, l'enricher fait un `claude -p` : **zéro clé**, mais il faut que le CLI `claude` soit installé et connecté. Si vous voulez faire tourner l'enricher **là où le CLI n'existe pas** (CI, autre machine, autre agent), remplacez l'appel sous-processus par un appel direct au SDK Anthropic authentifié via `ANTHROPIC_API_KEY`. Contrepartie assumée : cette clé est **facturée séparément** de l'abonnement (elle ne ride pas dessus). Variante documentée, non implémentée ici — le défaut zéro-clé couvre l'usage Claude Code.

---

## L'histoire d'ingénierie

Ce hook est le produit d'une évaluation qui a **réfuté son intuition de départ**, puis l'a nuancée.

**L'intuition** : « il faut un LLM pour comprendre un prompt mieux qu'un regex. » Six configurations ont été comparées sur un corpus de 14 cas synthétiques + 10 vrais prompts échantillonnés :

| Config | Mécanisme | Score | Latence | Appels LLM |
|---|---|---|---|---|
| **V0** | routeur regex **0-LLM** | **12/14** | **0 s** | **0** |
| V1 | clarificateur LLM solo | 12/14 | 10,4 s | 14/14 |
| V2 | chaîné (router + V1) | 10/14 | 10,4 s | 14/14 |
| V3 | clarificateur strict | 9/14 | 9,2 s | 14/14 |
| V4 | clarificateur permissif | 13/14 | 8,3 s | 14/14 |
| V5 | hybride séquentiel | 12/14 | ~4,5 s | 6/14 |

**Le verdict mesuré** : le 0-LLM (V0) **égale ou bat 4 des 5 variantes LLM**. La seule qui le dépasse (V4) le fait pour **+1 cas** au prix de **+8,3 s sur chaque prompt** et d'un appel LLM systématique. Sur 10 vrais prompts, la variante LLM n'a jamais divergé utilement du routeur — seulement ajouté de la latence.

**Le rebond** : la métrique testée (bloquer / laisser passer) était *incomplète*. La vraie valeur d'un LLM ici n'est pas la décision binaire — c'est la **qualité de la reformulation** elle-même, que le 0-LLM ne peut structurellement pas produire (il n'a qu'un déclencheur vers `forge`, pas de champ « reformulation »).

**Le mécanisme qui a tout débloqué** : plutôt qu'un `claude -p` « nu » (qui exige `ANTHROPIC_API_KEY` et casse l'auth abonnement), un **agent éphémère inline** — `--agents '{...}' --allowedTools "" --settings '{"disableAllHooks":true}'`, `maxTurns:1`, `effort:low`. Résultat : pas de rechargement des hooks de session, pas de risque que le modèle *exécute* la tâche au lieu de la reformuler, et surtout **consommation de l'abonnement, pas de facturation API séparée**. Validé 21/21 sans timeout.

**Le design retenu** — celui de ce dépôt : **gate 0-LLM (routeur) PUIS enrichissement Haiku non-bloquant (enricher).** On capture la valeur (la reformulation) sans le risque (jamais de faux-blocage, zéro appel sur les prompts triviaux). C'est la synthèse d'une thèse réfutée par la mesure, pas un réflexe « mets un LLM partout ».

---

## Limites assumées

- **Latence** : +5 à 24 s sur les prompts **non-triviaux** (plancher inhérent au modèle, mesuré sur 21 appels). **0 s** sur les prompts triviaux (gate 0-LLM).
- **Non-déterminisme** : la reformulation varie légèrement d'un appel à l'autre — trait attendu d'un LLM.
- **Coût** : ≈ 0,007–0,03 $ par prompt non-trivial via `claude -p` (le sous-processus recharge l'overhead du CLI). Les prompts triviaux ne coûtent rien.
- **Dépendance** : l'enricher exige le CLI `claude`. Le routeur, lui, est du pur stdlib et fonctionne partout.
- **Fail-safe** : toute erreur ou timeout d'un hook → silence, `exit 0`, prompt inchangé. Un hook ne doit jamais se mettre entre vous et votre travail.

---

## Adapter `forge` à votre écosystème

Le skill `forge` et l'instruction injectée par le routeur mentionnent des skills de l'écosystème d'origine (`new-feature`, `wiki-ingest`, `context7`/`playwright` MCP…). Ce sont des **exemples de cibles de routage**. Dans une installation minimale, `forge` s'arrête naturellement à la **spec** (Phase 4) — remplacez la liste par vos propres skills, ou laissez-la : `forge` se dégrade proprement.

---

## Licence

[MIT](../LICENSE) — © 2026 OMKomUnity.
