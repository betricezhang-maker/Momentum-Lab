"""Explicit buy-and-hold benchmark loading and comparable statistics."""
from pathlib import Path
import hashlib
import numpy as np
import pandas as pd


SUPPORTED_BASIS = {'price_return', 'adjusted_etf_return', 'total_return'}


def load_benchmark(path, ticker, name, basis='price_return'):
    if not path or not Path(path).is_file():
        raise ValueError('Selected benchmark is unavailable: configure a benchmark CSV first.')
    if basis not in SUPPORTED_BASIS:
        raise ValueError(f'Unsupported benchmark return basis: {basis}')
    frame=pd.read_csv(path, low_memory=False)
    required={'trade_date','close'}
    if not required.issubset(frame.columns):
        raise ValueError('Benchmark CSV requires trade_date and close columns.')
    if 'ts_code' in frame.columns:
        frame=frame[frame.ts_code.astype(str).str.strip().eq(str(ticker).strip())]
    frame['trade_date']=pd.to_datetime(frame['trade_date'].astype(str).str.replace(r'\.0$','',regex=True), errors='coerce',format='mixed')
    frame['close']=pd.to_numeric(frame['close'], errors='coerce')
    if frame[['trade_date','close']].isna().any().any() or (~np.isfinite(frame['close'])).any() or (frame['close']<=0).any():
        raise ValueError('Benchmark contains invalid dates or prices; repair its source file before comparing.')
    if frame.trade_date.duplicated().any():raise ValueError('Benchmark contains duplicate trading dates; resolve them before comparing.')
    frame=frame.sort_values('trade_date')
    if frame.empty: raise ValueError(f'Benchmark {ticker} has no usable rows in the configured file.')
    frame=frame.drop_duplicates('trade_date', keep='last').set_index('trade_date')['close'].rename('benchmark_close')
    return frame, dict(name=name, ticker=ticker, source=str(Path(path).resolve()), basis=basis,dataset_fingerprint=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                       basis_label={'price_return':'price return','adjusted_etf_return':'adjusted ETF return','total_return':'total return'}[basis])


def compare_equity(strategy_nav, benchmark_close, start=None, end=None, annualization=252):
    s=pd.Series(strategy_nav).copy(); s.index=pd.to_datetime(s.index).normalize()
    b=pd.Series(benchmark_close).copy(); b.index=pd.to_datetime(b.index).normalize()
    s=s.sort_index(); b=b.sort_index()
    if start is not None: s=s[s.index>=pd.Timestamp(start)]; b=b[b.index>=pd.Timestamp(start)]
    if end is not None: s=s[s.index<=pd.Timestamp(end)]; b=b[b.index<=pd.Timestamp(end)]
    if s.index.duplicated().any() or b.index.duplicated().any():raise ValueError('Comparison dates must be unique.')
    missing=s.index.difference(b.dropna().index)
    if len(missing):raise ValueError('Benchmark coverage incomplete: missing '+str(len(missing))+' strategy valuation dates, including '+', '.join(missing[:8].strftime('%Y-%m-%d'))+'. Download this date range; comparison was not shortened or forward-filled.')
    both=pd.concat([s.rename('strategy'),b.reindex(s.index).rename('benchmark')],axis=1)
    if both.isna().any().any() or (~np.isfinite(both)).any().any() or (both<=0).any().any():raise ValueError('Comparison requires finite positive valuations.')
    if len(both)<2: raise ValueError('Benchmark does not cover the strategy evaluation dates without forward-filling.')
    both=both/both.iloc[0]*100
    returns=both.pct_change(fill_method=None).dropna()
    out=[]
    for col in both:
        eq=both[col]/100; dd=eq/eq.cummax()-1
        out.append(dict(series=col, start=both.index[0].strftime('%Y-%m-%d'), end=both.index[-1].strftime('%Y-%m-%d'),
                        observations=len(both), cumulative_return=float(eq.iloc[-1]-1),
                        cagr=float(eq.iloc[-1]**(annualization/max(len(eq)-1,1))-1) if len(eq)>1 else None,
                        annualized_volatility=float(returns[col].std(ddof=1)*np.sqrt(annualization)),
                        max_drawdown=float(dd.min())))
    annual=[]
    for year,group in returns.groupby(returns.index.year):
        annual.append(dict(year=int(year),strategy=float((1+group.strategy).prod()-1),benchmark=float((1+group.benchmark).prod()-1),partial=bool(year==both.index[0].year or year==both.index[-1].year)))
    return both, {x['series']:x for x in out}, dict(requested_start=str(start) if start else None, requested_end=str(end) if end else None,calendar_year_returns=annual,annualization_sessions=annualization,normalization='Both start at 100 at the first shared strategy valuation. Costs already reflected before that valuation are outside the comparison; subsequent strategy costs are included.',
        actual_start=both.index[0].strftime('%Y-%m-%d'), actual_end=both.index[-1].strftime('%Y-%m-%d'),
        comparison_observations=len(both), excess_return=float(both.strategy.iloc[-1]/100-both.benchmark.iloc[-1]/100),
        excess_return_definition='strategy cumulative return minus benchmark cumulative return over the identical observed dates; not regression alpha')


def save_comparison_chart(curves, path, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax=plt.subplots(figsize=(10,5))
    for column in curves.columns: ax.plot(curves.index, curves[column], label=column)
    ax.set_title(title+'\nNormalized to 100 on identical observed dates')
    ax.set_xlabel('Date'); ax.set_ylabel('Normalized value'); ax.legend(); ax.grid(True, alpha=.25)
    fig.tight_layout(); fig.savefig(path,dpi=160,bbox_inches='tight'); plt.close(fig)
