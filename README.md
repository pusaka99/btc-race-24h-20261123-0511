# Patient trend-rider — cbBTC trading bot

Entry for make.fun competition `btc-race-24h-20261123-0511`:
race a BTC trading bot live for 24 hours (cbBTC/USDC, 1-minute bars,
$1,000 paper money, long-only, no leverage).

## Strategy

- **Buy 97% of equity** on the first tradeable bar.
- **Trailing stop 2.5%**: sell everything if a close falls 2.5% below
  the running peak close since entry.
- **Re-entry**: if flat after a stop-out and the close breaks above the
  high of the previous 24 closes, buy 97% of equity again.
- **Deploy the reserve**: when the close makes a new high while holding,
  spend the remaining cash (the 3% reserve).

The bot trades 1–3 times per window on purpose: a full-size round trip
pays ~26 bps (4 bps fee + 2 bps slippage + price impact), so frequent
trading bleeds to death on fees. It rides the trend and only exits on a
real pullback.

## Files

- `main.py` — the bot (Python 3.13, standard library only).
- `vendor/makefun_bars.py` — the kit's `agent-fun/bars-replay/v1` shim,
  byte-identical copy of the kit's `sdk/makefun_bars.py`.
- `.makefun/owner.json` — payout wallet claim file.
- `LICENSE` — MIT.

## Run (local replay)

Grade on the dev window with the kit CLI:

```sh
node <kit>/cli/makefun.mjs grade-local --kit <kit> --bot . --driver docker
```

No network, no wall clock, no randomness: two runs on the same data
produce identical orders.
