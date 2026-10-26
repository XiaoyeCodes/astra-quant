"""Exercise existing strategy presets on an explicitly simulated local account."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import urllib.request

TZ = timezone(timedelta(hours=8))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request('http://127.0.0.1:8080/api/' + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    with OPENER.open(req, timeout=25) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profiles', help='Comma separated profile:hold_seconds phases')
    parser.add_argument('--after', type=Path, help='Wait for cycle_complete in another phase log')
    parser.add_argument('--phase-log', default='phases.jsonl')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    phases = [('astra_index', 60),
              ('astra_trailing_grid', 35), ('astra_staggered', 35),
              ('astra_smart_dca', 35), ('astra_trend', 35), ('astra_dip', 35),
              ('astra_signal', 35), ('astra_ai_signal', 60)]
    if args.profiles:
        phases = [(part.rsplit(':', 1)[0], int(part.rsplit(':', 1)[1]))
                  for part in args.profiles.split(',')]
    if args.after:
        deadline = time.monotonic() + 1200
        while not (args.after.exists() and '"cycle_complete"' in args.after.read_text(encoding='utf-8')):
            if time.monotonic() >= deadline:
                raise TimeoutError('Previous cycle did not finish')
            time.sleep(2)
    with (args.output / args.phase_log).open('a', encoding='utf-8') as stream:
        def record(event, **fields):
            row = {'ts': datetime.now(TZ).isoformat(timespec='seconds'), 'event': event, **fields}
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)

        for profile, hold in phases:
            state = request('state')
            if state['mode'] != 'paper' or state.get('profile', {}).get('real'):
                raise RuntimeError('Only an explicitly simulated account may be tested')
            record('select', profile=profile)
            if state['profile']['id'] != profile:
                request('strategy/select', {'profile': profile})
            deadline = time.monotonic() + 210
            while True:
                state = request('state')
                if state['mode'] == 'live':
                    raise RuntimeError('Live trading detected: abort')
                if (state.get('profile', {}).get('id') == profile
                        and state['links']['octobot']['ok'] and not state['strategy']['switching']
                        and not state['strategy'].get('blocked')):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Strategy startup timeout: ' + profile)
                time.sleep(2)
            record('ready', profile=profile, holdSeconds=hold)
            time.sleep(hold)
            state = request('state')
            (args.output / (profile + '.json')).write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            record('observed', profile=state['profile']['id'], orders=len(state['orders']),
                   trades=len(state['trades']), equity=state['equity'].get('value'))
        record('cycle_complete')


if __name__ == '__main__':
    main()
