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

A stop-sell can be rejected for max_slippage when the next bar's open gaps
down past its allowance: the bot then retries on every following bar while
it still holds, escalating max_slippage_bps by 100 bps per rejection (100
-> 200 -> ... -> 1000, 10% cap). Buys and re-entry buys always use 100 bps.
Without the escalation a rejected stop leaves the bot holding through the
crash it already decided to exit, because the trailing-stop peak resets on
the first attempt.

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
MAX_SLIPPAGE_BPS = 100     # 1%: buys and re-entry buys, far above fee (4) + slippage (2) + impact (~7-17)
STOP_SLIPPAGE_BASE_BPS = 100  # first stop-sell attempt, same as a buy
STOP_SLIPPAGE_STEP_BPS = 100  # +1% per max_slippage rejection of the stop-sell
STOP_SLIPPAGE_MAX_BPS = 1000  # 10% cap: a stop must eventually fill, not loop forever
FEE_RESERVE_BPS = 10       # keep 0.1% of cash aside for the fill fee on deploy buys

_closes = []               # closes seen so far, in order (ints, price_scale 5)
_peak = None               # running peak close while holding (int or None)
_entered = False           # a buy order was placed at least once
_stopped_out = False       # a sell order was placed at least once
_stop_pending = False      # a stop-sell was decided and the fill is not confirmed yet
_stop_retries = 0          # consecutive max_slippage rejections of the stop-sell


def _stop_slippage_bps():
    """Current allowance for a stop-sell: base + 100 bps per rejection, capped at 10%."""
    return min(STOP_SLIPPAGE_BASE_BPS + STOP_SLIPPAGE_STEP_BPS * _stop_retries,
               STOP_SLIPPAGE_MAX_BPS)


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
    global _peak, _entered, _stopped_out, _stop_pending, _stop_retries
    if state.warming_up:
        return []
    close = state.close(SYMBOL)
    if close is None:
        return []
    # What happened to last bar's orders: a stop-sell rejected for
    # max_slippage escalates the next attempt; a filled sell clears the stop.
    for fill in batch.get("fills", []):
        if fill.get("side") != "sell":
            continue
        if fill.get("status") == "rejected" and fill.get("reason") == "max_slippage":
            _stop_retries += 1
        elif fill.get("status") == "filled":
            _stop_pending = False
            _stop_retries = 0
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
        # A stop already decided: keep selling until the fill lands, with an
        # escalated allowance. The peak is kept: resetting it here is what
        # made v1 silently hold after a rejected stop-sell.
        if _stop_pending:
            return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                     "max_slippage_bps": _stop_slippage_bps()}]
        # Trailing stop: close 2.5% below the running peak -> sell everything.
        if close * 10000 < _peak * (10000 - TRAIL_BPS):
            _stop_pending = True
            _stop_retries = 0
            _stopped_out = True
            return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                     "max_slippage_bps": STOP_SLIPPAGE_BASE_BPS}]
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
            _stop_pending = False
            _stop_retries = 0
            return [{"side": "buy", "symbol": SYMBOL,
                     "notional_quote_units": amount,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
    return []


makefun_bars.run(on_batch)
