# the-forge 🔨

Briques réutilisables pour orchestrer des agents IA — distillées de vrai travail [Claude Code](https://claude.com/claude-code), rendues autonomes.

> *AI orchestrated, not endured.*

Ce dépôt est un **recueil** : **un dossier par brique partageable**, bien nommé et autonome (son propre README, son propre `.claude/`, son propre exemple de configuration). Prenez ce dont vous avez besoin, ignorez le reste.

## Composants

| Dossier | Ce que c'est |
|---|---|
| [**`om-prompt-reforge/`**](./om-prompt-reforge) | Deux hooks `UserPromptSubmit` pour Claude Code : un *gate* **0-LLM** qui déclenche le skill `forge` sur les prompts vagues, + un enrichisseur **Haiku** qui reformule votre intention en une phrase (rose 🕉️) — **sans clé API** et **sans jamais bloquer**. |

*(D'autres briques viendront s'ajouter, une par dossier.)*

## Licence

[MIT](./LICENSE) — © 2026 OMKomUnity.
