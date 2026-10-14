# Watchexec polling for a bounded edit-test loop

Record ID: watchexec-polling-2026-10-14. Record kind: real tool execution with synthetic inputs. Date: 2026-10-14. Visibility: reviewed-public.

## Decision and scope

Question: Can an opt-in foreground watcher give useful fast-check feedback after saved source edits, preserve failures and avoid unwanted reruns?

The inputs were authored Python and JavaScript assertions in disposable directories. All commands were foreground and bounded. Logs were outside the watched root. There was no private project input, session access, model call, global install, daemon or project wiring. Execution permission was removed from trial copies after cleanup. The authorized decision owner has not adopted this into a production project.

The acceptance criteria required the final saved digests, assertion failure and recovery to be observed within 2.5 seconds, no checker work for ignored/outside changes or idle periods, and owned-process cleanup. An initial startup check was expected, separately from edit-triggered work. The existing fast-check commands were retained. These commands are not an operating-system security boundary.

## Synthetic outcomes

| Attempt | Observation | Disposition |
| --- | --- | --- |
| Earlier native workflow | Only the initial check ran; valid edits, burst final digest, assertion failure and recovery were missed. | Historical failure retained. Native registration/backend cause remains unknown. |
| Explicit polling, 90 seconds | All intended changed digests and failure/recovery were detected. One redundant check occurred during a combined ignored/outside-write phase. | Functional feedback demonstrated; full acceptance failed. |
| Isolation baseline, 45 seconds | Ignored-file creation and root-directory timestamp modification each emitted a directory-only `Modify(Metadata(WriteTime))` event. Ignored-file modification and separate outside-root modification/creation caused no checks. Startup was one empty-event dispatch. | Redundant trigger isolated. |
| Jaq filter, 60 seconds | Eight intended checks ran and unwanted checks were suppressed. The process did not exit on stdin close or the bounded TERM wait. | Not recommended; owned cleanup required KILL. |
| Short cleanup diagnostic | EOF and main-loop completion were logged, but the process still did not exit within 12 seconds. | Rules out the original three-second harness limit as a sufficient explanation. General cause unproved. |
| Command guard, 45 seconds | Eight intended checks ran; two directory-only dispatches skipped the checker. Normal/atomic save, watched-file creation/removal, rapid final digest, assertion failure and recovery passed. | Scoped mechanical acceptance passed; adoption remains advisory. |

The guard arm observed no additional checks during a 20.308-second idle interval. Stdin close exited within three seconds with status 0; recorded owned PIDs and process group were absent. The native failure and all intervening failures remain part of the result.

## Measured resource costs

| Metric | Observed value | Denominator and limits |
| --- | --- | --- |
| Saved-change to checker completion | 440.967–715.261 ms | Seven intended save/change phases in the 45-second guard arm; initial startup excluded. |
| Reaped-child CPU | 2.027206 seconds | Whole guard arm, including eight synthetic checkers and ten event-wrapper dispatches. |
| Maximum child RSS | 34.832 MiB | macOS child-resource receipt; includes checker/wrapper processes, not isolated watcher RSS. |
| Isolated idle CPU, human effort and token savings | Unknown | Not measured. |

The arm counts and workloads differ. These measurements are not a matched throughput benchmark, economic saving or long-term reliability result. Lower CPU while checks are missing is not efficiency. Historical native and current polling outcomes do not establish a general product defect or an operating-system causal claim.

## Source provenance and method

The tested executable is the official x86_64 Apple Darwin artifact for [Watchexec 2.8.0](https://github.com/watchexec/watchexec/releases/tag/v2.8.0), source commit `07e2b99e833da655571fc4001ae47b03f44742c7`. Its SHA-256 is `ee3e1fc92044e9808c39556c65be59ce25d134fa3c24f55a0452c121d2a35cca`. It was unsigned; publisher/archive checks and the original artifact identity were retained privately. A matching digest is not a complete security audit.

Watchexec is [Apache-2.0 licensed](https://github.com/watchexec/watchexec/blob/07e2b99e833da655571fc4001ae47b03f44742c7/LICENSE). Its pinned [manual](https://github.com/watchexec/watchexec/blob/07e2b99e833da655571fc4001ae47b03f44742c7/doc/watchexec.1.md) documents polling and JSON event emission. This report copies no upstream implementation code and remains under this repository's [MIT license](../../LICENSE).

The working configuration used explicit 500 ms polling, 250 ms debounce, a small watched source directory, source-extension filtering, queued busy updates, no shell, no symlink following, and JSON events on the command's stdin. The command guard skipped the expensive checker only when path tags existed and every path was a known directory. It ran the initial empty dispatch and file/mixed/unknown-type events. The jaq route is excluded from the recommendation because of the observed exit failure.

## Recommendation, review and limitations

Provisional recommendation: opt-in foreground fast edit-test loops with explicit source scope and the directory-only command guard. Directory-only events still start a small wrapper; they do not run the expensive checker. This is not a daemon or automatic full-suite recommendation.

The author checked saved digests, trigger events, failure/recovery, recorded-process cleanup, source preservation and the conservative guard behavior. Independent review: not performed. Human usefulness and production adoption: not measured/not adopted.

Moving a populated directory may provide only directory tags; perform a manual full check after structural changes. Configuration and dependency files require deliberate inclusion. Same-byte saves, queued slow-job behavior, active-child interruption, broader language projects and long-running reliability were not tested. Polling does not repair or attribute the historical native-event failure. No session-visibility claim follows.

The structured approved report is [results.json](results.json). The [evidence index](../index.json) pins the exact public bytes. Original raw receipts, private machine locations and internal approval records remain with their private owner and are not published.
