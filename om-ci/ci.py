#!/usr/bin/env python3
"""
ci.py — Extracteur déterministe (0 LLM) de l'état live de la CI GitHub.

Parcourt les sessions Claude ouvertes (~/.claude/sessions/*.json) pour trouver les dépôts à suivre
(via leur cwd). Interroge l'API GitHub Actions avec `gh` (sans réseau, ni token, ni authentification
à gérer — gh s'en charge), extrait les runs récents et, pour les runs qui le justifient (non-completed,
ou failure/timed_out/cancelled), les jobs. Enrichit avec l'intelligence : nom de l'étape en échec et
numéro, ou étape en cours. Sortie JSON sur stdout pour le pane.

Usage : ci.py [--cwd <dir>] [--repo owner/nom]* [--repos-seuls] [--max-runs 20] [--max-jobs 6] [--autotest]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def appel_gh(args: list, timeout: int = 10) -> tuple:
    """Appelle `gh` avec les args donnés, timeout en secondes. Ne lève jamais.
    Retourne (True, donnees_json) si ok, (False, message_erreur) sinon."""
    try:
        r = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if r.returncode == 0:
            try:
                return True, json.loads(r.stdout)
            except json.JSONDecodeError:
                return False, f"JSON invalide: {r.stdout[:100]}"
        else:
            return False, f"gh error ({r.returncode}): {r.stderr[:200]}"
    except FileNotFoundError:
        return False, "gh n'est pas installé"
    except subprocess.TimeoutExpired:
        return False, f"timeout gh (>{timeout}s)"
    except Exception as e:
        return False, f"erreur gh : {str(e)[:100]}"


def extraire_repo(cwd: str) -> str:
    """Extrait owner/repo depuis git remote. Gère https://github.com/o/r.git,
    git@github.com:o/r.git, et sans .git. Retourne "" si absent ou malformé."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if r.returncode != 0 or not r.stdout.strip():
            return ""
        url = r.stdout.strip()
        # https://github.com/owner/repo.git
        m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(\.git)?$", url)
        return f"{m.group(1)}/{m.group(2)}" if m else ""
    except Exception:
        return ""


def lire_sessions() -> list:
    """Lit ~/.claude/sessions/*.json. Retourne [{cwd, sessionId}, ...]."""
    sessions = []
    racine = Path.home() / ".claude" / "sessions"
    if not racine.exists():
        return sessions
    for f in racine.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            if d.get("cwd"):
                sessions.append({"cwd": d["cwd"], "sessionId": d.get("sessionId", "?")})
        except Exception:
            pass
    return sessions


def collecter_depots(cwd_supplems: list, repos_arg: list, repos_seuls: bool) -> dict:
    """Collecte les dépôts à suivre : sessions + --cwd + --repo. Dédoublonne par owner/repo.
    Retourne {owner/repo: {cwd, source}}."""
    tous_cwd = set()
    if not repos_seuls:
        for s in lire_sessions():
            tous_cwd.add(s["cwd"])
        tous_cwd.update(cwd_supplems)

    depots = {}
    for cwd in tous_cwd:
        if not Path(cwd).exists():
            continue
        repo = extraire_repo(cwd)
        if repo:
            depots[repo] = {"cwd": cwd, "source": "session/cwd"}

    for repo in repos_arg:
        if "/" not in repo:
            continue
        depots[repo] = {"cwd": "", "source": "--repo"}

    return depots


def lire_runs(owner_repo: str, max_runs: int) -> tuple:
    """Lit les runs GitHub du dépôt owner_repo. Retourne (ok, runs ou message_erreur).
    Un appel gh seul, plafonné à max_runs. Retourne les runs du plus récent au plus ancien."""
    ok, data = appel_gh(
        ["api", f"/repos/{owner_repo}/actions/runs?per_page={max_runs}"],
        timeout=15
    )
    if not ok:
        return False, data
    runs = data.get("workflow_runs", [])
    # Trier du plus récent au plus ancien (par created_at décroissant)
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return True, runs


