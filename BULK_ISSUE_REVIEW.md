# Bulk dataset-integrity review

Restart Momentum Lab with the usual portable launcher, refresh the browser, and open Data Manager. No Python commands are needed. Run the integrity check to show the Bulk issue review controls. Existing individual Repair, Acknowledge with reason, and Reopen actions remain available.

The Data Manager now shows one Integrity workspace directly below **Dataset to Manage**. Its operation panel acknowledges clicks immediately and displays the operation type, ID, stage, elapsed time, and measurable progress. The findings and upload previews each show 50 review groups or rows per page by default; choose 25, 100, or 250 when useful. Filters and sorting act before finding pagination. Export buttons explicitly choose the current page, all filtered results, or all findings.

If a workbook has written reasons but every **Requested action** remains `leave unchanged`, the preview reports zero valid actionable decisions and disables Apply. Change the action to `confirm`, `repair`, or `reopen` for each row you intend to act on. Confirmation does not download or fill prices.

## Review in Excel

1. Click **Export issues for review (Excel)**. This revalidates the current dataset and exports every finding, regardless of table filters or representative examples.
2. Open the downloaded workbook. The Review sheet groups matching raw-price and dependent adjusted-price gaps where safe. The Underlying findings sheet retains each finding's identity and component. Independent adjusted-price confirmations remain separate.
3. Edit only the three yellow columns: **Requested action**, **User reason**, and **Evidence reference or URL**. Actions are `leave unchanged`, `repair`, `confirm`, or `reopen`. Confirmation requires a written reason; Chinese and English are supported. A reference is optional. Leave the other sheets and protected finding fields unchanged. Rows can be reordered.
4. Save as `.xlsx`, then choose it using **Import reviewed issues**. Import only produces a preview; it does not confirm or repair anything.
5. Review the actions, inherited adjusted-price confirmations, unchanged entries, and rejected/stale rows. Click **Apply reviewed decisions** to apply valid decisions. Rejected rows are reported and are not applied. Findings are checked again at application time.
6. Read the final per-issue results. Revalidate when needed to see current findings and research permission.

Unknown identifiers, changed protected fields, formulas, conflicting duplicate decisions, and materially changed findings are rejected. An unrelated change to the overall dataset fingerprint does not invalidate an unchanged finding. Reimporting the same applied decisions does not create another confirmation audit event.

Export snapshots and import decision history are saved under the selected dataset's `integrity_reviews` directory. Keep that directory when moving the portable dataset: an exported workbook requires its original local snapshot to be imported. Workbook limits are 12 MB compressed and 100,000 review rows.

## Confirming a known gap

CONFIRMED means manually reviewed and accepted with the recorded explanation. It does not mean the data was repaired or a suspension independently verified. Only overridable completeness findings can be confirmed. Structural failures remain blocking.

A raw-price confirmation propagates only to verified dependent adjusted-price gaps on matching dates. The inherited record retains the original confirmation link and explanation. Independent adjusted-price confirmations are preserved. Use **Reopen** to revoke a confirmation and its inherited confirmations; the history remains auditable. Real repairs followed by revalidation resolve the findings without erasing past confirmation history.

Confirmed completeness exceptions permit research with a visible WARNING only if no other blocking findings remain. Existing signal-window exclusions and calculation rules are unchanged.

## Repair multiple findings

Use the status, blocking, ticker, component, and classification filters to narrow the completeness table. Select individual checkboxes, or click **Select visible findings**, then **Repair selected**. Previously selected rows remain selected when filters change. **Repair all eligible missing-data issues** uses the complete current report, not only visible rows.

Repairs request only affected ticker/date periods. ETF requests are batched by date, and responses are filtered to requested keys. The existing full local adjusted-price rebuild and QFQ methodology remain unchanged; this does not initiate a full historical provider download.

Changes are staged and validated before publication. Empty responses are reported as **Still missing**, not successful repairs. Results distinguish **Repaired and revalidated**, **Still missing**, **Provider error**, and **Not eligible for automatic repair**. A provider error or validation failure cannot publish invalid staged data.

Publication requires the complete staged dataset to pass the existing integrity gate. If unrelated unresolved blockers remain, otherwise useful staged repairs are not published; the results explain that validation blocked publication. Production data is retained. Resolve the other blockers or include all eligible gaps, then retry. No missing prices are fabricated and no unresolved gaps are automatically confirmed.

To retry an unsuccessful repair, use the repair buttons. Reimporting the same already-applied workbook decision is intentionally idempotent. Keep the browser open to follow progress and final results; confirmations and import history persist across application restarts.

## Verification

Automated checks cover Excel round trips, Chinese reasons, raw-to-adjusted inheritance, independent confirmations, reopening, stale/protected/unknown/formula/duplicate input rejection, unchanged findings after unrelated dataset updates, empty and partially failed repairs, targeted ETF batching, publication failure, and research permission.

A real Edge browser test uses temporary synthetic data and the application's HTTP handlers and UI scripts. It downloads a workbook, edits and uploads it, checks the preview, explicitly applies decisions, reloads/revalidates, verifies inherited persistence and duplicate import handling, filters findings, and runs an empty-response repair. Production findings are not confirmed by these tests.
