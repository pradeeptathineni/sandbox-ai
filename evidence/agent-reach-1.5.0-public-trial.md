# Agent-Reach 1.5.0 public retrieval trial

Record ID: agent-reach-1.5.0-public-trial. Kind: real bounded trial.
Date: 2026-10-15. Visibility: reviewed-public summary; original responses and
machine-specific execution receipts stay at their trial owner.

## Decision and permitted operations

The Signals owner tested whether Agent-Reach adds a useful acquisition route
beyond existing public HTTPS, RSS and optional Jina extraction. The baseline was
Signals `3c6f2adc2b2709454beb284b0396ad6fb014da9b`. The candidate was official
[Agent-Reach source](https://github.com/Panniantong/Agent-Reach/tree/94f06c1969dfc1834001269d79d3ad0972d9dee6),
package 1.5.0, MIT. The retained source archive SHA-256 is
`d608cc619e460ca982dc11ab72897677837c8d73ea23851026c6374cff864f93`;
121 original regular files were individually hashed. Popularity and a passing
test do not establish dependency security or source suitability.

The user authorized inspection, a specific isolated installation and bounded
public web/RSS/YouTube/GitHub trials. The environment used Python 3.12.10, an
owned virtual environment and empty HOME/config/cache/GitHub directories. No
personal cookies, browser sessions, account login, API token, system install,
skill installation, persistent MCP registration, transcription or model was
used. The installer was invoked with `--dry-run` only. Its broader installation,
update, watch and authenticated channels were not tested.

The exact official source was built locally, not installed from the namesake
PyPI project. Core dependencies were wheel/hash pinned. RSS initially stopped
because sgmllib3k had no wheel. A separately inspected BSD source-only
sgmllib3k 1.0.0 build was then admitted as one precise exception; network and
child execution were refused by a Python audit hook during that build. This is
not an operating-system sandbox. Its source archive SHA-256 is
`7868fb1c8bfa764c1ac563d3cf369c381d1325d36124933a726f29fcdaa812e9`.
The resulting environment passed `uv pip check` for 31 installed packages.
Dependency licenses differ; the top-level MIT license is not a blanket license
or a complete security review of those packages.

## Frozen tests and results

The first protocol fixed three public URL pairs before outputs. Signals ran
first, then the candidate, serially, once per route; there were no retries or
alternate sources. Signals' existing transport had its normal 2,000,000-byte
limit and a 21-second host deadline; the candidate used upstream's 5 MiB/30-second
reader with a 35-second host deadline. Different publisher responses, remote
rendering, caches, output sizes and these limits prevent a causal speed or
same-byte fidelity claim.

| Fixed URL | Signals process wall | Agent-Reach web process wall | Observed scope |
| --- | --- | --- | --- |
| [LLMLingua-2 abstract](https://arxiv.org/abs/2403.12968) | 1.766 s | 1.176 s | Both retained the authors' compression/latency declarations; no benchmark replication |
| [AWS Free Tier FAQs](https://aws.amazon.com/free/free-tier-faqs/) | 1.938 s | 0.984 s | Both retained expiry/data-retention and automatic paid-plan context; no account action or zero-cost assurance |
| [GPI L99 kit](https://gwatneyperformance.com/l99-cam-kit-for-2010-2015-chevrolet-camaro-ss/) | 2.383 s | 5.806 s | Alternate rendering exposed adverse regulatory context absent from the current normalized projection |

The GPI candidate representation had 67,665 UTF-16 units, exceeding Signals'
unchanged 48,000-unit whole-source claim cap. A separately dated, single raw
capture of the same URL (1,433,988 bytes; SHA-256
`49eb4ae22f479d8bd46c3721750614becce6c4bd6657bd1d3924bf7deeb4bf58`)
confirmed the vendor's California/similar-regulation restriction inside a
`display:none` form subtree. Current Signals extraction intentionally excludes
that subtree and reports omitted content. Its 15,682-unit normalized text digest
was `01453e5b4faf2ab60504064d79b14626a2a63491bfaebeee7e9e340d03de9d63`,
matching the earlier Signals result. This demonstrates a representation gap,
not an unexpected normalizer defect, legal conclusion, personal vehicle fit or
authority to silently include hidden text. The hosted rendering's original
source identity remains unverified; more text is not automatically better.

An additive protocol froze the following follow-ups before their execution:

| Operation | Status | Direct observation and limit |
| --- | --- | --- |
| Same-byte Atom parsing | completed | Both parsers returned the same seven ordered titles/links and feed title; feedparser reported no parse warning. One 40,514-byte capture, SHA-256 `dfd36a3c9b9cdf562ebb27d3be29b3665677d39b9d0c884435e8afcb86660673`; no discovery-quality result |
| GitHub CLI search | blocked | Empty-profile `gh search repos` required authentication; no login or token fallback |
| YouTube flat search | completed | One public result for the exposed AWS-practice query; metadata only, not official-source quality or transcript readiness |
| First-result English subtitles | blocked | The same first result returned a sign-in/bot challenge; no cookies, replacement video, media download or transcription fallback |
| Focused upstream authored tests | completed | 127 passed across web, URL, configuration, home-isolation, credential-boundary and scrubbing tests; real network/child events refused by a Python audit hook |
| Authored cancellation controls | completed | Signals refused already-aborted input before DNS; an Agent-Reach mocked blocking read was terminated by its owner. These are local mechanics, not live/native cancellation performance |

All recorded owned process groups were absent after those operations. Model
usage and served model identity are not applicable to these model-free commands;
the downstream tokens required to reason over each representation, hidden
service costs and internal HTTP counts are unknown. No ordinary model research
arm or whole-answer quality comparison ran. Fixed URL retrieval is not a
discovery or human-usefulness benchmark.

## Review and disposition

Author checks cover pinned input identities, retained failures, exact same-byte
RSS fields, the hidden-subtree qualification, bounded process receipts and
dependency compatibility. A separate Context/Runner owner verified all 20 bound
review inputs and inspected the relevant public source and retained results.
That review accepted the documentation and qualified evidence claims. It did not
rerun the 127 tests or package check, certify dependencies, or accept production
execution or adoption.

Prefer direct upstream tools and Signals' existing owners, with explicit
per-channel prerequisites. Agent-Reach's CLI configures/checks tools; it has no
uniform source-reading API, and its MCP status server is not a research source
provider. Its web route wraps Jina, and its RSS readiness check detects a parser
import. An additional Signals adapter would duplicate existing mechanisms
without a demonstrated new supported source contract.

Keep Agent-Reach as inspected optional guidance in this trial; do not enable its
system installer or authenticated/browser backends by inference. Revisit a
specific upstream channel when an actual public research need remains unmet
under unchanged access, source, omission and evidence-budget rules. The hidden
regulatory qualifier is a concrete reason to inspect alternate evidence, not a
general quality winner. Runtime adoption, human value and broader lifecycle or
security guarantees remain unestablished.
