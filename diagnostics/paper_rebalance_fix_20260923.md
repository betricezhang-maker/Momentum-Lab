# Paper rebalance feedback investigation

Source: `ui/live-portfolio.js`; server entry point: `MomentumLabV2.py`.

The first displayed PAPER portfolio (`48ad03f1c8af445783f203fb2977314d`)
already has a COMPLETED paper rebalance in the production journal:
record `ba621872ffbf435d84978e89abfb8121`, signal 2026-09-18,
applied 2026-09-22, recorded 2026-09-22T12:35:31.960941+00:00.
The loaded UI shows its next signal 2026-10-12, execution 2026-10-13,
and no generated target. No production rebalance was submitted during testing.

The second identically named PAPER portfolio (`3e61e33901ed4f02beb4f5895213316f`)
shows WAITING_FOR_EXECUTION for signal 2026-09-18, execution 2026-09-21.

Demonstrated UI defects:
- An enabled Apply awaited a redundant expensive preview before showing activity.
- Success was immediately overwritten by load/render messages.
- A calculated disabled-action explanation was never rendered.
- No in-flight duplicate-click guard existed.
- Yesterday's shared state change also disabled valid partially executed manual workflows.

Fix: immediate persistent inline progress, elapsed wait time, duplicate-click guard,
direct submission to the server's existing validating Apply endpoint, separate
save versus refresh error handling, rendered availability and saved-journal notice,
and restored manual partial-execution controls. No calculation or backend changes.

Verification:
- `node tests/test_paper_apply_ui.cjs`: passed success, duplicate clicks, capital
  payload, server failure, refresh failure, and no redundant preview request.
- `python -m unittest discover -s tests -p test_live.py`: 23 passed.
- Browser at localhost:8766 used `tests/paper_apply_browser_fixture.py`, the real
  UI script and real LiveService with a temporary synthetic SQLite portfolio.
  Click showed immediate activity, persisted journal
  `47677b0cc24a4541b33de8e8de0677be`, then displayed completion and refreshed.
  Reload retained Recorded Rebalances = 1.
- Production browser inspection at localhost:8765 established the two states above.
  Initial data loading was slow; this patch removes one repeated calculation on
  Apply but does not claim to resolve all portfolio/data loading costs.

At inspection start port 8765 refused connections. The source server was started
with the available Python runtime and existing temporary dependencies. The normal
Tk launcher could not run with that runtime because its Tcl files are absent;
the application source was run directly for verification.

Reload the browser to obtain the changed JavaScript; no backend restart is needed
when serving this source directory.
