"""Apply a guarded, idempotent compatibility patch to the mounted tentacles."""
from pathlib import Path
import json
import copy
import sys


def sync_installation_context(root):
    """Imported minimal presets share the already installed plugin package."""
    root = Path(root)
    reference = root / 'user/reference_tentacles_config/tentacles_config.json'
    if not reference.exists():
        return
    installed = json.loads(reference.read_text(encoding='utf-8'))
    context = installed.get('installation_context') or {}
    if not context.get('octobot_version'):
        return
    for target in (root / 'user/profiles').glob('astra_*/tentacles_config.json'):
        profile = json.loads(target.read_text(encoding='utf-8'))
        changed = False
        if not (profile.get('installation_context') or {}).get('octobot_version'):
            profile['installation_context'] = context
            changed = True
        activation = profile.get('tentacle_activation', {})
        defaults = installed.get('tentacle_activation', {})
        # A version marker alone is insufficient: minimal imported profiles also
        # need exchange connectors, WebInterface and automation infrastructure.
        if defaults and ('Services' not in activation or 'Bitget' not in activation.get('Trading', {})):
            merged = copy.deepcopy(defaults)
            for category, overrides in activation.items():
                merged.setdefault(category, {}).update(overrides)
            profile['tentacle_activation'] = merged
            changed = True
        if installed.get('registered_tentacles'):
            profile.setdefault('registered_tentacles', installed['registered_tentacles'])
        if changed:
            target.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def patch(root):
    target = Path(root) / 'tentacles/Services/Services_bases/gpt_service/gpt.py'
    if not target.exists():
        print('ASTRA: tentacles not installed yet; provider patch deferred')
        return
    sync_installation_context(root)
    action = Path(root) / 'tentacles/Automation/actions/sell_all_currencies_action/sell_all_currencies.py'
    if action.exists():
        action_source = action.read_text(encoding='utf-8')
        old_call = 'trading_api.sell_all_everything_for_reference_market(exchange_manager)'
        if old_call in action_source:
            action_source = action_source.replace('import asyncio\n',
                'import asyncio\nfrom octobot_paper_safety import sell_all_supported_paper_holdings\n')
            action_source = action_source.replace(old_call, 'sell_all_supported_paper_holdings(exchange_manager)')
            compile(action_source, str(action), 'exec')
            action.write_text(action_source, encoding='utf-8')
            print('ASTRA: applied cross-strategy paper liquidation patch')
    source = target.read_text(encoding='utf-8')
    marker = '# ASTRA provider compatibility v1'
    if marker in source:
        return
    original = '        return api_kwargs\n'
    if source.count(original) != 1:
        raise RuntimeError('Unsupported OctoBot LLMService layout; refusing an ambiguous patch')
    updated = source.replace(original,
        '        ' + marker + '\n'
        '        from octobot_provider_compat import adapt_chat_kwargs\n'
        '        return adapt_chat_kwargs(api_kwargs, self._get_base_url())\n')
    compile(updated, str(target), 'exec')
    target.write_text(updated, encoding='utf-8')
    print('ASTRA: applied DeepSeek JSON output compatibility patch')


if __name__ == '__main__':
    patch(sys.argv[1] if len(sys.argv) > 1 else '/octobot')
