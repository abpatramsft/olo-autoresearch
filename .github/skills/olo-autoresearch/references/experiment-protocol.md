# Olo experimenter protocol

1. From the main repository root, read:

   ```text
   python olo.py scratchpad --parent <parent> --query "<hypothesis or failure cluster>"
   python olo.py show <parent>
   python olo.py traces <parent> <task-id>
   ```

2. Form a concrete hypothesis naming the file/function, exact behavior change,
   and predicted task effect.
3. Allocate:

   ```text
   python olo.py new --parent <parent> --hypothesis "<specific hypothesis>"
   ```

   Add `--proposal <idea_id>` when testing a queued proposal. To combine tested
   approaches, use `recombine --base <id> --donor <id> --contribution
   "<donor>::<specific transferred idea>" --hypothesis "<interaction prediction>"`.
   Repeat donor/contribution flags for multiple donors. Optional `--take
   "<donor>:relative/path"` transfers a complete file, not a semantic merge.
   Compare the result against every source, including incompatible interactions.

4. Parse the JSON. Every read/edit of candidate source must use the returned
   absolute `worktree`. Never edit the main target.
5. Do not modify protected benchmark, gate, fixture, or held-out paths.
6. Run the `olo-verifier` agent in pre phase. Also run:

   ```text
   python olo.py verify <exp_id> --phase pre
   ```

   Address every blocking finding before spending benchmark time.
7. Record exploratory measurements with `python olo.py probe <exp_id>`. Never
   run an unrecorded benchmark as a hidden trial. Probes preserve logs, traces,
   diffs, and outcomes without consuming an experiment attempt or promoting a
   result; they still consume the round's evaluation budget. Then measure the
   final candidate:

   ```text
   python olo.py run <exp_id>
   ```

8. Read the recorded outcome with `python olo.py show <exp_id>`.
9. Expect `pending-review`, not automatic promotion. Invoke
   `olo-benchmark-reviewer` on task changes, partial successes, and failures;
   then invoke independent `olo-verifier` post review. The verifier records
   `review <id> --verdict approve|reject --reviewer <name> --reason "<evidence>"`.
   Never approve your own candidate. A later-discovered validity problem uses
   `invalidate`, which excludes descendants and donor-derived dependents.
10. An approved meaningful gain is `committed`; a valid small gain, tie, or
    trade-off is `retained` and may remain a specialist parent. Discard only
    after recording the reason. Measured snapshots are immutable: create a
    child or sibling for another source change. Retry only a repaired failed
    execution. Setup blocks do not consume evaluation attempts.
    Record lessons with `learn <id> --text "<fact or hypothesis>" --kind
    observation|hypothesis --tag <topic>`; correct earlier lessons with
    `--supersedes <note_id>`. Do not infer causality or full recall from prose
    labels such as passed. Read the actual task traces and computed facts.
11. Return one JSON object with `experiment_id`, `status`, `score`, `parent`,
    `verification`, and `learnings`.
