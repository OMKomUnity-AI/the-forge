# om-ci

**The GitHub CI of every repo you have open, live, beside the transcript.**

`gh run watch` follows one run in one terminal, and stops when it ends. But the question is never
"is this run advancing" — it is **what is red, on which repo, and at which step**. This mod answers
that in the session where you are already working, and it finds the repos by itself.

## What it shows

- counters: in progress, red, green, and how many repos are being watched
- in-progress runs first, with elapsed time and the step each job is on
- failed runs with the repo, branch, event, actor, commit title — and **the step that broke**
  (`Frontend — typecheck, tests, build, e2e · Tests unitaires (étape 7)`)
- the last failed-job log lines, in the pane, when you open a run
- repos it could not reach, **named**, with the reason

A dashboard that hides an unreachable repo makes you believe in green. So it never hides one.

## What you can do from it

Everything with buttons alone — the mobile app draws no `Input` and no `Select` (type `Elements`,
`claude-code`), so a CI pane that needed a keyboard would be useless from the phone.

- **open a run** — its failed-job log, truncated to the last lines that matter
- **re-run it** — failed jobs only, or the whole run. **Two presses**: a re-run spends CI minutes
  and can redeploy, so the first press only asks.
- **open it on GitHub** — for the cases only the web page answers (matrix, artefacts, one job)

## How it works

`ci.py` (no LLM, stdlib only) does the reading, and `gh` carries the authentication — no token is
ever read by hand.

1. **Repos, with no configuration to maintain.** The `cwd` of every open Claude session
   (`~/.claude/sessions/*.json`), plus `--cwd` and `--repo`, resolved through
   `git remote get-url origin` to `owner/repo`.
2. **One runs call per repo** (`--max-runs`, default 20).
3. **Jobs only where they teach something** — a run still going, or a `failure` / `timed_out` /
   `cancelled`. A green run costs zero job calls, which is what keeps the API quota safe
   (`--max-jobs`, default 6).
4. **The step, not the verdict.** For each job, the first step whose conclusion is `failure`, with
   its number — or the step currently running.

**CI data is untrusted.** A commit title, a branch name, a step name and a log line all come
from whoever can push a branch or open a pull request from a fork. Rendered as they are in a
terminal, an escape sequence repaints the screen and can hide what you think you are reading. So
every string `ci.py` emits goes through one sanitiser before it leaves, and the failed-job log —
which `gh` hands straight to the pane — is cleaned there. Tabs and newlines stay: they belong in
a log.

One repo failing never costs you the others: every `gh` call goes through one function that cannot
raise, and a repo that breaks lands in `erreurs` while the rest is returned.

## Install

```
claude plugin install om-ci@om-forge
```

Then `/ci` opens the pane. A status indicator shows `CI 2 rouge · 1 en cours · /ci` even when the
pane is closed — a failure that lands while you code should be visible without opening anything.

## Cost

No model call, ever. One `gh` call per repo every 30 s, plus one per run that is running or red:
about 240 calls an hour for two repos, against a 5000/hour quota.

## Tests

```
python3 ci.py --autotest          # extraction, with fixtures, no network
claude plugin test om-ci          # the pane, on terminal and on mobile
```
