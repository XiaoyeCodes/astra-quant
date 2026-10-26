"""Exercise OctoBot's paper-only emergency action chain, then recover to idle.

This deliberately fires the action chain; it is not a market-induced drawdown.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
from app.autopilot import automation_config
from paper_strategy_cycle import request

TZ = timezone(timedelta(hours=8))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def octobot(path, body=None):
    req = urllib.request.Request('http://127.0.0.1:5001' + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'})
    with OPENER.open(req, timeout=25) as response:
        return response.read().decode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 1200
    while not (args.after.exists() and '"cycle_complete"' in args.after.read_text(encoding='utf-8')):
        if time.monotonic() > deadline:
            raise TimeoutError('Strategy retest did not finish')
        time.sleep(2)
    with (args.output / 'stop-drill.jsonl').open('a', encoding='utf-8') as log:
        def record(event, **fields):
            row = {'ts': datetime.now(TZ).isoformat(timespec='seconds'), 'event': event, **fields}
            log.write(json.dumps(row, ensure_ascii=False) + '\n'); log.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)
        state = request('state')
        deadline = time.monotonic() + 120
        while (state['mode'] == 'demo' or '初始化' in str(state['strategy'].get('blocked'))) and time.monotonic() < deadline:
            time.sleep(2)
            state = request('state')
        if state['mode'] != 'paper' or state['profile'].get('real'):
            raise RuntimeError('Only an explicitly simulated account may be tested')
        if state['profile']['id'] != 'astra_spot_grid':
            request('strategy/select', {'profile': 'astra_spot_grid'})
        deadline = time.monotonic() + 210
        while True:
            state = request('state')
            if state['mode'] == 'live':
                raise RuntimeError('Live trading detected: abort')
            if (state['profile']['id'] == 'astra_spot_grid' and state['links']['octobot']['ok']
                    and not state['strategy']['switching']):
                break
            if time.monotonic() > deadline:
                raise TimeoutError('Grid startup timeout')
            time.sleep(2)
        # Let the bridge's startup grace and the initial order creation finish.
        time.sleep(65)
        state = request('state')
        record('before_forced_paper_stop', profile=state['profile']['id'],
               orders=len(state['orders']), equity=state['equity'].get('value'))
        octobot('/automations?action=save', automation_config(immediate=True))
        octobot('/automations?action=start', {})
        deadline = time.monotonic() + 80
        try:
            while time.monotonic() < deadline:
                state = request('state')
                if state['strategy'].get('stopped') and not state['strategy']['stopping'] and not state['orders']:
                    record('stop_observed', stopped=state['strategy']['stopped'],
                           orders=len(state['orders']), positions=len(state['positions']))
                    break
                time.sleep(2)
            else:
                record('stop_timeout', strategy=state['strategy'])
        finally:
            octobot('/automations?action=save', automation_config())
            octobot('/automations?action=start', {})
            octobot('/profile?select=astra_idle&next=/api/version')
            octobot('/commands/restart')
            record('recovery_requested', profile='astra_idle')


if __name__ == '__main__':
    main()
