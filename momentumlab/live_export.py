"""Auditable, spreadsheet-safe CSV exports plus the full event trail."""
from pathlib import Path
import csv
import json
from .live_store import identity
from .serialization import dumps


def export_portfolio(detail, results_folder):
    output=Path(results_folder)/'live_exports'/f"{detail['portfolio']['id']}_{identity()}"
    output.mkdir(parents=True)
    def write(name,rows,empty_fields):
        fields=list(dict.fromkeys(k for row in rows for k in row)) or empty_fields
        with (output/name).open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields)
            writer.writeheader()
            for row in rows:
                clean={}
                for k,v in row.items():
                    if isinstance(v,(dict,list)): v=dumps(v,ensure_ascii=False)
                    if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')): v="'"+v
                    clean[k]=v
                writer.writerow(clean)
    summary={**detail['portfolio'],**detail['stats'],'asof':detail['asof'],'data_end':detail['data_end'],
             'performance_type':'PAPER SIMULATION' if detail['is_paper'] else 'LIVE ACTUAL PERFORMANCE'}
    write('portfolio_summary.csv',[summary],[])
    write('live_positions.csv',detail['positions'],['ticker','quantity','current_price','market_value'])
    write('live_trades.csv',detail['trades'],['id','date','ticker','side','quantity','price','fee','notes'])
    write('rebalance_history.csv',[{'id':r['id'],**r['payload'],'recorded_at':r['recorded_at']} for r in detail['journals']],['id','date','actual_turnover'])
    write('live_nav.csv',detail['nav'],['date','actual_nav','model_nav'])
    (output/'audit.json').write_text(dumps({'events':detail['events'],'trade_versions':detail['trade_versions'],
                                           'cash_flows':detail.get('cash_flows',[]),'signals':detail['signals']},ensure_ascii=False,indent=2),encoding='utf-8')
    return {p.name:str(p) for p in output.iterdir()}
