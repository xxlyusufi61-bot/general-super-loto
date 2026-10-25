"""Send short, number-safe GitHub Actions reports to Telegram."""
import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

LABELS = {
    'abc_transition_to_c': 'A->B->C candidate',
    'a_to_t_pair_counts': 'A->T candidate',
    'mod5_state_to_t': 'MOD5->T candidate',
    'frequency_top6': 'Frequency top-6 baseline',
    'frequency_bottom6': 'Frequency bottom-6 baseline',
    'adjacent_draw_overlap': 'Adjacent-draw baseline',
}


def read_report(path):
    if not path:
        return None
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            value = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def metric_lines(value):
    output = []
    if not isinstance(value, dict):
        return output
    for name, stats in value.items():
        if not isinstance(stats, dict):
            continue
        hits = stats.get('mean_hits_out_of_6', stats.get('observed_mean_hits_out_of_6'))
        count = stats.get('evaluated_draws')
        if isinstance(hits, (int, float)) and not isinstance(hits, bool):
            suffix = f'{hits:.3f}/6 mean hits'
            if isinstance(count, int):
                suffix += f', {count} draws'
            output.append(f"- {LABELS.get(name, name)}: {suffix}")
    return output


def format_message(title, status, run_url, branch, kind='status', report=None):
    ok = status.lower() == 'success'
    lines = [f"{'PASS' if ok else 'FAIL'} — GENERAL {title}", f"Result: {'SUCCESS' if ok else 'FAILURE'}", f'Branch: {branch or "unknown"}']
    if not ok:
        lines.append('Open the Actions run below for failure details.')
    elif kind == 'candidates' and report:
        lines.append('Forward walk-forward experimental results:')
        lines.extend(metric_lines(report.get('forward_walk_forward', {}).get('baselines')))
        lines.append('Experimental only; historical overlap is not proof of prediction.')
        lines.append('Reverse-time results are retrospective, not predictive.')
    elif kind == 'walkforward' and report:
        isolated = report.get('current_target_excluded_from_scoring') is True and report.get('current_target_numbers_used_for_scoring') is False and report.get('current_target_numbers_exposed') is False
        lines.append('Target isolation: ' + ('PASS' if isolated else 'NOT CONFIRMED'))
        count = report.get('pre_target_history_records')
        if isinstance(count, int):
            lines.append(f'Pre-target history records: {count}')
        forward = report.get('forward_walk_forward', {})
        lines.append('Forward baselines:')
        lines.extend(metric_lines(forward.get('baselines') if isinstance(forward, dict) else None))
        cv = report.get('time_ordered_cross_validation', {})
        lines.append('Chronological CV baselines:')
        lines.extend(metric_lines(cv.get('baselines') if isinstance(cv, dict) else None))
        lines.append('A->B->C, A->T and MOD5->T are not implemented in this baseline engine.')
        lines.append('Reverse-time results are retrospective, not predictive.')
    if run_url:
        lines.extend(['', f'GitHub Actions: {run_url}'])
    return '\n'.join(lines)[:3800]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--title', required=True)
    p.add_argument('--status', required=True)
    p.add_argument('--run-url', default='')
    p.add_argument('--branch', default='')
    p.add_argument('--kind', choices=['status', 'candidates', 'walkforward'], default='status')
    p.add_argument('--report-log')
    a = p.parse_args()
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id or not chat_id.lstrip('-').isdigit():
        raise SystemExit('Telegram report failed: required secrets missing or invalid.')
    report = read_report(a.report_log)
    if a.status.lower() == 'success' and a.kind in ('candidates', 'walkforward') and report is None:
        raise SystemExit('Telegram report failed: aggregate report could not be parsed.')
    message = format_message(a.title, a.status, a.run_url, a.branch, a.kind, report)
    data = urllib.parse.urlencode({'chat_id': chat_id, 'text': message, 'disable_web_page_preview': 'true'}).encode()
    request = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage', data=data, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode('utf-8'))
    except Exception:
        raise SystemExit('Telegram report failed: delivery request did not complete.') from None
    if not isinstance(result, dict) or result.get('ok') is not True or not isinstance(result.get('result'), dict) or not isinstance(result['result'].get('message_id'), int):
        raise SystemExit('Telegram report failed: Telegram did not confirm delivery.')
    print('TELEGRAM REPORT DELIVERY: PASS')


if __name__ == '__main__':
    main()
