"""Patient trend-rider for cbBTC (make.fun btc-race-24h-20261123-0511).

Strategy, designed from the dev window alone (no peeking beyond it):
- Buy 97% of equity on the first tradeable bar.
- Trailing stop: sell everything if a close falls 2.5% below the running
  peak close since entry.
- Re-entry: if flat after a stop-out and the close breaks above the high
  of the previous 24 closes, buy 97% of equity again.
- Deploy the remaining cash (the 3% reserve) when the close makes a new
  high while holding.

Long-only, no leverage, no wall clock, no randomness, no network, no files
outside the repository. Standard library only; the wire protocol is spoken
by the vendored shim (sdk/makefun_bars.py, byte-identical copy).

Fills land at the next bar's open with a 4 bps fee, 2 bps slippage and
constant-product impact, so the bot trades 1-3 times per window: every
trade must catch a move larger than the ~26 bps round-trip cost.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
BUY_BPS = 9700             # buy 97% of equity per entry
TRAIL_BPS = 250            # trailing stop 2.5% below the running peak close
LOOKBACK = 24              # breakout lookback in bars
MAX_SLIPPAGE_BPS = 100     # 1%: far above fee (4) + slippage (2) + impact (~7-17)
FEE_RESERVE_BPS = 10       # keep 0.1% of cash aside for the fill fee on deploy buys

_closes = []               # closes seen so far, in order (ints, price_scale 5)
_peak = None               # running peak close while holding (int or None)
_entered = False           # a buy order was placed at least once
_stopped_out = False       # a sell order was placed at least once


def _min_notional(state):
    return int(state.hello["fees"]["min_notional_quote_units"])


def _buy_notional(state, fraction_bps):
    """What a buy may spend: a fraction of cash, inside the weight cap."""
    room = state.weight_room(SYMBOL)
    amount = state.cash * fraction_bps // 10000
    if amount > room:
        amount = room
    return amount


def on_batch(state, batch):
    global _peak, _entered, _stopped_out
    if state.warming_up:
        return []
    close = state.close(SYMBOL)
    if close is None:
        return []
    for bar in batch.get("bars", []):
        if bar.get("symbol") == SYMBOL:
            _closes.append(int(bar["c"]))
    prior = _closes[:-1]
    holding = state.positions.get(SYMBOL)
    qty = holding.qty if holding is not None else 0
    min_notional = _min_notional(state)
    if qty > 0:
        is_new_high = _peak is not None and close > _peak
        if _peak is None or close > _peak:
            _peak = close
        # Trailing stop: close 2.5% below the running peak -> sell everything.
        if close * 10000 < _peak * (10000 - TRAIL_BPS):
            _peak = None
            _stopped_out = True
            return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        # New high while holding: deploy the cash reserve.
        if is_new_high and state.cash >= min_notional:
            amount = _buy_notional(state, 10000 - FEE_RESERVE_BPS)
            if amount >= min_notional:
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": amount,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []
    _peak = None
    # Flat: enter on the first bars, or on a 24-bar-high breakout afterwards.
    if _stopped_out:
        enter = len(prior) >= LOOKBACK and close > max(prior[-LOOKBACK:])
    elif not _entered:
        enter = True
    else:
        # A buy was placed but we hold nothing (rejected fill): retry early,
        # breakout once history is long.
        enter = len(prior) < LOOKBACK or close > max(prior[-LOOKBACK:])
    if enter:
        amount = _buy_notional(state, BUY_BPS)
        if amount >= min_notional:
            _peak = close
            _entered = True
            return [{"side": "buy", "symbol": SYMBOL,
                     "notional_quote_units": amount,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
    return []


makefun_bars.run(on_batch)
