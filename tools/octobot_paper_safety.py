"""Liquidate simulated spot holdings even when their old strategy is inactive."""
from decimal import Decimal


async def liquidate_paper_spot(exchange_manager, mark_price_source):
    if not exchange_manager.is_simulated or exchange_manager.is_future:
        raise ValueError('This liquidation adapter is only for simulated spot accounts')
    portfolio_manager = exchange_manager.exchange_personal_data.portfolio_manager
    quote = portfolio_manager.reference_market
    currencies = list(portfolio_manager.portfolio.portfolio.items())
    orders = []
    for currency, holding in currencies:
        if currency == quote or Decimal(str(holding.available)) <= 0:
            continue
        symbol = f'{currency}/{quote}'
        # Ask the loaded exchange market catalog, not the active strategy's pair list.
        market = exchange_manager.exchange.get_market_status(symbol, with_fixer=False)
        if not market or market.get('active') is False:
            raise ValueError(f'Cannot liquidate simulated holding: unavailable market {symbol}')
        ticker = await exchange_manager.exchange.get_price_ticker(symbol)
        price = Decimal(str(ticker.get('close') or ticker.get('last') or 0))
        if not price.is_finite() or price <= 0:
            raise ValueError(f'Cannot liquidate simulated holding: invalid price for {symbol}')
        data = exchange_manager.exchange_symbols_data.get_exchange_symbol_data(symbol)
        data.prices_manager.set_mark_price(price, mark_price_source)
        orders.extend(await exchange_manager.trader._sell_everything(symbol, False, timeout=10))
    return orders


async def sell_all_supported_paper_holdings(exchange_manager):
    from octobot_trading import api, enums
    if not exchange_manager.is_simulated or exchange_manager.is_future:
        return await api.sell_all_everything_for_reference_market(exchange_manager)
    return await liquidate_paper_spot(exchange_manager, enums.MarkPriceSources.EXCHANGE_MARK_PRICE.value)
