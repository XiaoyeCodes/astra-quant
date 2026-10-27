"""Paper-trading fees from the profile instead of ccxt's static market defaults.

In live paper trading OctoBot computes fees through the ccxt connector, which uses ccxt's
built-in Bitget market fees (0.2% maker and taker) and ignores the profile's
`trader-simulator.fees`. Real Bitget spot is 0.1%. Only simulated traders are affected;
a real trader keeps the normal path untouched.
"""
from decimal import Decimal

SIMULATOR = "trader-simulator"
FEES = "fees"


def configured_paper_fee(exchange_manager, symbol, quantity, price, taker_or_maker):
    """Fee dict for a simulated trade using the profile's configured %, or None to keep OctoBot's."""
    if not getattr(exchange_manager, "is_simulated", False) or getattr(exchange_manager, "is_future", False):
        return None
    fees = ((exchange_manager.config or {}).get(SIMULATOR) or {}).get(FEES) or {}
    side = taker_or_maker or "taker"
    if side not in fees:
        return None
    rate = Decimal(str(fees[side])) / Decimal(100)
    quote = symbol.split("/")[1].split(":")[0]
    return {
        "type": side,
        "currency": quote,
        "rate": float(rate),
        "cost": Decimal(str(quantity)) * Decimal(str(price)) * rate,
        "is_from_exchange": False,
    }
