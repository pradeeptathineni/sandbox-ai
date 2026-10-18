# Saved-output word limits

A small opt-in Context checker rejected a three-word artifact at a two-word maximum, accepted a two-word repair, and rejected a four-word edit made after that pass. Each JSON receipt binds the result to the exact artifact digest. The checker left input bytes unchanged in all three calls.

The [plan](plan.json) fixed the three acceptance checks before execution. [Results](results.json) retain the inputs, exit statuses, counts, hashes and wall times. Counting covers the complete UTF-8 artifact using Python Unicode whitespace splitting. This is a bounded deterministic replay on authored synthetic text, with no model or network call and no added dependency.

Use this only when the task has an explicit word limit. It detects that mechanical constraint; source accuracy, readability, useful structure and fresh test claims need separate review. A changed artifact requires a new check. The manual repair here does not establish automatic repair, improved generation, global enforcement or a general context advantage. Review was by the coordinator; independent acceptance and human usefulness are not claimed.

Source: [Context AI at the tested commit](https://github.com/pradeeptathineni/context-ai/tree/750137272ee1f2a9702f054046805f8e91b061b6). Exact tested source hashes are in the plan. Earlier local rehearsals remain private and are not pooled with this final-source replay.
