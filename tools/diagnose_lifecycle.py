"""Read-only local coverage diagnosis; optional provider lifecycle queries only."""
import sys, json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from momentumlab.completeness import assess_completeness
from momentumlab.data_integrity import normalize_keys

def main():
    root=Path('data/ETF_250M'); output=Path('diagnostics/lifecycle_before.json')
    mapping={'calendar':'calendar.csv','raw_price':'prices.csv','adjusted_price':'adjusted_prices.csv','adjustment_factor':'adj_factors.csv','eligibility':'eligibility.csv'}
    frames={k:normalize_keys(pd.read_csv(root/v,dtype=str),['trade_date']+(['con_code'] if k=='eligibility' else [] if k=='calendar' else ['ts_code'])) for k,v in mapping.items()}
    report=assess_completeness(frames,{'root':str(root)})
    tickers=['563080.SH','589220.SH','520960.SH']; evidence={}
    for t in tickers:
        evidence[t]={'first_raw':frames['raw_price'].loc[frames['raw_price'].ts_code.eq(t),'trade_date'].min(),
            'first_eligibility':frames['eligibility'].loc[frames['eligibility'].con_code.eq(t),'trade_date'].min(),
            'findings':[r for r in report['findings'] if r['ticker']==t]}
    output.write_text(json.dumps({'examples':evidence,'report':report},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(evidence,ensure_ascii=False,default=str))
    if '--provider' in sys.argv:
        import MomentumLabV2 as app
        from datetime import datetime,timezone
        result=[]
        cfg=app.load_config()
        for t in tickers:
            for api,fields in [('fund_basic','ts_code,name,found_date,issue_date,list_date,delist_date,market'),('etf_basic','ts_code,csname,list_date,list_status,exchange')]:
                item={'ticker':t,'api':api,'params':{'ts_code':t},'fields':fields,'timestamp':datetime.now(timezone.utc).isoformat()}
                try:item.update(status='SUCCESS',rows=app.tushare_call(api,item['params'],fields).fillna('').to_dict('records'))
                except Exception as exc:
                    message=str(exc)
                    for key in ('tushare_token','proxy_url'):
                        if cfg.get(key):message=message.replace(cfg[key],'[REDACTED]')
                    item.update(status='ERROR',error=message)
                result.append(item);print(json.dumps(item,ensure_ascii=False))
                Path('diagnostics/lifecycle_provider.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
