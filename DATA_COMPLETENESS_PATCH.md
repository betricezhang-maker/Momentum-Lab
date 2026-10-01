# Calendar completeness patch

The prior validator compared observed raw/adjusted keys, which could agree while
both omitted a required ticker-day. Incremental downloads planned only sessions
after the global latest date. MOM rolling windows counted ticker rows, so a missing
session could silently lengthen a window. Live target lookup could select an older
valid ranking when the requested session had none.

This patch independently checks expected calendar coverage and gates MOM windows.
It does not modify formulas, ranking ties, costs, accounting, execution conventions,
the QFQ rebuild, or the incremental download plan. Staged completeness failure blocks
publication; historical gaps require an explicit separate repair decision.

## Changed files for this patch

- `momentumlab/completeness.py`: expected coverage, evidence and shared signal gate.
- `momentumlab/data_integrity.py`: separate statuses, evidence identity/staging and manifest.
- `MomentumLabV2.py`: calendar propagation, optional evidence paths, exclusion exports.
- `momentumlab/live_model.py`: exact open-session target lookup.
- `momentumlab/research_audit.py`: exact session inputs and exclusion evidence.
- `ui/integrity.js`: scope, examples, counts and full JSON export.
- `tests/test_completeness.py`, `tests/test_completeness_ui.cjs`: focused regressions.
- Existing regression fixtures in `test_regressions.py`, `test_v4_research.py`,
  `test_historical_preview.py`, `test_research_audit.py`, `test_http_v4.py`,
  `test_performance.py`, `test_data_integrity.py`: explicit calendars and new expected
  gap behavior; original arithmetic remains guarded.
- `MOMENTUM_LAB_MANUAL.md`, `DATA_INTEGRITY.md`, `RESEARCH_AUDIT.md`: updated rules.

Other pre-existing working-tree changes are outside this patch.

## Verification

Python unittest discovery: 95 tests run, 94 passed, one opt-in real-data test skipped.
This includes 12 focused completeness tests, complete-fixture arithmetic/rank/backtest
equivalence, and the real local HTTP Research/Grid/Live/export workflow on synthetic data.
All five Node UI test scripts passed, including full-report export and evidence escaping.
`git diff --check` passed. Temporary dependency repairs were confined to the test
environment; no application dependency requirement or financial dataset was changed.

## How to check existing ETF data

1. Restart the source application using your existing launcher.
2. Select the ETF universe in Data Manager and click Run Integrity Check.
3. Read Structural integrity and Data completeness separately. Inspect FAIL findings
   first, then warnings and the reported scope.
4. Export full completeness evidence (JSON). Each range identifies ticker, dates,
   component, classification, severity, source and MOM impact.
5. Do not infer that ALREADY CURRENT means history is complete. A refresh still
   validates staged history and cannot publish unresolved required gaps.

Validation never downloads or repairs financial files. It retains the existing
integrity audit-log write. Tests use temporary synthetic datasets; the opt-in real
ETF integration control is deliberately skipped.

## Issue resolution workflow

Data Manager now shows stable `CMP-...` issue IDs, affected dates/components,
severity, blocking state and resolution status. **Revalidate** recalculates against
current files and clears stale cached status. For ETF issues, **Repair Missing Data**
fetches only the listed ticker/session ranges through the existing date-batch APIs,
rebuilds adjusted prices in staging, validates, and publishes only after success.
Other universes report that targeted provider repair is not supported rather than
performing a broad historical rebuild. **Acknowledge with reason** requires a non-empty
written explanation; **Reopen** restores the block. Confirmations are recorded in
`completeness_acknowledgments.json` and do not alter prices, scores, or exclusions.
Structural failures and incomplete MOM windows cannot be acknowledged away.

CONFIRMED means manually reviewed and accepted, not repaired or independently
verified suspension. Original classifications remain visible. A confirmed raw-price
gap also confirms adjusted-price gaps only on the same ticker/open dates where raw
prices are absent. Inherited records retain the parent confirmation ID, explanation,
timestamp and reference; factors and independent calculation failures are not inherited.
Partial overlaps split into confirmed and unconfirmed ranges. Session evidence
signatures survive unrelated new dates, but changed evidence requires review.
Reopen revokes the parent and inherited confirmations. When actual rows return,
history displays RESOLVED without deleting the original review record.

The overall research gate is WARNING when all blocking completeness findings are
confirmed; the underlying completeness assessment may still be FAIL. Structural
failures remain blocking. Reports, exports and saved research/Grid metadata contain
the same effective confirmation records, including history. Later edits do not
rewrite prior run metadata. Revalidation also notices changes to the confirmation
file; confirmations accompany staged validation without overwriting production history.

## Assumptions and limits

Default scope ends at the latest raw dataset date, with 120 warm-up sessions before
dated eligibility intervals. Freshness is reported separately. The local exchange
calendar is authoritative, not externally verified. Optional lifecycle and sourced
full-session suspension files are documented in the manual. Absent/invalid evidence
warns; missing rows never prove suspension. A confirmed suspension explains a gap
but does not make an incomplete MOM window eligible. Per-component missing-session
counts can count one ticker-day three times; evidence findings are counted separately
by classification. No trusted production market dataset was modified during testing.
