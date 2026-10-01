"""Dataset-wide display and freshness policy; no price or strategy calculations."""
from datetime import datetime, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8))
PUBLICATION_HOUR = 18  # Conservative application cutoff, not a verified proxy SLA.


def freshness_details(calendar, raw, adjusted=None, through=None, now=None):
    import pandas as pd
    now = now or datetime.now(SHANGHAI)
    now = now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now.astimezone(SHANGHAI)
    bound = now.date() if now.hour >= PUBLICATION_HOUR else now.date() - timedelta(days=1)
    if through is not None: bound = min(bound, pd.Timestamp(through).date())
    sessions = sorted(set(calendar.loc[calendar.is_open.eq(1), 'trade_date']))
    due = [d for d in sessions if d <= bound.strftime('%Y%m%d')]
    expected = max(due, default=None)
    raw_last = raw.trade_date.max()
    adj_last = adjusted.trade_date.max() if adjusted is not None else None
    return dict(expected_session=expected, raw_latest=raw_last, adjusted_latest=adj_last,
                raw_session_lag=sum(d > raw_last for d in due),
                adjusted_session_lag=sum(d > adj_last for d in due) if adj_last else None,
                evaluated_at=now.isoformat(), publication_cutoff='18:00 Asia/Shanghai',
                publication_policy='Application planning cutoff after market close; provider/proxy publication SLA is not verified.',
                scope='Latest stored dates across the dataset; individual internal gaps are checked separately.',
                action='Incremental Update if newer provider records are available; no missing-gap repair for this notice.')
