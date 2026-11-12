"""Single local entry point; viewing is offline and updating uses the safe weekly runner."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
from .storage import PROJECT

ACTIONS={'view':'查看最新报告','update':'更新数据与周报','health':'检查运行状态','ai':'大模型逐股复核、资金分配与买入计划','folder':'打开最新报告文件夹','replay':'重新处理已保存的AI结果（不收费）','pdf':'导出分享版 PDF','horizon':'调整预计持有时间并重新筛选（不调用API）','capital':'设置计划资金与分配风格（不调用API）','full':'一键更新并生成完整研究报告（联网＋一次AI）','lab':'数据审计与增强因子观察（离线）','validation':'前瞻跟踪与策略验证状态（离线）'}


def report_status(project=PROJECT):
    from .investment_horizon import load as load_horizon
    from .capital_allocation import load as load_capital
    path=Path(project)/'reports/sector_research/research.json'
    if not path.exists():return ['尚无完整研究报告。下一步：菜单10更新并解读，或菜单2仅更新。']
    try:
        report=json.loads(path.read_text());horizon=load_horizon(Path(project)/'configs/research_preferences.json');capital=load_capital(Path(project)/'configs/research_preferences.json')
        counts=report.get('decision_pipeline',{}).get('counts',{});picks=report.get('stock_recommendations',{})
        reviewed=sum(counts.get(k,0) for k in ('approve_conditional','defer','reject','plan_incomplete','invalid'))
        lines=['最新报告：'+str(report.get('asof','未知'))[:19].replace('T',' ')+'；数据源 '+str(report.get('data_times',{}).get('last','未知'))[:10],
               f"优先行业 {len(report.get('research_priorities',[]))} 个 → 主名单 {len(picks.get('stocks',[]))} 只 → 模型已返回意见 {reviewed} 只；外围观察 {len(picks.get('supplementary_stocks',[]))} 只。"]
        ai_error=(report.get('ai') or {}).get('error') or (report.get('last_ai_failure') or {}).get('error')
        if ai_error:
            lines.append('最近一次AI解读未完成：'+str(ai_error))
            lines.append('已有数据与报告仍可查看。稍后选择菜单4手动重试解读，无需重新跑菜单10；本程序不自动重复调用。')
        elif report.get('investment_horizon',{}).get('months')!=horizon['months'] or report.get('capital_settings')!=capital:
            lines.append('设置已变化，当前报告与设置不一致。下一步：菜单10完整更新，或菜单4按新设置解读。')
        elif picks.get('selection_policy')!='industry-first-v1':lines.append('这是旧筛选流程的报告。下一步：菜单2或10生成行业内选股报告。')
        elif not reviewed and picks.get('stocks'):lines.append('下一步：菜单4复核现有候选；如需同时更新数据，选择菜单10。')
        elif capital and report.get('capital_allocation',{}).get('status')=='待大模型分配':lines.append('资金方案待生成。下一步：菜单4按本次资金解读。')
        else:lines.append('下一步：菜单1查看报告；新一轮数据与解读可选菜单10。')
        return lines
    except (OSError,ValueError,KeyError,TypeError):return ['报告状态暂时无法读取，请选择菜单3检查运行状态。']


def dispatch(action,runner=subprocess.run,prompt=getpass.getpass,input_fn=None):
    report=PROJECT/'reports/sector_research/index.html'
    base=[sys.executable,'-B','-m']
    if action in ('lab','validation'):
        module='stock_selector.research_lab' if action=='lab' else 'stock_selector.forward_validation'
        folder='research_lab' if action=='lab' else 'research_validation'
        result=runner(base+[module],cwd=PROJECT)
        if not result.returncode:runner(['open',str(PROJECT/'reports'/folder/'index.html')],cwd=PROJECT)
        return result.returncode
    if action=='capital':
        from .capital_allocation import load,save,parse_amount
        path=PROJECT/'configs/research_preferences.json';current=load(path);ask=input_fn or input
        print('资金指本次计划投入的新资金，分配不包含已有持仓。')
        if current:print('当前资金：'+current['budget_yuan']+'元；'+current['label']+'风格。')
        value=ask('计划投入多少元？例如100000或10万；回车取消：').strip()
        if not value:return 0
        try:amount=parse_amount(value)
        except ValueError as e:print(str(e)+'；设置未修改。');return 1
        print('分配风格：1 保守（单股≤15%，现金≥40%）；2 均衡（单股≤20%，现金≥25%）；3 积极（单股≤25%，现金≥10%）。')
        options={'1':'conservative','2':'balanced','3':'active'}
        default_choice=next((key for key,value in options.items() if current and value==current['style']),'2')
        choice=ask('选择风格，回车保留'+(current['label'] if current else '均衡')+'：').strip() or default_choice
        if choice not in options:print('请输入1、2或3；设置未修改。');return 1
        selected=save(amount,options[choice],path)
        print('已保存 '+selected['budget_yuan']+'元，'+selected['label']+'风格。现在用本地资料更新报告，不调用API。')
        result=runner(base+['stock_selector.sector_research'],cwd=PROJECT)
        if result.returncode:print('资金偏好已保存；报告未完成，请先选择菜单2更新数据。')
        else:
            if report.exists():runner(['open',str(report)],cwd=PROJECT)
            print('选择菜单4，DeepSeek将读取本次资金和持有期，生成资金分配建议；调用会使用API额度。')
        return result.returncode
    if action=='horizon':
        from .investment_horizon import load,save
        path=PROJECT/'configs/research_preferences.json'
        current=load(path)
        print('当前预计持有：'+current['label']+'。常用选择：1、3、6、12个月；支持1—24个月。')
        value=(input_fn or input)('预计持有几个月？直接回车保持不变：').strip()
        if not value:return 0
        if not value.isascii() or not value.isdigit() or not 1<=int(value)<=24:
            print('请输入1至24之间的整数月份；设置未修改。');return 1
        selected=save(int(value),path)
        print('已保存：'+selected['label']+'。'+selected['focus'])
        print('现在使用本地数据重新筛选并生成报告，不联网、不调用付费API。更换期限后，旧AI意见仅作历史参考。')
        result=runner(base+['stock_selector.sector_research'],cwd=PROJECT)
        if result.returncode:
            print('期限偏好已保存，本次报告生成未完成；已有报告保留。可选择“更新数据与周报”。')
        elif report.exists():
            runner(['open',str(report)],cwd=PROJECT)
            print('重筛已完成。如需更新行情，请选择“更新数据与周报”；如需按新期限获得模型意见，请选择“大模型逐股复核与买入计划”。')
        return result.returncode
    if action=='view':
        if not report.exists():
            print('尚无报告，请先选择“更新数据与周报”。');return 1
        return runner(['open',str(report)],cwd=PROJECT).returncode
    if action=='folder':return runner(['open',str(PROJECT/'reports/sector_research')],cwd=PROJECT).returncode
    if action in ('update','health'):
        module='stock_selector.weekly' if action=='update' else 'stock_selector.health'
        result=runner(base+[module],cwd=PROJECT)
        if result.returncode:
            print('操作未完成，已有报告仍保留；可选择“检查运行状态”。');return result.returncode
        if action=='update':runner(['open',str(report)],cwd=PROJECT)
        return 0
    if action=='pdf':
        result=runner(base+['stock_selector.pdf_export'],cwd=PROJECT)
        if not result.returncode:runner(['open',str(PROJECT/'reports/sector_research/行业研究周报.pdf')],cwd=PROJECT)
        return result.returncode
    if action=='replay':
        result=runner(base+['stock_selector.sector_research','--replay-ai'],cwd=PROJECT)
        if not result.returncode and report.exists():runner(['open',str(report)],cwd=PROJECT)
        return result.returncode
    if action in ('ai','full'):
        if action=='ai' and not report.exists():print('请先更新或生成报告，再进行解读。');return 1
        key=(os.environ.get('DEEPSEEK_API_KEY') or prompt('请输入DeepSeek API密钥（输入不显示、不保存）：')).strip()
        if not key or not key.isascii() or any(ord(c)<33 or ord(c)>126 for c in key):
            print('密钥格式不正确，请仅复制密钥本身，勿包含中文、空格或说明文字。');return 1
        env=dict(os.environ,DEEPSEEK_API_KEY=key)
        print('将使用一次DeepSeek调用生成完整解读与资金建议，不自动重试。')
        command=base+(['stock_selector.weekly'] if action=='full' else ['stock_selector.sector_research','--ai'])
        result=runner(command,cwd=PROJECT,env=env)
        if result.returncode:print('本次未完成。请查看菜单3的状态检查；旧报告保留在周归档中。')
        if not result.returncode:runner(['open',str(report)],cwd=PROJECT)
        return result.returncode
    raise ValueError('未知操作')


def main():
    p=argparse.ArgumentParser(description='股票研究助手统一入口')
    p.add_argument('--action',choices=list(ACTIONS))
    a=p.parse_args()
    if a.action:raise SystemExit(dispatch(a.action))
    menu=list(ACTIONS)
    while True:
        from . import __version__
        print('\n股票研究助手 '+__version__+'\n──────────────')
        from .investment_horizon import load
        print('  当前预计持有：'+load()['label']+'（菜单8可调整）')
        from .capital_allocation import load as load_capital
        capital=load_capital()
        print('  计划资金：'+(capital['budget_yuan']+'元，'+capital['label'] if capital else '未设置')+'（菜单9可调整）')
        for line in report_status(PROJECT):print('  '+line)
        print('\n常用操作')
        for i in (1,10,2,4):print(f'  {i}. {ACTIONS[menu[i-1]]}')
        print('\n研究设置')
        for i in (8,9):print(f'  {i}. {ACTIONS[menu[i-1]]}')
        print('\n文件与维护')
        for i in (5,7,3,6):print(f'  {i}. {ACTIONS[menu[i-1]]}')
        print('\n研究验证（不调用AI）')
        for i in (11,12):print(f'  {i}. {ACTIONS[menu[i-1]]}')
        print('  0. 退出\n\n直接回车：查看最新报告（不联网）')
        try:choice=input('请选择：').strip() or '1'
        except (EOFError,KeyboardInterrupt):print('\n已退出。');return
        if choice=='0':return
        if not choice.isdigit() or not 1<=int(choice)<=len(menu):print('请输入菜单中的数字。');continue
        try:dispatch(menu[int(choice)-1])
        except (OSError,ValueError) as e:print('操作未完成：'+type(e).__name__+'。请使用“检查运行状态”查看详情。')
        except KeyboardInterrupt:print('\n本次操作已取消。')

if __name__=='__main__':main()
