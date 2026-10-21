# Repository guidance

Read [.meta/context/workflow.md](.meta/context/workflow.md) for purpose, file ownership and the current workflow. Read [evidence meanings](docs/evidence.md) when changing a trial, metric, review or decision. Read [Context reuse](docs/context-reuse.md) before proposing execution or an interchange contract.

Use the existing templates and native tools. Keep private evidence at its source, preserving exact references and hashes in a private index. A source link is a reading route, not a claim that it was read or invoked.

For harness or recipe changes, run `python3 -m unittest discover -s tests`; it needs only Python 3.12+ and Git. For document changes, check local links, JSON syntax, fixture/index digests and `git diff --check`. When Context is available, check the bundle fixture against its existing schema and intake. Report actual checks and unavailable ones. Do not add dependencies, agents, hooks, models or runtime services from a recommendation.