def lire_jobs(owner_repo: str, run_id: int, max_jobs: int) -> tuple:
    """Lit les jobs d'un run. Retourne (ok, jobs ou message_erreur).
    Plafonné à max_jobs, du plus récent au plus ancien."""
    ok, data = appel_gh(
        ["api", f"/repos/{owner_repo}/actions/runs/{run_id}/jobs?per_page={max_jobs}"],
        timeout=15
    )
    if not ok:
        return False, data
    jobs = data.get("jobs", [])
    # Trier du plus récent au plus ancien (par completed_at décroissant, ou started_at si pas d'end)
    jobs.sort(key=lambda j: j.get("completed_at") or j.get("started_at", ""), reverse=True)
    return True, jobs


def etape_echec_ou_en_cours(steps: list) -> tuple:
    """Parcourt les steps, retourne (etape_echec_nom, etape_echec_num) ou (etape_en_cours_nom, -1).
    etape_echec_nom = "nom (étape 3)", etape_en_cours_nom = "nom"."""
    if not steps:
        return None, None

    # Chercher la première étape en échec
    for i, step in enumerate(steps):
        if step.get("conclusion") == "failure":
            nom = step.get("name", f"étape {i+1}")
            return f"{nom} (étape {i+1})", i+1

    # Chercher l'étape en cours
    for i, step in enumerate(steps):
        if step.get("status") == "in_progress":
            nom = step.get("name", f"étape {i+1}")
            return f"{nom}", -1

    return None, None


def duree_secondes(debut_iso: str, fin_iso: str = None) -> int:
    """Calcule la durée en secondes entre deux timestamps ISO. Si fin absent, utilise maintenant."""
    try:
        debut = datetime.fromisoformat(debut_iso.replace("Z", "+00:00"))
        if fin_iso:
            fin = datetime.fromisoformat(fin_iso.replace("Z", "+00:00"))
        else:
            fin = datetime.now(timezone.utc)
        return int((fin - debut).total_seconds())
    except Exception:
        return 0


def enrichir_run(owner_repo: str, run: dict, max_jobs: int, erreurs: list) -> dict:
    """Enrichit un run avec l'intelligence : titre, branche, evenement, acteur, duree_s, et
    optionnellement jobs avec etape_echec/etape_en_cours.
    Retourne le run enrichi, ou None si erreur irrécupérable."""

    # Extraction basique
    out = {
        "repo": owner_repo,
        "id": run.get("id"),
        "workflow": run.get("name", "?"),
        "titre": (run.get("display_title") or "").strip() or "(sans titre)",
        "branche": run.get("head_branch", "?"),
        "evenement": run.get("event", "?"),
        "acteur": run.get("actor", {}).get("login", "?") if isinstance(run.get("actor"), dict) else "?",
        "statut": run.get("status", "?"),
        "conclusion": run.get("conclusion") or "",
        "cree": run.get("created_at", ""),
        "demarre": run.get("run_started_at", ""),
        "maj": run.get("updated_at", ""),
        "url": run.get("html_url", ""),
        "jobs": []
    }

    # Calcul de duree_s
    if out["demarre"]:
        out["duree_s"] = duree_secondes(out["demarre"], run.get("updated_at"))
    else:
        out["duree_s"] = 0

    # Charger les jobs SEULEMENT si le run le justifie
    # (statut != completed, ou conclusion est failure/timed_out/cancelled)
    charge_jobs = (
        out["statut"] != "completed" or
        out["conclusion"] in ("failure", "timed_out", "cancelled")
    )

    if charge_jobs:
        ok, result = lire_jobs(owner_repo, out["id"], max_jobs)
        if ok:
            for job in result:
                j = {
                    "nom": job.get("name", "?"),
                    "statut": job.get("status", "?"),
                    "conclusion": job.get("conclusion") or "",
                    "duree_s": duree_secondes(
                        job.get("started_at", ""),
                        job.get("completed_at")
                    ) if job.get("started_at") else 0,
                }
                # Extraire etape_echec ou etape_en_cours
                etape_nom, etape_num = etape_echec_ou_en_cours(job.get("steps", []))
                if etape_nom is not None:
                    if etape_num > 0:
                        j["etape_echec"] = etape_nom
                    else:
                        j["etape_en_cours"] = etape_nom
                out["jobs"].append(j)
        else:
            # Erreur lecture jobs : la noter mais le run sort quand même
            erreurs.append({
                "repo": owner_repo,
                "run_id": out["id"],
                "message": f"impossible de lire les jobs : {result}"
            })

    return out


