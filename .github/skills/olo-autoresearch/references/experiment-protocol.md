# Olo experimenter protocol

1. From the main repository root, read:

   ```text
   python olo.py scratchpad
   python olo.py show <parent>
   python olo.py traces <parent> <task-id>
   ```

2. Form a concrete hypothesis naming the file/function, exact behavior change,
   and predicted task effect.
3. Allocate:

   ```text
   python olo.py new --parent <parent> --hypothesis "<specific hypothesis>"
   ```

4. Parse the JSON. Every read/edit of candidate source must use the returned
   absolute `worktree`. Never edit the main target.
5. Do not modify protected benchmark, gate, fixture, or held-out paths.
6. Run the `olo-verifier` agent in pre phase. Also run:

   ```text
   python olo.py verify <exp_id> --phase pre
   ```

   Address every blocking finding before spending benchmark time.
7. Run:

   ```text
   python olo.py run <exp_id>
   ```

8. Read the recorded outcome with `python olo.py show <exp_id>`.
9. Run `olo-verifier` in post phase. If committed, inspect failing task traces
   and invoke `olo-benchmark-reviewer` in review-experiment mode.
10. For a clean non-improver, discard with `failure-class=hypothesis`. Retry the
    same node only for a concrete implementation or transient execution bug.
11. Return one JSON object with `experiment_id`, `status`, `score`, `parent`,
    `verification`, and `learnings`.
