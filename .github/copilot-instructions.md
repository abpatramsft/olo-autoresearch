# Olo repository instructions

- `.olo/` is local experiment state and must not be committed.
- When Olo optimize mode is active, candidate edits must occur only in the
  experiment worktree returned by `python olo.py new`.
- Treat configured benchmark, gate, scorer, fixture, and held-out-data paths as
  immutable during experiments.
- Prefer the `/olo-autoresearch` skill and named `olo-*` custom agents for
  optimization work.
- Do not merge an experiment branch unless the user explicitly requests it.
