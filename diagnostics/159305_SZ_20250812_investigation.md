# Missing ETF price investigation: 159305.SZ / 2025-08-12

Report recorded: 2026-09-21T06:13:34+08:00 (Asia/Shanghai), equivalent to
2026-09-20T22:13:34Z. This is the report timestamp, not an API request timestamp.
The original requests were executed earlier in this conversation; precise request
start/end timestamps were not captured and cannot be reconstructed from their output.
Request IDs below preserve the available provider correlation evidence. No requests
were repeated to create this report.

## Method and credential handling

The investigation used Momentum Lab's existing saved Tushare token and configured
proxy endpoint, read from its local settings. It submitted the same JSON API request
format used by the application. The diagnostic used a read-only HTTP client to
retain the response envelope; it did not invoke data download/publication workflows.
No token, authorization header, settings contents or configured endpoint URL is
included here. Provider request IDs are retained for support correlation.

Both calls used these parameters:

```json
{"ts_code":"159305.SZ","start_date":"20250811","end_date":"20250813"}
```

Requested fields: empty string (endpoint default fields).

## Returned responses

### suspend_d

HTTP status: 200. API status: code 0. Message: empty string.
Request ID: `5d93b2d9-21e2-4880-af37-3fe9d57f55d5`.

```json
{
  "request_id": "5d93b2d9-21e2-4880-af37-3fe9d57f55d5",
  "code": 0,
  "data": {
    "fields": ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
    "items": [],
    "has_more": false,
    "count": 0
  },
  "msg": "",
  "detail": "..."
}
```

An earlier isolated test in this conversation also returned no suspension rows
with code 0: request ID `a93fe1a4-043a-4449-ba0e-313997cb159c`.
Its precise request timestamp was also not captured.

### fund_daily

HTTP status: 200. API status: code 0. Message: empty string.
Request ID: `72ca6f5f-de41-481a-ab36-79b26f347324`.

```json
{
  "request_id": "72ca6f5f-de41-481a-ab36-79b26f347324",
  "code": 0,
  "data": {
    "fields": ["ts_code", "trade_date", "pre_close", "open", "high", "low", "close", "change", "pct_chg", "vol", "amount"],
    "items": [
      ["159305.SZ", "20250813", 1.319, 1.319, 1.362, 1.319, 1.362, 0.043, 3.26, 37770, 5098.494],
      ["159305.SZ", "20250811", 1.282, 1.295, 1.319, 1.292, 1.319, 0.037, 2.8861, 42099, 5521.873]
    ],
    "has_more": false,
    "count": 0
  },
  "msg": "",
  "detail": "..."
}
```

The `detail` value was literally `"..."` in the returned response. It is not a
redaction of additional evidence. Rows are retained in their original returned order.
No August 12 price row was returned.

## Additional user observation

The user reports checking Tonghuashun and finding no daily K-line for 2025-08-12.
This is user-supplied evidence, not independently inspected by this diagnostic.
The observation's exact time and a supporting screenshot/reference were not supplied.

## Interpretation

The configured provider returned price records for August 11 and August 13, but
none for August 12. The user's Tonghuashun observation independently reports the
same visible chart gap. These observations support the existence of a missing
daily-price record in these sources, but do not establish its cause.

Suspension is NOT confirmed. An empty suspend_d response cannot establish normal
trading because its historical ETF coverage has not been confirmed. Possible
explanations remain unverified; this report makes no trading-status classification.
No official-versus-proxy comparison was performed.

## Client inspection: count 0 versus two rows

Inspected the current application source without changing it:

- `MomentumLabV2.py`, `tushare_call`: on code 0 (or absent code), constructs a
  pandas DataFrame from `data.items` and `data.fields`. It does not read `data.count`.
- `fetch_etf_date_batches`: processes the returned DataFrame using its actual
  length, filters ticker membership, accumulates returned rows and upserts them.
  It does not use the response count metadata to decide whether rows exist.
- `fetch_stock_history`: also processes actual returned DataFrame rows rather
  than response count metadata.

Therefore, count=0 cannot cause this client to discard these two valid returned
rows. The August 12 record is already absent from the provider response; this
metadata inconsistency does not explain that absence.

The client also does not consume `has_more` from the response envelope. That is
a separate pagination/coverage limitation to investigate if a future response
indicates more pages; this response reports has_more=false. The meaning of the
proxy's count field, and whether it differs from official Tushare, is unconfirmed.

## Provider message draft (not sent)

Hello, we queried your configured Tushare-compatible service for 159305.SZ using
start_date=20250811 and end_date=20250813. Both requests succeeded (HTTP 200,
code 0). suspend_d returned no records; fund_daily returned August 11 and August
13 but no August 12. Tonghuashun also shows no daily K-line for August 12.
We have not classified the gap as a suspension.

Could you please clarify:

1. Does your suspend_d endpoint cover ETFs, including historical full-day and
   intraday suspension/resumption events?
2. Which endpoint supplies historical ETF suspension/resumption records?
3. Why is 159305.SZ missing from fund_daily on 2025-08-12? Is there documented
   exchange evidence explaining the missing record?
4. Could your proxy's historical coverage or filtering differ from official
   Tushare? Can you compare this specific query with the official source?
5. Why does fund_daily return count=0 despite two items and has_more=false?
   What do count and has_more mean in your response schema?

Request IDs for tracing:
suspend_d: 5d93b2d9-21e2-4880-af37-3fe9d57f55d5
fund_daily: 72ca6f5f-de41-481a-ab36-79b26f347324

## Changes made

Only this diagnostic document was created. No application code, production data,
integrity acknowledgment or suspension classification was changed. The provider
message has not been sent.
