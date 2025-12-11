# How to commit and push (fast-forward only)

Use one linear commit for both branches to avoid drift and conflicts.

Workflow:
1. Work on `env/test` branch. Commit and push there.
2. Switch to `env/prod` and fast-forward from test: `git checkout env/prod && git pull --ff-only origin env/test`.
3. Push prod: `git push origin env/prod`.

Rules:
- No cherry-picks or merge commits between these branches.
- If `--ff-only` fails, fix the divergence first (re-run on test, then fast-forward).
- Keep history linear so Flux applies the same commit SHA on both clusters.
