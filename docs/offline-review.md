# Review frozen evidence offline

The offline sheet lets a reviewer read the existing A/B projection, choose each
rating and write a rationale without hand-authoring the full review JSON. It
uses the [frozen calibration owner](grounding-calibration.md), its rubric and
its exact anchor-set digest. No additional review engine or store is involved.

First freeze an anchor set through the existing `freeze-anchors` operation.
Keep its store and original evidence with their authorized owner. Then create
this small request, using the exact returned digest:

```json
{"schema":"sandbox.grounding-calibration-request.v1","operation":"blind-session","input":{"anchorSetSha256":"EXACT_SHA256"}}
```

Save that request privately as `blind-session-request.json`. Generate the sheet
from the Sandbox checkout into a new private directory:

```sh
mkdir -m 700 /PRIVATE_PARENT/review
python3 -m harness.grounding_calibration --store /PRIVATE_STORE --format html \
  < blind-session-request.json > /PRIVATE_PARENT/review/sheet.html
```

Open the local file in a browser. It contains no remote scripts, fonts, requests
or automatic submission. Reference ratings, their rationales and author
provenance are withheld. Strategy names use the existing owner's A/B masking.
Source text can still identify its subject or origin, so this is not a guarantee
of participant blinding. Keep the sheet private because it includes the selected
answers and excerpts.

Open long retained excerpts to read their full text. They are collapsed without
truncation. Enter a reviewer label, choose a rating for every row and give each a rationale.
Choose `uncertain` or `not-assessed` where appropriate. There is no preselected
judgment. Download the review request before closing the page. Ratings live only
in the open page, with no browser storage or automatic recovery. Use the
downloaded file's actual location in the explicit import command:

```sh
python3 -m harness.grounding_calibration --store /PRIVATE_STORE \
  < /PRIVATE_DOWNLOAD/review-request.json
```

The native owner checks the exact frozen session and row coverage, then appends
one immutable review event. It refuses the anchor author's label and a reviewer
already recorded for this session. Retain the result. Use `read-history` with
the same digest to cold-reopen it, and the existing `correct-rating` operation
for a later correction. The original review remains intact.

`--format html` supports only `blind-session`. Every other operation refuses
before dispatch when that format is selected. Default JSON behavior is unchanged.
Generating or downloading a sheet stores no review. The request binds the
anchor digest and rows, but does not authenticate a person, prove the sheet was
unaltered or establish what the person read. Ratings remain caller judgments.
Descriptive agreement, source truth, accuracy, adoption and actual human
usefulness remain separate claims. A local HTML file is not an OS isolation
boundary.
