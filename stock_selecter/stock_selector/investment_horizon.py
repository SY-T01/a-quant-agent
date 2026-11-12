"""Persist the user's research horizon; policies are explicit, uncalibrated hypotheses."""
import json
from .storage import PROJECT,atomic_json

SETTINGS=PROJECT/'configs/research_preferences.json'


def profile(months=6):
    if isinstance(months,bool) or not isinstance(months,int) or not 1<=months<=24:
        raise ValueError('预计持有时间请输入1至24之间的整月数')
    if months<=2:
        name='偏短期';weights=dict(cash_quality=25,growth=15,valuation=20,sector_flow=40)
        gates=dict(min_cash_cover=.8,max_pe=35,max_pb=6)
        focus='更重视近期资金延续、成交活跃和事件兑现；每周复核，出现资金反转或事件落空时重审。'
    elif months<=6:
        name='中期';weights=dict(cash_quality=35,growth=25,valuation=20,sector_flow=20)
        gates=dict(min_cash_cover=.8,max_pe=45,max_pb=6)
        focus='兼顾资金、估值和未来一至两次财报的经营兑现；每周观察，财报披露后重点复核。'
    elif months<=12:
        name='中长期';weights=dict(cash_quality=40,growth=25,valuation=25,sector_flow=10)
        gates=dict(min_cash_cover=1.,max_pe=35,max_pb=5)
        focus='更重视现金流覆盖、估值与持续盈利；短期资金仅作择时辅助，按季复核经营逻辑。'
    else:
        name='长期';weights=dict(cash_quality=45,growth=25,valuation=25,sector_flow=5)
        gates=dict(min_cash_cover=1.2,max_pe=30,max_pb=4.5)
        focus='更重视现金流和估值纪律，检验商业竞争力、资本开支与盈利持续性；需要多年经营资料，不能由单期高增外推。'
    return dict(months=months,label=f'{months}个月',style=name,weights=weights,gates=gates,
                flow_gate='both_positive' if months<=6 else 'ten_day_positive',focus=focus,
                validation='持有期对应的权重和门槛是可解释的研究起始假设，尚未完成对应期限的样本外收益验证；不代表收益更高。')


def load(path=None):
    path=path or SETTINGS
    data=json.loads(path.read_text()) if path.exists() else {'holding_months':6}
    return profile(data.get('holding_months',6))


def save(months,path=None):
    value=profile(months)
    path=path or SETTINGS
    data=json.loads(path.read_text()) if path.exists() else {}
    data['holding_months']=months
    atomic_json(path,data)
    return value


def get(report):
    # Rebuild rather than accepting policy weights embedded in arbitrary report JSON.
    return profile(report.get('investment_horizon',{}).get('months',6))
