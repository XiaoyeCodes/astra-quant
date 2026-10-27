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


FEE_MARKER = "# ASTRA paper fees v1"
FEE_ANCHOR = "    def get_adapter_class(self):\n        return BitgetCCXTAdapter\n"
FEE_METHOD = (
    "    " + FEE_MARKER + ": ccxt's static Bitget fees (0.2%) replace the profile's paper fees\n"
    "    def get_trade_fee(self, symbol, order_type, quantity, price, taker_or_maker):\n"
    "        from octobot_paper_fees import configured_paper_fee\n"
    "        fee = configured_paper_fee(self.exchange_manager, symbol, quantity, price, taker_or_maker)\n"
    "        if fee is not None:\n"
    "            return fee\n"
    "        return super().get_trade_fee(symbol, order_type, quantity, price, taker_or_maker)\n\n"
)


def patch_paper_fees(root):
    """Make Bitget paper trades pay the profile's simulator fees (optional: skipped if layout changed)."""
    target = Path(root) / 'tentacles/Trading/Exchange/bitget/bitget_exchange.py'
    if not target.exists():
        return
    source = target.read_text(encoding='utf-8')
    if FEE_MARKER in source:
        return
    if source.count(FEE_ANCHOR) != 1:
        print('ASTRA: Bitget tentacle layout changed; paper fee patch skipped')
        return
    updated = source.replace(FEE_ANCHOR, FEE_METHOD + FEE_ANCHOR)
    compile(updated, str(target), 'exec')
    target.write_text(updated, encoding='utf-8')
    print('ASTRA: applied paper fee patch (profile simulator fees for Bitget paper trading)')


BACKUP_MARKER = "# ASTRA backup stop v1"
BACKUP_ANCHOR = (
    "        # select non trading profile\n"
    "        config = interfaces_util.get_edited_config(dict_only=False)\n"
    "        config.select_profile(self.PROFILE_ID)\n"
)
BACKUP_CODE = (
    "        " + BACKUP_MARKER + ": wait for the sold paper account to be saved, then park on 观望\n"
    "        from octobot_backup_stop import before_restart\n"
    "        profile_id = await before_restart(self.PROFILE_ID)\n"
    "        await super().process(execution_details)  # orders a strategy placed meanwhile\n"
    "        config = interfaces_util.get_edited_config(dict_only=False)\n"
    "        config.select_profile(profile_id)\n"
)


def patch_backup_stop(root):
    """StopTrading parks on astra_idle after the paper save (optional: skipped if layout changed)."""
    target = Path(root) / 'tentacles/Automation/actions/stop_trading_action/stop_trading.py'
    if not target.exists():
        return
    source = target.read_text(encoding='utf-8')
    if BACKUP_MARKER in source:
        return
    if source.count(BACKUP_ANCHOR) != 1:
        print('ASTRA: StopTrading layout changed; backup stop patch skipped')
        return
    updated = source.replace(BACKUP_ANCHOR, BACKUP_CODE)
    compile(updated, str(target), 'exec')
    target.write_text(updated, encoding='utf-8')
    print('ASTRA: applied backup stop patch (StopTrading parks on astra_idle without pausing)')


KEEP_MARKER = "# ASTRA keep paper account v1"
KEEP_ANCHOR = (
    "    async def stop(self):\n"
    "        await self.save_historical_portfolio_value(update_data=False)\n"
)
KEEP_CODE = (
    "    async def stop(self):\n"
    "        " + KEEP_MARKER + ": a session without any portfolio change never filled ending_portfolio,\n"
    "        # and saving None here made the next start rebuild the paper account from the starting funds\n"
    "        if self.ending_portfolio is None:\n"
    "            self._update_portfolios()\n"
    "        await self.save_historical_portfolio_value(update_data=False)\n"
)


def patch_keep_paper_account(path=None):
    """Stopping OctoBot keeps the paper account (optional: skipped if layout changed)."""
    if path is None:
        import importlib.util
        spec = importlib.util.find_spec('octobot_trading')
        if spec is None or not spec.submodule_search_locations:
            return
        path = Path(list(spec.submodule_search_locations)[0]) / \
            'personal_data/portfolios/history/historical_portfolio_value_manager.py'
    target = Path(path)
    if not target.exists():
        return
    source = target.read_text(encoding='utf-8')
    if KEEP_MARKER in source:
        return
    if source.count(KEEP_ANCHOR) != 1:
        print('ASTRA: portfolio history layout changed; keep paper account patch skipped')
        return
    updated = source.replace(KEEP_ANCHOR, KEEP_CODE)
    compile(updated, str(target), 'exec')
    target.write_text(updated, encoding='utf-8')
    print('ASTRA: applied keep paper account patch (stopping OctoBot no longer resets the paper account)')


def patch(root):
    target = Path(root) / 'tentacles/Services/Services_bases/gpt_service/gpt.py'
    if not target.exists():
        print('ASTRA: tentacles not installed yet; provider patch deferred')
        return
    sync_installation_context(root)
    patch_paper_fees(root)
    patch_backup_stop(root)
    patch_keep_paper_account()
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
