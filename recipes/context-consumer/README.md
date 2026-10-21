# Context consumer recipe

Context is the first evaluated client. Context owns its claims, operating conditions, acceptance criteria and readable proof, and tests them without Sandbox. [contract.json](contract.json) pins the Context files this recipe evaluates by commit, path and SHA-256, with short excerpts of the claims it probes. It is an identity record, not a copy or a competing standard. Sandbox adds its own consumer and adversarial criteria in `evaluator/`.

| Path | Readable by | Contents |
| --- | --- | --- |
| `agent/receipt-review.json` | agent, after materialization | Integration control: the synthetic shop fixture and matched prompt from the saved Context native pair, reproduced byte-for-byte. |
| `agent/task-requirements.json` | agent, after materialization | Small implementation task: add `release` with tests, offline, no new dependencies, existing API preserved. |
| `evaluator/` | evaluator only | Expected answers as observable checks, with provenance. |
| `synthetic/outputs.json` | evaluator only | Authored adapter outputs, labeled synthetic. Never model results. |

Agents only see a disposable workspace and prompt built from `agent/`. Evaluator and synthetic files are checked to be outside that closure, by path and by content hash.

## Commands

Each command requires a caller-supplied Context checkout or artifact directory and a new output directory.

```sh
python3 -m harness.context_consumer prepare --context-source <context> --out <new-dir>
python3 -m harness.context_consumer demo --context-source <context> --out <new-dir> [--legacy-inputs <staged-dir>]
python3 -m harness.context_consumer import-legacy --inputs <staged-dir> --store <store>
```

`prepare` hashes the pinned Context files, builds baseline and equipped fixture repositories (equipped receives the pinned Context bytes in a commit before the task), checks the evaluator closure, freezes one plan per case and writes not-run native draft manifests plus `NATIVE-RECIPE.md`. It runs no model. A future authorized native host may run its own reviewed command in each workspace, then fill a draft from its saved receipts and seal it with `harness.trials seal --receipt-root`.

`demo` additionally exercises seal and compare with the synthetic adapter: one completed attempt per arm, an interrupted attempt that is retained, deterministic grading, and a discrimination check that grades an authored violating output. With `--legacy-inputs`, it imports the saved Context pair and checks that the integration control reproduces its reviewed diff and baseline commit.

`import-legacy` reads `original-artifacts.json`, verifies each staged receipt against its original SHA-256 and refuses missing or malformed fields. The pair is recorded as `historical-retrospective`. Package, lockfile, runtime, toolchain, requested model and elapsed time remain unknown with reasons. It is not promoted to a qualified comparable trial.

## Limits

Keyword and static checks are observable proxies, not judgments of review or code quality. The equipped arm receives pinned resource bytes, not a full Context lifecycle delivery with routers. Equipment fit (task relevance, conflicts, authority, freshness) is computed from declared metadata, not from model behavior. Recommendations grant no adoption authority.
