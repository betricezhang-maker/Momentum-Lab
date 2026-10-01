"""Manual/paper portfolio workflow; no brokerage or order submission."""
from datetime import date
from bisect import bisect_right
import json
import math
from .live_store import identity, stamp, encode
from .live_valuation import active_trades, active_cash_flows, flow_amount, ledger, mark, build_nav, performance, Quotes
from .live_model import target_snapshot, scheduled_signals, next_schedule
from .turnover import compare_weights, summarize_turnover
from .reconstruction import reconcile, compare_strategy_targets
from .rebalance_workflow import recommendations, workflow_status, FINAL_STATUSES
from .costs import validate_cost
from .dates import daily_iso

STATUSES={'ACTIVE','PAUSED','CLOSED','PAPER'}


def day(value):
    return daily_iso(value)


def finite(value, positive=False):
    result=float(value)
    if not math.isfinite(result) or (result<=0 if positive else result<0):
        raise ValueError('Enter finite, nonnegative values (price/quantity/capital must be positive).')
    return result


class LiveService:
    def __init__(self, store, market_provider, reconstruction_provider=None, preview_provider=None):
        self.store=store
        self.market_provider=market_provider
        self.reconstruction_provider=reconstruction_provider
        self.preview_provider=preview_provider or reconstruction_provider

    @staticmethod
    def _historical_anchor(params, market):
        requested=day(params['start_date'])
        basis=params.get('start_date_type','SIGNAL').upper()
        if basis=='SIGNAL':
            at=bisect_right(market['calendar'],requested)-1
            if at<0: raise ValueError('INVALID SIGNAL DATE: no open session on/before requested date.')
            signal=market['calendar'][at]
            execution=market['calendar'][at+1] if at+1<len(market['calendar']) else None
        elif basis=='EXECUTION':
            try:
                at=market['calendar'].index(requested)
                if at==0: raise ValueError
                signal=market['calendar'][at-1];execution=requested
            except ValueError:
                raise ValueError('Historical execution date must be an open session with a preceding signal session.')
        else: raise ValueError('Historical start date type must be SIGNAL or EXECUTION.')
        return requested,basis,signal,execution

    @staticmethod
    def _check_historical_target(bundle,start,execution):
        if not bundle or not bundle.get('signals'): raise ValueError('NO TARGET FOUND: no research target for the resolved signal.')
        checks=bundle.get('integrity') or {}
        if any((checks.get(k) or {}).get('match') is False for k in ['research_vs_backtest','saved_grid_vs_backtest']):
            raise ValueError('STRATEGY TARGET MISMATCH: research/backtest/saved target disagree.')
        first=bundle['signals'][0]
        if first['signal_date']!=start or not execution or first['execution_date']!=execution:
            raise ValueError('INVALID SIGNAL DATE: target does not match the resolved signal and next open execution session.')
        weights=first.get('weights',{})
        if not weights or not all(math.isfinite(w) and w>0 for w in weights.values()) or not math.isclose(sum(weights.values()),1.,abs_tol=1e-9):
            raise ValueError('NO TARGET FOUND: target weights are missing or invalid.')
        return first

    @staticmethod
    def _capital_adjustment(params, effective_date):
        choice=str(params.get('capital_adjustment','NONE')).upper()
        if choice in {'NO CHANGE','NO_CHANGE'}: choice='NONE'
        if choice in {'ADD','CONTRIBUTION'}: kind='CONTRIBUTION'
        elif choice in {'WITHDRAW','WITHDRAWAL'}: kind='WITHDRAWAL'
        elif choice=='NONE': return None
        else: raise ValueError('Capital adjustment must be NONE, ADD or WITHDRAW.')
        amount=finite(params.get('capital_amount',0),True)
        return {'kind':kind,'amount':amount,'date':day(effective_date)}

    @staticmethod
    def _proposal(detail, params=None):
        params=params or {};workflow=detail['rebalance_workflow'];target_record=next((s for s in detail['signals'] if s['id']==workflow.get('signal_id')),None)
        flow=LiveService._capital_adjustment(params,params.get('capital_date') or detail['asof'])
        if flow and not detail['portfolio']['start_date']<=flow['date']<=date.today().isoformat():
            raise ValueError('Capital flow date must be within the portfolio tracking period.')
        if flow and workflow.get('execution_date') and flow['date']<workflow['execution_date']:
            raise ValueError('Rebalance capital cannot predate the scheduled execution.')
        contribution=flow['amount'] if flow and flow['kind']=='CONTRIBUTION' else 0.
        withdrawal=flow['amount'] if flow and flow['kind']=='WITHDRAWAL' else 0.
        current_nav=detail['stats']['current_nav'];investable=current_nav+contribution-withdrawal
        if investable<=0: raise ValueError('Capital withdrawal must leave a positive investable NAV.')
        if not target_record:
            return dict(current_nav=current_nav,capital_added=contribution,capital_withdrawn=withdrawal,investable_nav=investable,
                        target_holdings_count=0,target_weight_per_holding=None,model_turnover=None,estimated_trading_cost=None,
                        total_sell_value=0,total_buy_value=0,net_cash_change=contribution-withdrawal,
                        expected_post_rebalance_cash=detail['stats']['cash']+contribution-withdrawal,recommendations=[],final_portfolio=[])
        target=target_record['payload']
        rows,comparison=recommendations(detail['positions'],target['weights'],target.get('names',{}),investable,target.get('ranks',{}),target.get('scores',{}))
        cost=investable*comparison['one_way_turnover']*detail['portfolio']['strategy']['cost_bps']/10000
        if cost>=investable: raise ValueError('Estimated trading cost must leave positive portfolio capital.')
        rows,_=recommendations(detail['positions'],target['weights'],target.get('names',{}),investable,target.get('ranks',{}),target.get('scores',{}),allocation_nav=investable-cost)
        sells=-sum(min(r['suggested_trade_value'],0) for r in rows);buys=sum(max(r['suggested_trade_value'],0) for r in rows)
        expected_cash=detail['stats']['cash']+contribution-withdrawal+sells-buys-cost
        final=[dict(ticker=r['ticker'],name=r['name'],rank=r['rank'],status=r['status'],final_quantity=r['final_quantity'],
                    final_target_weight=r['target_weight'],final_target_value=r['final_target_value'],change_vs_previous=r['change_vs_previous'])
               for r in rows if r['target_weight']>0]
        return dict(current_nav=current_nav,capital_added=contribution,capital_withdrawn=withdrawal,investable_nav=investable,
                    target_holdings_count=len(target['weights']),target_weight_per_holding=(next(iter(target['weights'].values())) if target['weights'] else None),
                    model_turnover=comparison['one_way_turnover'],estimated_trading_cost=cost,total_sell_value=sells,total_buy_value=buys,
                    net_cash_change=contribution-withdrawal+sells-buys-cost,expected_post_rebalance_cash=expected_cash,
                    recommendations=rows,final_portfolio=final,comparison=comparison,capital_flow=flow)

    def preview_historical(self, params, source):
        strategy={k:source[k] for k in ('universe','lookback','selection','rebalance_days','cost_bps')}
        if 'cost_bps' in params: strategy['cost_bps']=finite(params['cost_bps'])
        validate_cost(strategy['cost_bps'])
        market=self.market_provider(strategy)
        requested,basis,start,mapped_execution=self._historical_anchor(params,market)
        end=day(params['end_date'])
        if not start<=end<=date.today().isoformat(): raise ValueError('Historical end must follow the signal date and not be in the future.')
        if not mapped_execution or mapped_execution>end: raise ValueError('NO TARGET FOUND: end date must include the resolved execution session.')
        bundle=self.preview_provider(strategy,start,end,source) if self.preview_provider else None
        first=self._check_historical_target(bundle,start,mapped_execution)
        holdings=[dict(ticker=t,rank=first.get('ranks',{}).get(t),score=first.get('scores',{}).get(t),target_weight=w,
                       name=first.get('names',{}).get(t,t)) for t,w in first['weights'].items()]
        holdings.sort(key=lambda r:(r['rank'] is None,r['rank'] or 0,r['ticker']))
        if basis=='EXECUTION' and first['execution_date']!=mapped_execution: raise ValueError('The selected execution date does not match the reconstructed signal cycle.')
        return dict(preview_status='READY',requested_start_date=requested,start_date_type=basis,signal_date=first['signal_date'],execution_date=first['execution_date'],holdings=holdings,
                    strategy=strategy,integrity=bundle.get('integrity'),dataset_integrity=market.get('integrity',{}),
                    resolution_rule='Nearest exchange-open signal date on/before requested date' if basis=='SIGNAL' else 'Open execution session and preceding signal session',
                    run_id=source['run_id'],provenance=first.get('provenance'),rebalance_count=len(bundle['signals']))

    def create(self, params, source):
        name=str(params.get('name','')).strip()
        if not name or len(name)>120: raise ValueError('Enter a portfolio name (1–120 characters).')
        start_mode=params.get('start_mode','legacy')
        if start_mode not in {'legacy','today','historical'}: raise ValueError('Choose today or historical start.')
        requested_start=date.today().isoformat() if start_mode=='today' else day(params['start_date'])
        if requested_start>date.today().isoformat(): raise ValueError('Tracking start cannot be in the future.')
        capital=finite(params['capital'],True)
        status=params.get('status','PAPER')
        if status not in {'ACTIVE','PAPER'}: raise ValueError('New portfolios must be ACTIVE or PAPER.')
        strategy={k:source[k] for k in ('universe','lookback','selection','rebalance_days','cost_bps')}
        strategy.update(mode=status,paper_engine='automatic' if status=='PAPER' else 'manual')
        if 'cost_bps' in params: strategy['cost_bps']=finite(params['cost_bps'])
        validate_cost(strategy['cost_bps'])
        market=self.market_provider(strategy)
        if start_mode=='historical':
            requested_start,start_basis,start,mapped_execution=self._historical_anchor(params,market)
            end=day(params['end_date'])
            if not start<=end<=date.today().isoformat(): raise ValueError('Historical end must follow start and not be in the future.')
            if not self.reconstruction_provider: raise ValueError('Historical reconstruction is unavailable.')
            bundle=self.reconstruction_provider(strategy,start,end,source)
            self._check_historical_target(bundle,start,mapped_execution)
            strategy.update(historical_end=end,continue_live=end>=min(date.today().isoformat(),market['data_end']),
                            reconstruction=bundle,signal_anchor=bundle['signals'][0]['signal_date'],
                            requested_start_date=requested_start,start_date_type=start_basis,
                            mapped_execution_date=mapped_execution or bundle['signals'][0].get('execution_date'))
            snapshots=bundle['signals']
        else:
            start=requested_start
            snapshots=[target_snapshot(strategy,market,start)]
            strategy['signal_anchor']=snapshots[0]['signal_date']
        strategy['dataset_provenance']=market.get('provenance')
        pid=identity()
        with self.store.transaction() as db:
            db.execute('INSERT INTO portfolios(id,name,status,start_date,capital,strategy,source_run_id,created_at,mode,archived,parent_id,activation_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (pid,name,status,start,capital,encode(strategy),source['run_id'],stamp(),status,0,params.get('parent_id'),params.get('activation_date')))
            self.store.event(db,pid,'Portfolio Created',{'source':source,'capital':capital,'status':status,'start_date':start})
            for snapshot in snapshots: self._save_signal(db,pid,snapshot)
        return {'id':pid,'name':name,'status':status}

    def _save_signal(self, db, pid, snapshot):
        exists=db.execute('SELECT id FROM signals WHERE portfolio_id=? AND signal_date=?',(pid,snapshot['signal_date'])).fetchone()
        if exists: return
        snapshot=dict(snapshot)
        snapshot['provenance']={**snapshot.get('provenance',{}),'strategy_run_id':self.store.portfolio(db,pid)['source_run_id']}
        sid=identity()
        db.execute('INSERT INTO signals VALUES(?,?,?,?,?)',(sid,pid,snapshot['signal_date'],encode(snapshot),stamp()))
        self.store.event(db,pid,'Signal Generated',{'signal_id':sid,**snapshot})

    def record_trade(self, pid, params):
        ticker=str(params.get('ticker','')).strip().upper()
        if not ticker or len(ticker)>32: raise ValueError('Enter a ticker.')
        side=params.get('side')
        if side not in {'BUY','SELL','DIVIDEND','SPLIT'}: raise ValueError('Choose BUY, SELL, DIVIDEND or SPLIT.')
        payload=dict(ticker=ticker,side=side,date=day(params['date']),price=finite(params.get('price',0),side in {'BUY','SELL'}),
                     quantity=finite(params.get('quantity',0),side in {'BUY','SELL','SPLIT'}),fee=finite(params.get('fee',0)),notes=str(params.get('notes',''))[:2000])
        request_id=str(params.get('request_id') or identity())
        supersedes=params.get('supersedes') or None
        with self.store.transaction() as db:
            p=self.store.portfolio(db,pid)
            if p['strategy'].get('paper_engine')=='automatic': raise ValueError('Automatic PAPER portfolios follow model targets; manual trades require ACTIVE mode.')
            retry=db.execute('SELECT id,portfolio_id,payload FROM trades WHERE request_id=?',(request_id,)).fetchone()
            if retry:
                if retry['portfolio_id']!=pid: raise ValueError('Request identifier belongs to another portfolio.')
                previous=json.loads(retry['payload'])
                if any(previous.get(k)!=v for k,v in payload.items()): raise ValueError('This request identifier was already used for different trade details.')
                return {'id':retry['id'],'duplicate':True}
            if p['status']=='CLOSED': raise ValueError('Closed portfolios are read-only.')
            if p['archived']: raise ValueError('Archived portfolios are read-only.')
            if not p['start_date']<=payload['date']<=date.today().isoformat(): raise ValueError('Trade date must be within the tracking period and not in the future.')
            records=self.store.records(db,'trades',pid)
            if supersedes:
                previous=next((r for r in records if r['id']==supersedes),None)
                if not previous or any(r.get('supersedes')==supersedes for r in records): raise ValueError('Correct the latest version of an existing trade.')
                if previous['payload'].get('side')=='OPENING_TRANSFER': raise ValueError('Opening continuation records cannot be edited.')
                payload['sequence']=previous['payload'].get('sequence',previous['recorded_at'])
            tid=identity(); recorded=stamp()
            candidate={'id':tid,'supersedes':supersedes,'payload':payload,'recorded_at':recorded}
            flows=active_cash_flows(self.store.records(db,'cash_flows',pid))
            ledger(p['capital'],active_trades(records+[candidate]),cash_flows=flows)
            db.execute('INSERT INTO trades VALUES(?,?,?,?,?,?)',(tid,pid,supersedes,request_id,encode(payload),recorded))
            self.store.event(db,pid,'Trade Edited' if supersedes else 'Trade Recorded',candidate)
        return {'id':tid}

    def set_status(self,pid,status):
        if status not in STATUSES: raise ValueError('Invalid portfolio status.')
        with self.store.transaction() as db:
            p=self.store.portfolio(db,pid)
            if p['status']=='CLOSED': raise ValueError('Closed portfolios are read-only.')
            if p['archived']: raise ValueError('Archived portfolios are read-only.')
            # PAPER is a permanent ledger classification: never relabel simulated fills as real trades.
            if status in {'ACTIVE','PAPER'}:
                created=self.store.records(db,'events',pid)[0]['payload']['status']
                if (created=='PAPER') != (status=='PAPER'): raise ValueError('Create a separate portfolio to switch between paper and actual capital.')
            if status=='CLOSED':
                p['strategy']['closed_date']=date.today().isoformat()
            db.execute('UPDATE portfolios SET status=?,strategy=? WHERE id=?',(status,encode(p['strategy']),pid))
            self.store.event(db,pid,'Portfolio '+status.title(),{'from':p['status'],'to':status,'effective_date':date.today().isoformat()})

    def archive(self,pid):
        with self.store.transaction() as db:
            p=self.store.portfolio(db,pid)
            if p['archived']: return
            p['strategy']['archived_from_status']=p['status']
            db.execute('UPDATE portfolios SET archived=1,status=?,strategy=? WHERE id=?',('ARCHIVED',encode(p['strategy']),pid))
            self.store.event(db,pid,'Portfolio Archived',{'status':p['status']})

    def restore(self,pid):
        with self.store.transaction() as db:
            p=self.store.portfolio(db,pid)
            if not p['archived']: return
            restored=p['strategy'].pop('archived_from_status','PAUSED')
            if restored not in STATUSES: restored='PAUSED'
            db.execute('UPDATE portfolios SET archived=0,status=?,strategy=? WHERE id=?',(restored,encode(p['strategy']),pid))
            self.store.event(db,pid,'Portfolio Restored',{'status':restored})

    def delete_portfolio(self,pid, confirmation):
        with self.store.transaction() as db:
            p=self.store.portfolio(db,pid)
            if confirmation!='DELETE': raise ValueError('Type DELETE to permanently delete this portfolio.')
            if db.execute('SELECT 1 FROM portfolios WHERE parent_id=?',(pid,)).fetchone():
                raise ValueError('This trial has a linked ACTIVE continuation; archive it to preserve the audit relationship.')
            for table in ('nav_history','rebalances','cash_flows','trades','signals','events'):
                db.execute(f'DELETE FROM {table} WHERE portfolio_id=?',(pid,))
            db.execute('DELETE FROM portfolios WHERE id=?',(pid,))

    def delete_trial(self,pid, confirmation='DELETE'):
        return self.delete_portfolio(pid,confirmation)

    def promote(self,pid,params):
        paper=self.detail(pid)
        p=paper['portfolio']
        if p['mode']!='PAPER' or p['archived']: raise ValueError('Choose an unarchived PAPER portfolio.')
        activation=day(params.get('activation_date',date.today().isoformat()))
        if not p['start_date']<=activation<=date.today().isoformat(): raise ValueError('Activation date must be within the paper tracking period and not in the future.')
        if activation<paper['asof']: raise ValueError('Activation cannot precede the current Paper state date.')
        held=[r for r in paper['positions'] if r.get('quantity',0)>1e-9]
        opening_nav=paper['stats']['current_nav'];opening_cash=paper['stats']['cash']
        raw_quotes=Quotes(self.market_provider(p['strategy'])['raw'])
        incompatible=[r['ticker'] for r in held if raw_quotes.get(r['ticker'],activation)[0] is None or
                      not math.isclose(raw_quotes.get(r['ticker'],activation)[0],r['current_price'],rel_tol=1e-8,abs_tol=1e-8)]
        if incompatible:
            raise ValueError('Cannot preserve both Paper model-unit quantities and NAV at raw actual prices for: '+', '.join(incompatible)+'. Reconcile model units to actual shares before activation.')
        applied_signals=self._paper_signals(paper['signals'],paper['journals'])
        last_signal_date=applied_signals[-1]['signal_date'] if applied_signals else paper['signals'][0]['signal_date']
        source_signal=next((s for s in reversed(paper['signals']) if s['signal_date']==last_signal_date),paper['signals'][0])
        strategy=dict(p['strategy'])
        strategy.update(mode='ACTIVE',paper_engine='manual',continue_live=True,continued_from_paper=pid,
                        continuity_start_date=p['start_date'],continuity_starting_capital=p['capital'],
                        inherited_nav=[dict(date=r['date'],actual_nav=r.get('paper_nav') or r.get('model_nav'),model_nav=r.get('model_nav'),mode='PAPER') for r in paper['nav']],
                        inherited_cash_flows=paper.get('cash_flows',[]),inherited_stats=paper['stats'],
                        inherited_position_history=paper.get('position_history',[]),
                        inherited_snapshots=[e['payload'] for e in paper['events'] if e['event_type']=='NAV Snapshot'],
                        inherited_last_signal=paper['rebalance_workflow'].get('last_signal_date'),
                        inherited_last_rebalance=paper['rebalance_workflow'].get('last_rebalance_date'),
                        opening_model_state={'cash':opening_cash,'quantities':{r['ticker']:r['quantity'] for r in held},
                                             'average_costs':{r['ticker']:r.get('average_cost',r.get('current_price',0)) for r in held}})
        new_id=identity();name=str(params.get('name') or p['name']+' — Active').strip()
        if not name or len(name)>120: raise ValueError('Enter an active portfolio name (1–120 characters).')
        with self.store.transaction() as db:
            db.execute('INSERT INTO portfolios(id,name,status,start_date,capital,strategy,source_run_id,created_at,mode,archived,parent_id,activation_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (new_id,name,'ACTIVE',activation,opening_nav,encode(strategy),p['source_run_id'],stamp(),'ACTIVE',0,pid,activation))
            self.store.event(db,new_id,'Portfolio Created',{'source_run_id':p['source_run_id'],'capital':opening_nav,'status':'ACTIVE','start_date':activation,'continuation':True})
            self._save_signal(db,new_id,source_signal['payload'])
            active_signal=db.execute('SELECT id FROM signals WHERE portfolio_id=?',(new_id,)).fetchone()[0]
            finalized={j['payload'].get('signal_id') for j in paper['journals'] if j['payload'].get('status') in FINAL_STATUSES}
            for pending in paper['signals']:
                if pending['signal_date']>last_signal_date and pending['id'] not in finalized:
                    self._save_signal(db,new_id,pending['payload'])
            transfer_ids=[]
            for position in held:
                tid=identity();recorded=stamp()
                payload=dict(ticker=position['ticker'],side='OPENING_TRANSFER',date=activation,price=position['current_price'],
                             quantity=position['quantity'],fee=0.,basis=position.get('basis',position['market_value']),
                             notes=f"Opening transfer from PAPER portfolio {pid}",sequence=recorded,source='PAPER_CONTINUATION')
                db.execute('INSERT INTO trades VALUES(?,?,?,?,?,?)',(tid,new_id,None,identity(),encode(payload),recorded));transfer_ids.append(tid)
            opening_weights={r['ticker']:r.get('actual_weight',0) for r in held}
            opening_rebalance=dict(kind='PAPER_TO_ACTIVE_CONTINUATION',status='COMPLETED',initial_funding=True,signal_id=active_signal,
                                   signal_date=source_signal['signal_date'],execution_date=source_signal['payload'].get('execution_date'),
                                   actual_trade_date=activation,holdings_before=opening_weights,target_holdings=source_signal['payload']['weights'],
                                   actual_trades=transfer_ids,holdings_after=opening_weights,nav_before=opening_nav,contribution=0.,withdrawal=0.,
                                   investable_nav=opening_nav,nav_after=opening_nav,model_turnover=0.,actual_turnover=0.,model_cost=0.,fees=0.,
                                   tracking_difference=max((abs(source_signal['payload']['weights'].get(t,0)-opening_weights.get(t,0)) for t in source_signal['payload']['weights'].keys()|opening_weights.keys()),default=0),
                                   notes='Opening state transferred from PAPER; no market trade or turnover.',timestamp=stamp())
            rid=identity();db.execute('INSERT INTO rebalances VALUES(?,?,?,?)',(rid,new_id,encode(opening_rebalance),stamp()))
            p['strategy']['continued_at']=paper['asof']
            db.execute('UPDATE portfolios SET status=?,strategy=? WHERE id=?',('PAUSED',encode(p['strategy']),pid))
            self.store.event(db,pid,'Continued As Active',{'active_portfolio_id':new_id,'activation_date':activation,'opening_nav':opening_nav})
            self.store.event(db,new_id,'Continued From Paper',{'paper_portfolio_id':pid,'activation_date':activation,'opening_nav':opening_nav,
                                                               'cash':opening_cash,'holdings':held,'opening_rebalance_id':rid})
        return {'id':new_id,'name':name,'status':'ACTIVE','opening_nav':opening_nav}

    def detail(self,pid,refresh=False):
        with self.store.transaction() as db:
            p=self.store.portfolio(db,pid)
        market=self.market_provider(p['strategy'])
        asof=min(date.today().isoformat(),max(market['data_end'],p['start_date']))
        asof=min(asof,p['strategy'].get('closed_date',asof))
        asof=min(asof,p['strategy'].get('continued_at',asof))
        if not p['strategy'].get('continue_live'):
            asof=min(asof,p['strategy'].get('historical_end',asof))
        with self.store.transaction() as db:
            p=self.store.portfolio(db,pid)
            if p['status'] in {'ACTIVE','PAPER'} and (not p['strategy'].get('historical_end') or p['strategy'].get('continue_live')) and not p['archived']:
                transitions=[e for e in self.store.records(db,'events',pid) if 'to' in e['payload']]
                for when in scheduled_signals(p['strategy'].get('signal_anchor',p['start_date']),market['calendar'],p['strategy']['rebalance_days'],min(asof,market['data_end'])):
                    if when<p['start_date']: continue
                    paused=False
                    for event in transitions:
                        if event['payload'].get('effective_date',event['recorded_at'][:10])<=when:
                            paused=event['payload']['to']=='PAUSED'
                    if paused: continue
                    if not db.execute('SELECT 1 FROM signals WHERE portfolio_id=? AND signal_date=?',(pid,when)).fetchone():
                        snapshot=target_snapshot(p['strategy'],market,when)
                        if snapshot['signal_date']==when: self._save_signal(db,pid,snapshot)
            records=self.store.records(db,'trades',pid)
            trades=active_trades(records)
            cash_flow_records=self.store.records(db,'cash_flows',pid)
            cash_flows=active_cash_flows(cash_flow_records)
            # Include recorded dates even when the price feed lags, with stale-price labels.
            asof=max(asof,max((r['date'] for r in trades+cash_flows),default=asof))
            signals=self.store.records(db,'signals',pid)
            signals.sort(key=lambda s:s['signal_date'])
            for signal in signals:
                if not signal['payload'].get('execution_date'):
                    at=bisect_right(market['calendar'],signal['signal_date'])
                    if at<len(market['calendar']): signal['payload']['execution_date']=market['calendar'][at]
            journals=self.store.records(db,'rebalances',pid)
            events=self.store.records(db,'events',pid)
            is_paper=p['mode']=='PAPER'
            automatic=is_paper and p['strategy'].get('paper_engine')=='automatic'
            nav,quotes,model=build_nav(p,trades,signals,market,asof,include_details=True,cash_flows=cash_flows)
            book=ledger(p['capital'],trades,asof,[] if automatic else cash_flows)
            total,positions=mark(book,quotes,asof)
            completed=set()
            if automatic:
                completed={j['payload'].get('signal_id') for j in journals if j['payload'].get('kind')=='PAPER' and j['payload'].get('status')=='COMPLETED'}
                paper_signals=self._paper_signals(signals,journals)
                paper_nav,_,paper=build_nav(p,[],paper_signals,market,asof,include_details=True,cash_flows=cash_flows)
                paper_by_date={r['date']:r['model_nav'] for r in paper_nav}
                total=paper_nav[-1]['model_nav'];positions=paper['positions']
                book=dict(cash=paper['cash'],fees=sum(j['cost'] for j in paper['journals']),realized_pl=None,
                          contributions=sum(f['amount'] for f in cash_flows if f['kind']=='CONTRIBUTION'),
                          withdrawals=sum(f['amount'] for f in cash_flows if f['kind']=='WITHDRAWAL'))
            for row in nav:
                row['paper_nav']=paper_by_date.get(row['date']) if automatic else row['actual_nav'] if is_paper else None
                if is_paper: row['actual_nav']=None
                row['source']='PAPER' if is_paper else 'ACTUAL'
            latest=signals[-1] if signals else None
            target=latest['payload'] if latest else {'weights':{},'ranks':{},'names':{}}
            actual={r['ticker']:r['actual_weight'] for r in positions}
            by_ticker={r['ticker']:r for r in positions}
            reference_quotes=Quotes(market['adjusted'],'adj_close') if automatic else quotes
            for t in {t for signal in signals for t in signal['payload']['weights']}:
                if t not in by_ticker:
                    price,price_date=reference_quotes.get(t,asof)
                    by_ticker[t]=dict(ticker=t,quantity=0,average_cost=0,basis=0,market_value=0,actual_weight=0,pl=0,current_price=price,price_date=price_date,valuation_source='raw close' if price else 'unavailable')
            for t,r in by_ticker.items():
                r.update(name=target['names'].get(t,t),signal_rank=target['ranks'].get(t),target_weight=target['weights'].get(t,0))
            rebalance_rows,comparison=recommendations(positions,target['weights'],target.get('names',{}),total,
                                                       target.get('ranks',{}),target.get('scores',{}))
            turnover=0.
            prior=[]
            for trade in trades:
                valuation_flows=[f for f in cash_flows if f['date']<trade['date'] or f['date']==trade['date'] and f['kind']=='CONTRIBUTION']
                _,before_positions=mark(ledger(p['capital'],prior,trade['date'],valuation_flows),quotes,trade['date'])
                prior.append(trade)
                if trade['side'] in {'BUY','SELL'}:
                    _,after_positions=mark(ledger(p['capital'],prior,trade['date'],valuation_flows),quotes,trade['date'])
                    turnover+=compare_weights({r['ticker']:r['actual_weight'] for r in before_positions},
                                              {r['ticker']:r['actual_weight'] for r in after_positions},
                                              initial=not before_positions)['one_way_turnover']
            flows_by_date={}
            for flow in cash_flows: flows_by_date[flow['date']]=flows_by_date.get(flow['date'],0)+flow_amount(flow)
            flow_series=[0.]+[flows_by_date.get(r['date'],0) for r in nav]
            stats=performance([p['capital']]+[r['paper_nav'] if is_paper else r['actual_nav'] for r in nav],flow_series)
            model_return=performance([p['capital']]+[r['model_nav'] for r in nav],flow_series)['cumulative_return']
            recurring_journals=[j for j in journals if not j['payload'].get('initial_funding')]
            initial_journals=[j for j in journals if j['payload'].get('initial_funding')]
            journal_turnover_rows=[dict(one_way_turnover=j['payload'].get('actual_turnover',0),initial_funding=False,
                                        retained=j['payload'].get('retained'),bought=j['payload'].get('bought'),
                                        retention_rate=(j['payload'].get('retained',0)/max(len(j['payload'].get('holdings_before',{})),1)))
                                   for j in recurring_journals]
            journal_summary=summarize_turnover(journal_turnover_rows,len(nav))
            stats.update(current_nav=total,cash=book['cash'],cash_pct=book['cash']/total if total else 0,
                          invested_pct=1-book['cash']/total if total else 0,total_fees=book['fees'],realized_pl=book['realized_pl'],
                         external_contributions=book['contributions'],external_withdrawals=book['withdrawals'],
                         investment_profit_loss=total-p['capital']-book['contributions']+book['withdrawals'],
                         total_turnover=turnover,annualized_turnover=turnover*252/max(len(nav)-1,1),
                         initial_funding_turnover=sum(j['payload'].get('actual_turnover',0) for j in initial_journals),
                         recurring_turnover=sum(j['payload'].get('actual_turnover',0) for j in recurring_journals),
                         average_rebalance_turnover=sum(j['payload']['actual_turnover'] for j in recurring_journals)/len(recurring_journals) if recurring_journals else None,
                         median_turnover=journal_summary['median_turnover'],maximum_rebalance_turnover=journal_summary['maximum_rebalance_turnover'],
                         average_holdings_retained=journal_summary['average_holdings_retained'],average_holdings_replaced=journal_summary['average_holdings_replaced'],
                         holdings_retention_rate=journal_summary['holdings_retention_rate'],
                         number_of_rebalances=len(recurring_journals),days_live=(date.fromisoformat(asof)-date.fromisoformat(p['start_date'])).days,
                         model_return=model_return,implementation_gap=stats['cumulative_return']-model_return)
            if automatic:
                summary=summarize_turnover(paper['journals'],len(nav))
                stats.update(summary,total_fees=book['fees'],initial_funding_turnover=summary['initial_turnover'],
                             recurring_turnover=summary['total_turnover'],average_rebalance_turnover=summary['average_turnover'],
                             number_of_rebalances=summary['recurring_rebalances'])
            inherited_nav=[r for r in p['strategy'].get('inherited_nav',[]) if r['date']<p['start_date']]
            display_nav=inherited_nav+nav
            inherited_stats=p['strategy'].get('inherited_stats')
            if inherited_stats:
                inherited_flows=p['strategy'].get('inherited_cash_flows',[])
                all_flow_by_date={}
                for flow in inherited_flows+cash_flows: all_flow_by_date[flow['date']]=all_flow_by_date.get(flow['date'],0)+flow_amount(flow)
                continuity_capital=float(p['strategy']['continuity_starting_capital'])
                continuity_values=[continuity_capital]+[r['actual_nav'] for r in display_nav]
                continuity_flow_series=[0.]+[all_flow_by_date.get(r['date'],0) for r in display_nav]
                continuity=performance(continuity_values,continuity_flow_series)
                inherited_contributions=float(inherited_stats.get('external_contributions',0));inherited_withdrawals=float(inherited_stats.get('external_withdrawals',0))
                inherited_recurring=float(inherited_stats.get('recurring_turnover',0));inherited_count=int(inherited_stats.get('number_of_rebalances',0))
                current_count=len(recurring_journals);combined_count=inherited_count+current_count
                stats.update(continuity,external_contributions=inherited_contributions+book['contributions'],
                             external_withdrawals=inherited_withdrawals+book['withdrawals'],
                             investment_profit_loss=total-continuity_capital-inherited_contributions-book['contributions']+inherited_withdrawals+book['withdrawals'],
                             total_turnover=float(inherited_stats.get('total_turnover',0))+turnover,
                             initial_funding_turnover=float(inherited_stats.get('initial_funding_turnover',0)),
                             recurring_turnover=inherited_recurring+sum(j['payload'].get('actual_turnover',0) for j in recurring_journals),
                             average_rebalance_turnover=((inherited_recurring+sum(j['payload'].get('actual_turnover',0) for j in recurring_journals))/combined_count if combined_count else None),
                             number_of_rebalances=combined_count,
                             days_live=(date.fromisoformat(asof)-date.fromisoformat(p['strategy']['continuity_start_date'])).days)
                model_return=performance([continuity_capital]+[r['model_nav'] for r in display_nav],continuity_flow_series)['cumulative_return']
                stats.update(model_return=model_return,implementation_gap=stats['cumulative_return']-model_return)
            position_history=[{**r,'source':'PAPER' if automatic else 'MODEL'} for r in (paper['timeline'] if automatic else model['timeline'])]
            if p['strategy'].get('inherited_position_history'):
                position_history=p['strategy']['inherited_position_history']+position_history
            prior=[]
            for t in trades:
                prior.append(t)
                timeline_flows=[f for f in cash_flows if f['date']<t['date'] or f['date']==t['date'] and f['kind']=='CONTRIBUTION']
                _,held=mark(ledger(p['capital'],prior,t['date'],timeline_flows),quotes,t['date'])
                for r in held:
                    position_history.append(dict(date=t['date'],ticker=r['ticker'],name=r['ticker'],action=t['side'] if r['ticker']==t['ticker'] else 'HOLD',target_weight=None,
                                                 weight=r['actual_weight'],quantity=r['quantity'],price=r['current_price'],position_value=r['market_value'],source='PAPER' if is_paper else 'ACTUAL'))
            reconciliation=reconcile(nav,p['strategy'].get('reconstruction',{}).get('expected_nav',{}),p['capital'])
            target_integrity=dict(p['strategy'].get('reconstruction',{}).get('integrity') or {})
            stored_source=p['strategy'].get('reconstruction',{}).get('signals',[])
            if stored_source and signals:
                target_integrity['portfolio_vs_source']=compare_strategy_targets(stored_source[0],signals[0]['payload'])
            schedule=next_schedule(p['strategy'].get('signal_anchor',p['start_date']),market['calendar'],p['strategy']['rebalance_days'],asof) if p['status'] in {'ACTIVE','PAPER'} and (not p['strategy'].get('historical_end') or p['strategy'].get('continue_live')) and not p['archived'] else {'next_signal':None,'next_rebalance':None,'calendar_end':market['calendar'][-1] if market['calendar'] else None}
            finalized={j['payload'].get('signal_id') for j in journals if j['payload'].get('status') in FINAL_STATUSES or ('status' not in j['payload'] and j['payload'].get('signal_id'))}
            def auto_applied(i,signal):
                return bool(automatic and (i==0 or signal['payload'].get('source')=='RESEARCH' or signal['id'] in completed))
            active_signal=next((signal for i,signal in enumerate(signals) if signal['id'] not in finalized and not auto_applied(i,signal)),None)
            if p['status'] not in {'ACTIVE','PAPER'} or p['archived']: active_signal=None
            journaled_trades={trade_id for j in journals for trade_id in j['payload'].get('actual_trades',j['payload'].get('trade_ids',[])) if isinstance(trade_id,str)}
            pending_trades=[t for t in trades if t['id'] not in journaled_trades]
            state=workflow_status(active_signal,journals,pending_trades,asof,False) if active_signal else 'UPCOMING'
            display_execution=active_signal['payload'].get('execution_date') if active_signal else schedule['next_rebalance']
            sessions_until=sum(asof<d<=display_execution for d in market['calendar']) if display_execution else None
            display_signal=active_signal['signal_date'] if active_signal else schedule['next_signal']
            sessions_until_signal=sum(asof<d<=display_signal for d in market['calendar']) if display_signal else None
            applied_signals=[]
            for i,signal in enumerate(signals):
                if signal['id'] in finalized or auto_applied(i,signal):
                    if signal['payload'].get('execution_date'): applied_signals.append(signal['payload']['execution_date'])
            completed_dates=[j['payload'].get('actual_trade_date') or j['payload'].get('execution_date') for j in journals
                             if j['payload'].get('status')=='COMPLETED' and j['payload'].get('kind')!='PAPER_TO_ACTIVE_CONTINUATION']
            last_rebalance=max([d for d in applied_signals+completed_dates if d],default=p['strategy'].get('inherited_last_rebalance'))
            workflow_target=active_signal['payload'] if active_signal else None
            workflow_rows,workflow_comparison=(recommendations(list(by_ticker.values()),workflow_target['weights'],workflow_target.get('names',{}),total,
                                                                workflow_target.get('ranks',{}),workflow_target.get('scores',{}))
                                               if workflow_target else ([],None))
            workflow_sells=-sum(min(r['suggested_trade_value'],0) for r in workflow_rows)
            workflow_buys=sum(max(r['suggested_trade_value'],0) for r in workflow_rows)
            workflow_cost=total*workflow_comparison['one_way_turnover']*p['strategy']['cost_bps']/10000 if workflow_comparison else None
            workflow=dict(status=state,overdue=bool(state in {'WAITING_FOR_EXECUTION','PARTIALLY_EXECUTED'} and display_execution and display_execution<date.today().isoformat()),
                          signal_id=active_signal['id'] if active_signal else None,
                          initial_funding=bool(active_signal and signals and active_signal['id']==signals[0]['id']),
                          signal_date=display_signal,
                          execution_date=display_execution,last_signal_date=latest['signal_date'] if latest else p['strategy'].get('inherited_last_signal'),
                          last_rebalance_date=last_rebalance,trading_sessions_until_signal=sessions_until_signal,trading_sessions_until=sessions_until,
                          strategy=f"MOM{p['strategy']['lookback']} / {p['strategy']['selection']} / {p['strategy']['rebalance_days']}D",
                          current_nav=total,current_cash=book['cash'],model_turnover=workflow_comparison['one_way_turnover'] if workflow_comparison else None,
                          estimated_model_cost=workflow_cost,current_portfolio_value=total,capital_added=0,capital_withdrawn=0,investable_nav=total,
                          target_holdings_count=len(workflow_target['weights']) if workflow_target else 0,
                          target_weight_per_holding=next(iter(workflow_target['weights'].values())) if workflow_target and workflow_target['weights'] else None,
                          total_sell_value=workflow_sells,total_buy_value=workflow_buys,
                          net_cash_change=workflow_sells-workflow_buys-(workflow_cost or 0),
                          expected_post_rebalance_cash=book['cash']+workflow_sells-workflow_buys-(workflow_cost or 0),
                          recommendations=workflow_rows,
                          final_portfolio=[dict(ticker=r['ticker'],name=r['name'],rank=r['rank'],status=r['status'],final_quantity=r['final_quantity'],
                                                final_target_weight=r['target_weight'],final_target_value=r['final_target_value'],change_vs_previous=r['change_vs_previous'])
                                           for r in workflow_rows if r['target_weight']>0])
            proposal=self._proposal(dict(rebalance_workflow=workflow,signals=signals,stats=stats,positions=list(by_ticker.values()),portfolio=p,asof=asof))
            workflow.update(proposal,estimated_model_cost=proposal['estimated_trading_cost'])
            workflow['signal_status']=('READY' if active_signal else
                                       'WAITING' if display_signal and display_signal>date.today().isoformat() else
                                       'WAITING FOR DATA' if display_signal else 'CALENDAR UNAVAILABLE')
            workflow['target_status']='GENERATED' if active_signal else 'NOT YET GENERATED'
            calendar_diagnostics=dict(market.get('calendar_diagnostics') or {})
            calendar_diagnostics.update(universe_key=p['strategy']['universe'],latest_price_date=market['data_end'],
                                        signal_anchor=p['strategy'].get('signal_anchor',p['start_date']),
                                        required_last_signal=workflow['last_signal_date'],next_signal=workflow['signal_date'],
                                        next_execution=workflow['execution_date'],rebalance_interval_sessions=p['strategy']['rebalance_days'])
            if not calendar_diagnostics.get('failure_reason'):
                if p['status'] not in {'ACTIVE','PAPER'} or p['archived']:
                    calendar_diagnostics['failure_reason']='Scheduling is disabled for this portfolio status.'
                elif not market['calendar']:
                    calendar_diagnostics['failure_reason']='Trading calendar contains no usable open sessions.'
                elif not workflow['execution_date']:
                    calendar_diagnostics['failure_reason']=f"Insufficient future trading-calendar coverage: last open session {market['calendar'][-1]}; the anchored {p['strategy']['rebalance_days']}D cycle needs more sessions. Price coverage is separate."
                elif workflow['signal_date'] and workflow['signal_date']>market['data_end']:
                    calendar_diagnostics['price_status']='Future schedule is available; signal generation waits for prices through the signal date.'
                else: calendar_diagnostics['price_status']='Calendar and available price coverage are reported separately.'
            stats['current_model_turnover']=workflow['model_turnover']
            if refresh:
                snapshot_id=identity()
                db.executemany('INSERT INTO nav_history(portfolio_id,snapshot_id,date,actual_nav,model_nav,recorded_at,paper_nav,mode) VALUES(?,?,?,?,?,?,?,?)',
                               [(pid,snapshot_id,r['date'],r['actual_nav'] if r['actual_nav'] is not None else r['paper_nav'],r['model_nav'],stamp(),r['paper_nav'],p['mode']) for r in nav])
                self.store.event(db,pid,'NAV Snapshot',{'snapshot_id':snapshot_id,'timestamp':stamp(),'asof':asof,'data_end':market['data_end'],
                                                       'status':p['status'],'holdings':list(by_ticker.values()),'cash':book['cash'],'nav':total,
                                                       'profit_loss':stats['investment_profit_loss'],'turnover':{'current_model':workflow['model_turnover'],
                                                       'initial':stats['initial_funding_turnover'],'recurring':stats['recurring_turnover'],'cumulative':stats['total_turnover']},
                                                       'last_signal':workflow['last_signal_date'],'last_rebalance':workflow['last_rebalance_date'],'stats':stats})
                events=self.store.records(db,'events',pid)
            return dict(portfolio=p,asof=asof,data_end=market['data_end'],is_paper=is_paper,automatic_paper=automatic,stats=stats,positions=list(by_ticker.values()),
                        position_history=position_history,model_journals=model['journals'],reconciliation=reconciliation,
                        target_integrity=target_integrity or None,
                        recommendations=rebalance_rows,comparison=comparison,rebalance_workflow=workflow,signals=signals,latest_signal_id=latest['id'] if latest else None,
                        trades=trades,trade_versions=records,cash_flows=cash_flows,journals=journals,events=events,nav=display_nav,
                        schedule=schedule,calendar_diagnostics=calendar_diagnostics,
                         notes=['Actual ledger uses raw closes; dividends/splits require explicit records. Fees are actual recorded fees.',
                               'Model uses adjusted-close returns and the saved targets; late-generated targets are labeled reconstructed.',
                               'NAV history is a recalculated view until Refresh & Save Snapshot is used; prior snapshots and corrections remain auditable.',
                                'Annualization uses 252 observations. Short records and partial periods are not mature track records.'])

    def preview_rebalance(self,pid,params):
        detail=self.detail(pid)
        if detail['portfolio']['archived'] or detail['portfolio']['status']=='CLOSED': raise ValueError('Closed and archived portfolios are read-only.')
        if not detail['rebalance_workflow'].get('signal_id'): raise ValueError('No rebalance target is currently available.')
        return self._proposal(detail,params)

    @staticmethod
    def _paper_signals(signals,journals):
        applied={j['payload'].get('signal_id'):j['payload'] for j in journals
                 if j['payload'].get('kind')=='PAPER' and j['payload'].get('status')=='COMPLETED'}
        result=[]
        for i,s in enumerate(signals):
            if i==0 or s['payload'].get('source')=='RESEARCH' or s['id'] in applied:
                payload=dict(s['payload'])
                if s['id'] in applied:
                    payload['execution_date']=applied[s['id']].get('actual_trade_date',payload['execution_date'])
                result.append({**s,'payload':payload})
        return result

    def apply_paper_rebalance(self,pid,params):
        detail=self.detail(pid)
        workflow=detail['rebalance_workflow'];signal_id=params.get('signal_id')
        if not detail['automatic_paper']: raise ValueError('Apply Paper Rebalance is only available for automatic PAPER portfolios.')
        if signal_id!=workflow['signal_id']: raise ValueError('Refresh and review the current model target before applying it.')
        if workflow['status'] not in {'WAITING_FOR_EXECUTION'}: raise ValueError('The paper rebalance is not ready for execution.')
        p=detail['portfolio'];market=self.market_provider(p['strategy']);asof=detail['asof']
        with self.store.transaction() as db:
            signals=self.store.records(db,'signals',pid);signals.sort(key=lambda s:s['signal_date'])
            journals=self.store.records(db,'rebalances',pid)
            if any(j['payload'].get('signal_id')==signal_id and j['payload'].get('status') in FINAL_STATUSES for j in journals):
                raise ValueError('This paper rebalance is already finalized.')
        target=next(s for s in signals if s['id']==signal_id)
        if abs(sum(target['payload']['weights'].values())-1)>1e-9: raise ValueError('Model target weights do not sum to 100%.')
        proposal=self._proposal(detail,params)
        flow=proposal.get('capital_flow')
        completed={j['payload'].get('signal_id') for j in journals if j['payload'].get('kind')=='PAPER' and j['payload'].get('status')=='COMPLETED'}
        before_signals=self._paper_signals(signals,journals)
        applied_target={**target,'payload':{**target['payload'],'execution_date':asof}}
        after_signals=sorted(before_signals+[applied_target],key=lambda s:s['signal_date'])
        with self.store.transaction() as db:
            cash_flows=active_cash_flows(self.store.records(db,'cash_flows',pid))
        if flow and any(f.get('signal_id')==signal_id for f in cash_flows): raise ValueError('A capital adjustment is already recorded for this rebalance.')
        candidate_flow={**flow,'signal_id':signal_id} if flow else None
        after_flows=cash_flows+([candidate_flow] if candidate_flow else [])
        _,_,before=build_nav(p,[],before_signals,market,asof,include_details=True,cash_flows=cash_flows)
        _,_,after=build_nav(p,[],after_signals,market,asof,include_details=True,cash_flows=after_flows)
        journal=next(j for j in after['journals'] if j['signal_date']==target['signal_date'])
        executions=[r for r in after['timeline'] if r['date']==asof]
        payload=dict(kind='PAPER',status='COMPLETED',signal_id=signal_id,signal_date=target['signal_date'],
                     execution_date=target['payload']['execution_date'],actual_trade_date=asof,holdings_before=journal['holdings_before'],
                     target_holdings=target['payload']['weights'],proposed_trades=proposal['recommendations'],actual_trades=executions,holdings_after=journal['holdings_after'],
                     nav_before=proposal['current_nav'],contribution=proposal['capital_added'],withdrawal=proposal['capital_withdrawn'],
                     investable_nav=proposal['investable_nav'],nav_after=journal['nav_after'],model_turnover=journal['one_way_turnover'],
                     actual_turnover=journal['one_way_turnover'],model_cost=journal['cost'],fees=journal['cost'],
                     retained=proposal['comparison']['retained'],bought=proposal['comparison']['bought'],sold=proposal['comparison']['sold'],
                     notes=str(params.get('notes',''))[:2000],timestamp=stamp())
        rid=identity()
        with self.store.transaction() as db:
            current=self.store.portfolio(db,pid)
            if current['archived'] or current['status']=='CLOSED': raise ValueError('Closed and archived portfolios are read-only.')
            if candidate_flow:
                fid=identity();candidate_flow['rebalance_id']=rid
                db.execute('INSERT INTO cash_flows VALUES(?,?,?,?,?)',(fid,pid,identity(),encode(candidate_flow),stamp()))
                self.store.event(db,pid,'Capital '+('Added' if candidate_flow['kind']=='CONTRIBUTION' else 'Withdrawn'),{'id':fid,**candidate_flow})
            db.execute('INSERT INTO rebalances VALUES(?,?,?,?)',(rid,pid,encode(payload),stamp()))
            self.store.event(db,pid,'Paper Rebalance Applied',{'id':rid,**payload})
        return {'id':rid,'status':'COMPLETED'}

    def record_actual_rebalance(self,pid,params):
        detail=self.detail(pid);workflow=detail['rebalance_workflow'];signal_id=params.get('signal_id')
        request_id=str(params.get('request_id') or identity())
        with self.store.transaction() as db:
            duplicate=next((j for j in self.store.records(db,'rebalances',pid) if j['payload'].get('request_id')==request_id),None)
            if duplicate: return {'id':duplicate['id'],'status':duplicate['payload']['status'],'duplicate':True}
        if detail['portfolio']['mode']!='ACTIVE': raise ValueError('Actual rebalance entry is available only for ACTIVE portfolios.')
        if signal_id!=workflow['signal_id'] or workflow['status'] not in {'WAITING_FOR_EXECUTION','PARTIALLY_EXECUTED'}:
            raise ValueError('Refresh and review the current executable target before recording trades.')
        target=next(s for s in detail['signals'] if s['id']==signal_id)
        if abs(sum(target['payload']['weights'].values())-1)>1e-9: raise ValueError('Model target weights do not sum to 100%.')
        proposal=self._proposal(detail,params);flow=proposal.get('capital_flow')
        entries=params.get('trades') or []
        allowed={r['ticker']:r for r in proposal['recommendations'] if r['trade_side']}
        if not entries and not flow and not params.get('mark_completed'): raise ValueError('Enter executed fills, a capital adjustment, or explicitly mark the rebalance completed.')
        payloads=[]
        for item in entries:
            ticker=str(item.get('ticker','')).strip().upper();side=str(item.get('side','')).upper()
            if ticker not in allowed or side not in {'BUY','SELL'}: raise ValueError('Each trade must match a reviewed rebalance security and BUY/SELL action.')
            if allowed[ticker]['trade_side']!=side:
                raise ValueError(f'{ticker} execution side does not match the model recommendation.')
            trade=dict(ticker=ticker,side=side,date=day(item['date']),price=finite(item['price'],True),
                       quantity=finite(item['quantity'],True),fee=finite(item.get('fee',0)),notes=str(item.get('notes',''))[:2000])
            if not detail['portfolio']['start_date']<=trade['date']<=date.today().isoformat(): raise ValueError('Actual trade dates must be within the tracking period.')
            if target['payload'].get('execution_date') and trade['date']<target['payload']['execution_date']:
                raise ValueError('Actual trades cannot predate the model execution date.')
            payloads.append(trade)
        payloads.sort(key=lambda t:(t['date'],0 if t['side']=='SELL' else 1))
        p=detail['portfolio'];market=self.market_provider(p['strategy']);quotes=Quotes(market['raw'])
        with self.store.transaction() as db:
            current=self.store.portfolio(db,pid)
            if current['archived'] or current['status']=='CLOSED': raise ValueError('Closed and archived portfolios are read-only.')
            records=self.store.records(db,'trades',pid);existing=active_trades(records);candidates=[]
            cash_flow_records=self.store.records(db,'cash_flows',pid);existing_flows=active_cash_flows(cash_flow_records)
            if flow and any(f.get('signal_id')==signal_id for f in existing_flows): raise ValueError('A capital adjustment is already recorded for this rebalance.')
            candidate_flow={**flow,'signal_id':signal_id} if flow else None
            for trade in payloads:
                tid=identity();recorded=stamp();trade['sequence']=recorded
                candidate={'id':tid,'supersedes':None,'payload':trade,'recorded_at':recorded};candidates.append(candidate)
            all_flows=existing_flows+([candidate_flow] if candidate_flow else [])
            ledger(p['capital'],active_trades(records+candidates),cash_flows=all_flows)
            effective_dates=[t['date'] for t in payloads]+([flow['date']] if flow else []) or [detail['asof']]
            first=min(effective_dates);last=max(effective_dates)
            nav_before,before_positions=mark(ledger(p['capital'],existing,first,existing_flows),quotes,first)
            nav_after,after_positions=mark(ledger(p['capital'],active_trades(records+candidates),last,all_flows),quotes,last)
            before_weights={r['ticker']:r['actual_weight'] for r in before_positions};after_weights={r['ticker']:r['actual_weight'] for r in after_positions}
            actual_compare=compare_weights(before_weights,after_weights,initial=not before_positions)
            tracking=max((abs(target['payload']['weights'].get(t,0)-after_weights.get(t,0)) for t in target['payload']['weights'].keys()|after_weights.keys()),default=0)
            status='COMPLETED' if params.get('mark_completed') else 'PARTIALLY_EXECUTED'
            rebalance=dict(kind='INITIAL_FUNDING' if workflow.get('initial_funding') else 'ACTUAL',status=status,initial_funding=workflow.get('initial_funding',False),request_id=request_id,signal_id=signal_id,signal_date=target['signal_date'],
                           execution_date=target['payload']['execution_date'],actual_trade_date=last,
                           holdings_before=before_weights,target_holdings=target['payload']['weights'],proposed_trades=proposal['recommendations'],
                           actual_trades=[c['id'] for c in candidates],holdings_after=after_weights,
                           nav_before=nav_before,contribution=proposal['capital_added'],withdrawal=proposal['capital_withdrawn'],
                           investable_nav=proposal['investable_nav'],nav_after=nav_after,model_turnover=proposal['model_turnover'],
                           actual_turnover=actual_compare['one_way_turnover'],model_cost=proposal['estimated_trading_cost'],
                           fees=sum(t['fee'] for t in payloads),retained=proposal['comparison']['retained'],bought=proposal['comparison']['bought'],
                           sold=proposal['comparison']['sold'],tracking_difference=tracking,notes=str(params.get('notes',''))[:2000],timestamp=stamp())
            rid=identity()
            if candidate_flow:
                fid=identity();candidate_flow['rebalance_id']=rid
                db.execute('INSERT INTO cash_flows VALUES(?,?,?,?,?)',(fid,pid,request_id+':capital',encode(candidate_flow),stamp()))
                self.store.event(db,pid,'Capital '+('Added' if candidate_flow['kind']=='CONTRIBUTION' else 'Withdrawn'),{'id':fid,**candidate_flow})
            for candidate in candidates:
                trade=candidate['payload'];db.execute('INSERT INTO trades VALUES(?,?,?,?,?,?)',(candidate['id'],pid,None,identity(),encode(trade),candidate['recorded_at']))
                self.store.event(db,pid,'Trade Recorded',candidate)
            db.execute('INSERT INTO rebalances VALUES(?,?,?,?)',(rid,pid,encode(rebalance),stamp()))
            self.store.event(db,pid,'Actual Rebalance Recorded',{'id':rid,**rebalance})
        return {'id':rid,'status':status,'tracking_difference':tracking}

    def skip_rebalance(self,pid,params):
        detail=self.detail(pid);workflow=detail['rebalance_workflow'];signal_id=params.get('signal_id')
        if signal_id!=workflow['signal_id'] or workflow['status'] not in {'WAITING_FOR_EXECUTION','PARTIALLY_EXECUTED'}:
            raise ValueError('Only the current due rebalance can be skipped.')
        payload=dict(kind=detail['portfolio']['mode'],status='SKIPPED',signal_id=signal_id,
                     signal_date=workflow['signal_date'],execution_date=workflow['execution_date'],
                     holdings_before={r['ticker']:r['actual_weight'] for r in detail['positions']},
                     target_holdings=next(s for s in detail['signals'] if s['id']==signal_id)['payload']['weights'],
                     actual_trades=[],holdings_after={r['ticker']:r['actual_weight'] for r in detail['positions']},
                     nav_before=detail['stats']['current_nav'],nav_after=detail['stats']['current_nav'],
                     model_turnover=workflow['model_turnover'],actual_turnover=0,model_cost=workflow['estimated_model_cost'],
                     fees=0,notes=str(params.get('notes',''))[:2000])
        rid=identity()
        with self.store.transaction() as db:
            p=self.store.portfolio(db,pid)
            if p['archived'] or p['status']=='CLOSED': raise ValueError('Closed and archived portfolios are read-only.')
            db.execute('INSERT INTO rebalances VALUES(?,?,?,?)',(rid,pid,encode(payload),stamp()))
            self.store.event(db,pid,'Rebalance Skipped',{'id':rid,**payload})
        return {'id':rid,'status':'SKIPPED'}

    def journal(self,pid,params):
        detail=self.detail(pid)
        when=day(params['date'])
        p=detail['portfolio']
        if not p['start_date']<=when<=date.today().isoformat(): raise ValueError('Invalid journal date.')
        with self.store.transaction() as db:
            current=self.store.portfolio(db,pid)
            if current['status']=='CLOSED' or current['archived']: raise ValueError('Closed and archived portfolios are read-only.')
            signals=self.store.records(db,'signals',pid)
            signal=next((s for s in signals if s['id']==params.get('signal_id')),None)
            if not signal or signal['signal_date']>when: raise ValueError('Select an existing signal dated on/before the rebalance.')
            journals=self.store.records(db,'rebalances',pid)
            used={t for j in journals for t in j['payload'].get('trade_ids',j['payload'].get('actual_trades',[])) if isinstance(t,str)}
            # Corrected versions retain their original journal association.
            versions=self.store.records(db,'trades',pid)
            changed=True
            while changed:
                previous=len(used)
                used|={r['id'] for r in versions if r.get('supersedes') in used}
                changed=len(used)!=previous
            selected=[t for t in active_trades(versions) if t['id'] not in used and signal['signal_date']<=t['date']<=when and t['side'] in {'BUY','SELL'}]
            if not selected: raise ValueError('Record actual trades before recording an actual rebalance.')
            quotes=Quotes(self.market_provider(p['strategy'])['raw'])
            all_trades=active_trades(versions)
            cash_flows=active_cash_flows(self.store.records(db,'cash_flows',pid))
            at=all_trades.index(selected[0]); before_book=ledger(p['capital'],all_trades[:at],cash_flows=cash_flows)
            value,positions=mark(before_book,quotes,selected[0]['date'])
            comparison=compare_weights({r['ticker']:r['actual_weight'] for r in positions},signal['payload']['weights'])
            last_index=max(all_trades.index(t) for t in selected)
            _,after_positions=mark(ledger(p['capital'],all_trades[:last_index+1],cash_flows=cash_flows),quotes,selected[-1]['date'])
            actual_comparison=compare_weights({r['ticker']:r['actual_weight'] for r in positions},
                                              {r['ticker']:r['actual_weight'] for r in after_positions},initial=not positions)
            payload=dict(date=when,signal_id=signal['id'],model_turnover=comparison['one_way_turnover'],retained=comparison['retained'],
                         sold=len({t['ticker'] for t in selected if t['side']=='SELL'}),bought=len({t['ticker'] for t in selected if t['side']=='BUY'}),
                         actual_turnover=actual_comparison['one_way_turnover'],
                         fees_paid=sum(t['fee'] for t in selected),trade_ids=[t['id'] for t in selected],notes=str(params.get('notes',''))[:2000])
            rid=identity()
            db.execute('INSERT INTO rebalances VALUES(?,?,?,?)',(rid,pid,encode(payload),stamp()))
            self.store.event(db,pid,'Rebalance Recorded',{'id':rid,**payload})
            return {'id':rid,**payload}
