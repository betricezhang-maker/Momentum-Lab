"""Securities-only one-way turnover. Initial cash deployment is reported separately."""
import json
import math
import statistics


def compare_weights(old, target, initial=False):
    old = {str(k): float(v) for k, v in old.items() if float(v) != 0}
    target = {str(k): float(v) for k, v in target.items() if float(v) != 0}
    if any(not math.isfinite(v) or v < 0 for v in [*old.values(), *target.values()]):
        raise ValueError('Weights must be finite and nonnegative.')
    keep, sell, buy = sorted(old.keys() & target.keys()), sorted(old.keys() - target.keys()), sorted(target.keys() - old.keys())
    return {
        'old_weights': json.dumps(old, sort_keys=True), 'target_weights': json.dumps(target, sort_keys=True),
        'keep': ','.join(keep), 'sell': ','.join(sell), 'buy': ','.join(buy),
        'retained': len(keep), 'sold': len(sell), 'bought': len(buy),
        'retention_rate': len(keep) / len(old) if old else None,
        'one_way_turnover': 0.5 * sum(abs(target.get(k, 0) - old.get(k, 0)) for k in old.keys() | target.keys()),
        'initial_funding': bool(initial),
    }


def summarize_turnover(history, observations):
    rows = [r for r in history if not r.get('initial_funding')]
    def average(key):
        values=[float(r[key]) for r in rows if r.get(key) is not None and math.isfinite(float(r[key]))]
        return sum(values)/len(values) if values else None
    total = sum(float(r['one_way_turnover']) for r in rows)
    return dict(average_turnover=average('one_way_turnover'), annualized_turnover=total * 252 / max(observations - 1, 1),
                total_turnover=total, average_holdings_retained=average('retained'), average_holdings_replaced=average('bought'),
                holdings_retention_rate=average('retention_rate'), maximum_rebalance_turnover=max((r['one_way_turnover'] for r in rows), default=None),
                median_turnover=statistics.median([r['one_way_turnover'] for r in rows]) if rows else None,
                recurring_rebalances=len(rows), initial_turnover=sum(r['one_way_turnover'] for r in history if r.get('initial_funding')))
