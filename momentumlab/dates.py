"""One daily date representation: naive midnight datetime64[ns]."""
import pandas as pd


def normalize_dates(values):
    raw = pd.Series(values, copy=True)
    if pd.api.types.is_datetime64_any_dtype(raw.dtype):
        if raw.dt.tz is not None:raw=raw.dt.tz_convert(None)
        return raw.dt.normalize().astype('datetime64[ns]')
    text = raw.astype('string').str.strip().str.replace(r'^(\d{8})\.0$', r'\1', regex=True)
    result = pd.to_datetime(text, format='mixed', errors='coerce', utc=True)
    return result.dt.tz_convert(None).dt.normalize().astype('datetime64[ns]')


def daily_iso(value):
    result = normalize_dates([value]).iloc[0]
    if pd.isna(result):
        raise ValueError('Enter a valid daily date.')
    return result.strftime('%Y-%m-%d')


normalize_daily_date = normalize_dates