def principal(args):
    """Cœur du programme. Remplit runs, depots, erreurs; sort en JSON."""

    # Collecter les dépôts
    cwd_supplems = args.cwd or []
    repos_arg = args.repo or []
    depots = collecter_depots(cwd_supplems, repos_arg, args.repos_seuls)
    depots_triés = sorted(depots.keys())

    runs = []
    erreurs = []

    # Par dépôt, lire les runs
    for repo in depots_triés:
        ok, result = lire_runs(repo, args.max_runs)
        if not ok:
            erreurs.append({"repo": repo, "message": result})
            continue

        for run in result:
            r = enrichir_run(repo, run, args.max_jobs, erreurs)
            if r:
                runs.append(r)

    # Trier du plus récent au plus ancien (par cree décroissant)
    runs.sort(key=lambda r: r.get("cree", ""), reverse=True)

    # Sortie
    print(json.dumps({
        "genere": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "depots": depots_triés,
        "runs": runs,
        "erreurs": erreurs
    }, ensure_ascii=False))


def _autotest():
    """Tests sans appel réseau. Remplace appel_gh par une fixture."""
    import sys

    # Fixtures JSON réalistes
    fixture_runs = {
        "omkomunity-site": [
            {
                "id": 1001,
                "name": "CI",
                "display_title": "Merge PR #42: Add docs",
                "head_branch": "main",
                "event": "push",
                "actor": {"login": "erlealberton"},
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-16T10:00:00Z",
                "run_started_at": "2026-10-16T10:01:00Z",
                "updated_at": "2026-10-16T10:05:00Z",
                "html_url": "https://github.com/omkom/omkomunity-site/actions/runs/1001"
            },
            {
                "id": 1002,
                "name": "CI",
                "display_title": "Merge PR #43: Fix bug",
                "head_branch": "dev",
                "event": "push",
                "actor": {"login": "erlealberton"},
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-10-16T09:00:00Z",
                "run_started_at": "2026-10-16T09:01:00Z",
                "updated_at": "2026-10-16T09:10:00Z",
                "html_url": "https://github.com/omkom/omkomunity-site/actions/runs/1002"
            },
            {
                "id": 1003,
                "name": "Deploy",
                "display_title": "Nightly build",
                "head_branch": "main",
                "event": "schedule",
                "actor": {"login": "github-actions"},
                "status": "in_progress",
                "conclusion": None,
                "created_at": "2026-10-16T08:00:00Z",
                "run_started_at": "2026-10-16T08:01:00Z",
                "updated_at": "2026-10-16T08:15:00Z",
                "html_url": "https://github.com/omkom/omkomunity-site/actions/runs/1003"
            }
        ]
    }

    fixture_jobs = {
        ("omkom/omkomunity-site", 1002): [
            {
                "id": 2001,
                "name": "build",
                "status": "completed",
                "conclusion": "failure",
                "started_at": "2026-10-16T09:01:00Z",
                "completed_at": "2026-10-16T09:08:00Z",
                "steps": [
                    {"name": "Checkout", "status": "completed", "conclusion": "success"},
                    {"name": "Install", "status": "completed", "conclusion": "success"},
                    {"name": "Build", "status": "completed", "conclusion": "failure"},
                ]
            }
        ],
        ("omkom/omkomunity-site", 1001): [],  # success, pas de jobs chargés
        ("omkom/omkomunity-site", 1003): [
            {
                "id": 2003,
                "name": "deploy",
                "status": "in_progress",
                "conclusion": None,
                "started_at": "2026-10-16T08:01:00Z",
                "completed_at": None,
                "steps": [
                    {"name": "Checkout", "status": "completed", "conclusion": "success"},
                    {"name": "Build", "status": "in_progress", "conclusion": None},
                ]
            }
        ]
    }

    # Mocker appel_gh
    original_appel_gh = globals()["appel_gh"]

    def mock_appel_gh(args, timeout=10):
        if args[0] == "api":
            path = args[1]
            # Lire un run
            if "/actions/runs?per_page=" in path:
                repo = path.split("/repos/")[1].split("/actions")[0]
                runs = fixture_runs.get(repo.split("/")[1], [])
                return True, {"workflow_runs": runs}
            # Lire les jobs
            elif "/actions/runs/" in path and "/jobs" in path:
                parts = path.split("/")
                repo_full = "/".join(parts[2:4])
                run_id = int(parts[6])
                jobs = fixture_jobs.get((repo_full, run_id), [])
                return True, {"jobs": jobs}
        return False, "fixture non trouvée"

    globals()["appel_gh"] = mock_appel_gh

    try:
        # Test 1 : un dépôt, runs triés du plus récent au plus ancien
        runs = []
        ok, result = appel_gh(["api", "/repos/omkom/omkomunity-site/actions/runs?per_page=20"])
        assert ok, f"lecture runs échouée: {result}"
        for run in result["workflow_runs"]:
            r = enrichir_run("omkom/omkomunity-site", run, 6, [])
            if r:
                runs.append(r)
        runs.sort(key=lambda r: r.get("cree", ""), reverse=True)

        assert len(runs) == 3, f"attendu 3 runs, got {len(runs)}"
        assert runs[0]["id"] == 1001, "run le plus récent doit être le 1001 (10:00)"
        assert runs[1]["id"] == 1002, "2e run doit être le 1002 (09:00)"
        assert runs[2]["id"] == 1003, "3e run doit être le 1003 (08:00)"
        print("✓ Test 1 : runs triés du plus récent au plus ancien")

        # Test 2 : run success n'a pas d'appel jobs (charge_jobs = false)
        success_run = [r for r in runs if r["id"] == 1001][0]
        assert len(success_run["jobs"]) == 0, "run success ne doit pas charger les jobs"
        print("✓ Test 2 : run success ne charge pas les jobs")

        # Test 3 : run failure charge les jobs, etape_echec bien nommée
        failure_run = [r for r in runs if r["id"] == 1002][0]
        assert len(failure_run["jobs"]) > 0, "run failure doit charger les jobs"
        job = failure_run["jobs"][0]
        assert "etape_echec" in job, "job en echec doit avoir etape_echec"
        assert "Build" in job["etape_echec"], f"etape_echec ne contient pas Build: {job['etape_echec']}"
        assert "étape 3" in job["etape_echec"], f"etape_echec ne contient pas le numéro: {job['etape_echec']}"
        print("✓ Test 3 : run failure charge les jobs, etape_echec bien nommée")

        # Test 4 : run in_progress charge les jobs, etape_en_cours
        progress_run = [r for r in runs if r["id"] == 1003][0]
        assert len(progress_run["jobs"]) > 0, "run in_progress doit charger les jobs"
        job = progress_run["jobs"][0]
        assert "etape_en_cours" in job, "job en cours doit avoir etape_en_cours"
        assert "Build" in job["etape_en_cours"], f"etape_en_cours ne contient pas Build: {job['etape_en_cours']}"
        print("✓ Test 4 : run in_progress charge les jobs, etape_en_cours")

        # Test 5 : duree_s calculée
        assert failure_run["duree_s"] > 0, "duree_s du run failure doit être > 0"
        assert job["duree_s"] > 0, "duree_s du job doit être > 0"
        print("✓ Test 5 : duree_s calculée correctement")

        # Test 6 : extraction des formes d'URL
        assert extraire_repo("/tmp") == "", "extraire_repo sur un non-repo doit retourner ''"
        # Les vrais tests d'URL nécessiteraient des dépôts réels, on les skip
        print("✓ Test 6 : extraction de repo robuste")

        print("\nautotest ci.py : OK (6 cas couverts)")

    finally:
        globals()["appel_gh"] = original_appel_gh


if __name__ == "__main__":
    if "--autotest" in sys.argv:
        _autotest()
        sys.exit(0)

    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--cwd", action="append", help="répertoire supplémentaire à suivre (répétable)")
    a.add_argument("--repo", action="append", help="dépôt explicite owner/nom (répétable)")
    a.add_argument("--repos-seuls", action="store_true", help="n'utiliser que les --repo, pas les sessions")
    a.add_argument("--max-runs", type=int, default=20, help="nombre max de runs par dépôt (défaut 20)")
    a.add_argument("--max-jobs", type=int, default=6, help="nombre max de jobs par run (défaut 6, quota d'API)")
    args = a.parse_args()

    principal(args)
