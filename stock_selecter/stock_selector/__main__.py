import argparse
import csv
import hashlib
import json
import random
from pathlib import Path
from datetime import date, timedelta
from dataclasses import replace
from .core import Config, load, features, select, backtest, VERSION
from .storage import project_path


def write_csv(path, rows):
    if not rows: return
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def demo(path):
    rng=random.Random(42); rows=[]; prices={f'{i:06d}.SZ':10+i/10 for i in range(1,61)}
    d=date(2023,1,2)
    for t in range(360):
        while d.weekday()>4: d+=timedelta(days=1)
        for i,(s,prev) in enumerate(list(prices.items())):
            op=prev*(1+rng.gauss(0,.004)); cl=op*(1+rng.gauss(.0002,.012))
            amount=rng.uniform(4e7,2e8)
            rows.append(dict(date=str(d),symbol=s,industry=f'I{i%8}',open=op,close=cl,adj_factor=1,amount=amount,moneyflow=amount*rng.uniform(-.15,.15),available_at=f'{d}T20:00:00',provider='SYNTHETIC',method_version='demo-v1',listed_on='2020-01-01',is_st=0,suspended=0,limit_up=prev*1.1,limit_down=prev*.9))
            prices[s]=cl
        d+=timedelta(days=1)
    write_csv(path,rows)


def main():
    p=argparse.ArgumentParser(description='资金流选股研究工具（不连接实盘）')
    p.add_argument('command', choices=['demo','select','backtest','optimize'])
    p.add_argument('--data',default='data/demo.csv'); p.add_argument('--out',default='reports/run')
    p.add_argument('--config'); p.add_argument('--asof')
    args=p.parse_args()
    args.data=str(project_path(args.data)); args.out=str(project_path(args.out))
    if args.config: args.config=str(project_path(args.config))
    Path(args.data).parent.mkdir(parents=True,exist_ok=True)
    if args.command=='demo': demo(args.data); print(f'已生成合成演示数据：{args.data}'); return
    cfg=Config(**json.loads(Path(args.config).read_text())) if args.config else Config()
    rows=load(args.data); out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    manifest=dict(model_version=VERSION,data_sha256=hashlib.sha256(Path(args.data).read_bytes()).hexdigest(),synthetic=rows[0]['provider']=='SYNTHETIC',decision_time='23:00 Asia/Shanghai',notes=['合成数据不代表策略有效','费用为用户配置的研究假设，不代表历史法定费率','日成交额参与率为事后容量近似，非开盘可成交量','无实时数据、财报、机器学习或实盘接口'])
    if args.command=='select':
        sig=features(rows,cfg); d=args.asof or max(sig)
        if d not in sig: raise ValueError('asof不在数据交易日内')
        selected=select(sig[d],cfg)
        write_csv(out/'candidates.csv',selected)
        manifest.update(asof=d,candidates=len(selected),status='ok' if selected else 'no_recommendation')
        # Remove stale output when data quality leaves no eligible candidates.
        if not selected: (out/'candidates.csv').unlink(missing_ok=True)
    elif args.command=='backtest':
        result=backtest(rows,features(rows,cfg),cfg)
        write_csv(out/'nav.csv',result.pop('nav')); write_csv(out/'orders.csv',result.pop('orders'))
        manifest.update(result)
    else:
        days=sorted({r['date'] for r in rows})
        if len(days)<150: raise ValueError('滚动研究至少需要150个交易日')
        # Fixed split, warmup before training, no test-based model selection.
        train_start=days[21]; val_start=days[int(len(days)*.6)]; test_start=days[int(len(days)*.8)]
        experiments=[]
        for w in (0.,.25,.5,.75):
            trial=replace(cfg,flow_weight=w); sig=features(rows,trial)
            train=backtest(rows,sig,trial,train_start,days[int(len(days)*.6)-1])['metrics']
            val=backtest(rows,sig,trial,val_start,days[int(len(days)*.8)-1])['metrics']
            experiments.append(dict(flow_weight=w,train=train,validation=val))
        best=max(experiments,key=lambda e:e['validation']['sharpe'])
        chosen=replace(cfg,flow_weight=best['flow_weight'])
        test=backtest(rows,features(rows,chosen),chosen,test_start)
        baseline=replace(cfg,flow_weight=0)
        comparison=backtest(rows,features(rows,baseline),baseline,test_start)
        write_csv(out/'test_nav.csv',test.pop('nav')); write_csv(out/'test_orders.csv',test.pop('orders'))
        manifest.update(split=dict(train_start=train_start,validation_start=val_start,test_start=test_start),experiments=experiments,selected_flow_weight=best['flow_weight'],test=test,price_baseline=comparison['metrics'],test_incremental_return=test['metrics']['total_return']-comparison['metrics']['total_return'],interpretation='一次固定时间切分，只供研究；需多年滚动样本外验证，重复查看测试集后不可再称封存测试')
    (out/'report.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'输出已保存：{out.resolve()}')

if __name__=='__main__': main()
