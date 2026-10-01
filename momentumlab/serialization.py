"""Standards-compliant JSON at API, persistence and downloadable-file boundaries."""
import json
import math
from datetime import date, datetime
import numpy as np
import pandas as pd


def sanitize_for_json(obj):
    if obj is None or obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, pd.DataFrame):
        return sanitize_for_json(obj.to_dict('records'))
    if isinstance(obj, pd.Series):
        return sanitize_for_json(obj.to_dict())
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, np.ndarray)):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, (np.datetime64, pd.Timestamp, datetime, date)):
        return None if pd.isna(obj) else pd.Timestamp(obj).isoformat()
    if isinstance(obj, np.generic):
        return sanitize_for_json(obj.item())
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def dumps(obj, **kwargs):
    kwargs['allow_nan'] = False
    return json.dumps(sanitize_for_json(obj), **kwargs)
