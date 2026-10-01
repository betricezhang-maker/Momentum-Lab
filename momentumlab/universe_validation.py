"""Runtime ETF checks shared by research and live model generation."""
import pandas as pd

ETF_LABEL = 'Equity ETF ≥ RMB250M — Excludes bond, cash/money-market and commodity/gold ETFs'


def equity_classification(frame):
    kind = frame['fund_type'].fillna('').astype(str)
    equity = kind.str.contains('股票|equity|stock', case=False, regex=True)
    excluded = kind.str.contains('债券|固定收益|货币|现金|商品|黄金|bond|fixed.income|money|cash|commodity|gold|REIT', case=False, regex=True)
    return frame[equity & ~excluded].copy()


def validate_etf_weights(weights, equity_codes):
    required = {'trade_date', 'con_code', 'market_cap_rmb'}
    if not required.issubset(weights.columns):
        raise ValueError('ETF eligibility must include historical market_cap_rmb, trade_date and con_code. Rebuild eligibility.')
    cap = pd.to_numeric(weights['market_cap_rmb'], errors='coerce')
    if cap.isna().any() or (~cap.map(lambda v: 0 <= v < float('inf'))).any():
        raise ValueError('ETF historical size evidence is invalid or missing.')
    selected = weights[weights['con_code'].astype(str).isin(equity_codes) & cap.ge(250_000_000)].copy()
    if selected.empty:
        raise ValueError('No equity ETFs meet historical size ≥ RMB250M.')
    return selected
