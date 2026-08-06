# sagix-balm

A desk for **calendar / diagonal structures**: a long-dated "anchor" financed by a rolling
series of short weekly options, with the central question being *how much of the anchor's
debit has the weekly premium actually paid off*.

Prices come from Cboe's public delayed feed. There is no broker account, no gateway
process and no credential anywhere in this repository — which is why the whole thing runs
from a static host and keeps itself current.

Two underlyings, two tabs. Both are configurable — nothing is hardcoded to a ticker.

## Live desk (website)

**Shareable URL:** [https://sagix-balm.vercel.app](https://sagix-balm.vercel.app)

**Repo:** [github.com/aummfi-bit/sagix-balm](https://github.com/aummfi-bit/sagix-balm)

Interactive Next.js desk lives in [`web/`](web/). Pick a ticker, see what you hold in it,
then open either calendar:

```
● Cboe feed reachable · quoted 06 Aug 15:49 ET · delayed ~15 min
[GLXY] [IBIT]                 ticker
┌─ Holdings ──────────────┐   price, shares, stock value, cash, and your option lines
[▾ Puts] [▸ Calls]            one panel per right, collapsible
┌─ Put calendar ──────────┐   greeks, term structure, scenarios, ladder, roll checklist
```

The status line is the only thing at the top: a green light when the quote feed answers,
red when it does not, the timestamp on the prices actually being displayed, and the delay.
There is no refresh button: the desk polls the feed itself every minute through its own
`/api/quotes` route, so the prices keep up without anyone clicking anything. The committed
snapshot is what paints first, before the first poll lands.

The holdings panel is yours to fill in: share count, cash, and option lines with the fill
price you actually got. It lives in your browser's local storage and is never sent
anywhere. Each line is valued at the side that would close it.

The calls panel is the same analysis pointed at the other half of the chain — a long-dated
call anchor financed by weekly short calls. The feed carries both rights, so both panels
have a real book behind them.

Deployed on Vercel from the `web/` root.

```bash
cd web && npm install && npm run dev
```

## What it does

- **Prices and risks the structure.** Black-Scholes for speed, plus a Cox-Ross-Rubinstein
  binomial for the short leg, because American early exercise is what actually determines
  assignment risk.
- **Tracks the campaign.** Signed cash-flow accounting over your fills, so rolls, partial
  closes, assignments and commissions all fold in without special cases. Reports realized
  premium, recovery ratio, and an honest weeks-to-payoff based on the rate actually
  achieved rather than a theoretical full-premium capture. (`campaign.py` currently has no
  automatic source of fills — the broker integration it used to read is gone, and the
  desk's holdings panel is not yet wired into it.)
- **Models scenarios properly.** Volatility shocks are damped across the term structure by
  the inverse square root of time, so a crash correctly inflates the weekly you are short
  far more than the anchor you own.
- **Prices the side you actually trade.** Buys are marked at the ask, sales at the bid, so
  every cost, credit and valuation is one you could transact at rather than a mid the book
  never offers. See [Which price](#which-price).
- **Runs the weekly checklist.** Roll deadline, harvest target, strike drift, assignment
  cushion, and bid/ask spread, each evaluated against the current position.

## Install

Requires Python 3.11+. The only dependency is `requests`.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp config.example.toml config.toml
.venv/bin/balm doctor
```

`doctor` reports what is wired up and what is missing:

```
Underlyings   : GLXY, IBIT
  GLXY   configured
  IBIT   configured
Cboe GLXY     : 822 contracts, spot 20.14, quoted 2026-08-06 15:49:05 ET
Cboe IBIT     : 2366 contracts, spot 36.62, quoted 2026-08-06 17:44:15 ET
Quote delay   : ~15 min — indicative, not executable
```

## Where the prices come from

[Cboe](https://www.cboe.com) publishes delayed quotes for US-listed options as plain JSON,
no key and no account:

```
https://cdn.cboe.com/api/global/delayed_quotes/options/{SYMBOL}.json
```

Each contract carries bid, ask, sizes, implied volatility, greeks and open interest, and
the payload carries the underlying's own market and a timestamp. Everything sourced from
it is labelled with that timestamp so nothing gets mistaken for live.

**It is delayed by about fifteen minutes.** That is fine for knowing where a position
stands and wrong for pricing a roll to the cent, and the desk says so on every page. It
also knows nothing about *you*: no positions, no cash, no fills. Those you type into the
holdings panel.

## Commands

```bash
balm doctor                  # check the config and that the feed answers
balm plan                    # model-only snapshot from [underlyings.plan], no market data
balm quotes                  # snapshot priced off the delayed feed
```

Both write the same JSON schema to `data/snapshots/latest.json` plus a dated copy, so a
plan can be diffed against what the market actually quotes.

`quotes` keeps the strikes and expiries named in `[underlyings.plan]` but takes spot and
implied volatility from the feed — a plan written weeks ago carries a spot that has since
moved, and pricing a structure off it is how a short leg goes quietly in the money without
the checklist noticing.

The desk does not need either command to stay current; it polls the feed directly. Run
`balm quotes` when you want a snapshot committed for the first paint, or to diff a day
against another.

## Configuration

Everything lives in `config.toml` — see `config.example.toml`. Nothing in it is a
credential. The parts worth knowing:

```toml
[[underlyings]]
symbol = "GLXY"

[underlyings.plan]
spot = 22.70
anchor_strike = 22.5
anchor_expiry = 2027-01-15
anchor_iv = 0.98            # decimals, not percent
short_strike = 22.0
short_expiry = 2026-08-14
short_iv = 1.10
short_credit = 1.21         # your actual fill, drives the harvest check
vol_beta = 1.5              # 10% drop lifts 30-day IV by 15 points

[rules]
roll_by_weekday = 2         # Monday=0, so never carry past Wednesday
extrinsic_harvest_target = 0.80
short_delta_target = 0.40
short_delta_max = 0.55
assignment_warn_premium = 0.10
max_spread_pct = 0.10
```

`vol_beta` is deliberately per-underlying. A 60-point volatility shock is realistic for a
crypto-linked single name and absurd for a broad ETF.

## Which price

Nothing here transacts at the mid, so nothing here is marked at the mid. Each number takes
the side of the trade it describes:

| Number | Side | Why |
| --- | --- | --- |
| Anchor debit, `anchorCandidates[].cost` | **ask** | You buy the anchor |
| Weekly credit, `weeklyCandidates[].premium`, breakevens | **bid** | You sell the weekly |
| Anchor mark (`campaign.anchorMark`) | **bid** | Liquidating a long |
| Open short liability, cost to close, harvest target | **ask** | Buying the short back |
| A holding's value now | **bid** long, **ask** short | Whichever side closes it |
| `net.liquidationValue`, `campaign.netLiquidation` | both | Bid on the long, ask on the short |
| Underlying spot | mid | A model reference, not a position being traded |

On a weekly quoting 25% wide, marking both legs at the mid overstates the campaign by half
a spread on each — and in the flattering direction both times.

**Where there is no quote, there is no price.** The tradable fields go `null`, the desk
shows a dash, and everything derived from them — breakevens, payoff horizons, cost as a
share of spot — goes with them rather than being estimated. The model's own value travels
alongside under a `theo` name and in the desk's `Model` column, where it can be read as
what it is: a valuation, not a fill.

`balm plan` has no market data behind it, so a model snapshot quotes nothing at all. With
quotes, a missing side fails the liquidity check and leaves the harvest target
unmeasurable, because an exit that cannot be priced is a finding, not a detail.

## The desk

[`web/src/components/CalendarDesk.tsx`](web/src/components/CalendarDesk.tsx) is the front
end described above, and [`web/src/lib/pricing.ts`](web/src/lib/pricing.ts) reimplements
Black-Scholes for both rights in TypeScript so the greeks recompute as you change strikes
rather than displaying a frozen table. Quotes come from the snapshot; the model never
supplies a price the desk presents as tradable.

## Three things the numbers say

These fall out of the model rather than being assumed, and all three cut against intuition.

**An at-the-money calendar on a high-volatility name is net *long* delta.** At 90–100% IV
the `sigma^2/2 * T` drift term pushes a long-dated ATM put far out of the money in forward
terms, so its delta is only about −0.27 against roughly −0.45 for the weekly. Selling an ATM
weekly against an ATM anchor therefore produces a position that *loses on the first leg
down*. If you want the anchor to behave like protection, it has to sit meaningfully higher
than the short strike.

**The term structure decides whether the trade has an edge at all.** A put calendar is short
the front tenor and long the back one, so it wants backwardation. GLXY trades around 110–130%
on weeklies against ~98% on the January 2027 anchor, which pays you to run the structure.
IBIT is the mirror image: ~36% front against ~42% on the same anchor, so every roll sells
volatility for less than the anchor cost. Same trade, opposite structural sign.

**The worst case is a sharp rally, not a crash.** In a crash the anchor gains enough to
partly offset the short going in the money. In a fast rally the anchor loses on both delta
and vega while the volatility crush shrinks every subsequent weekly credit, and nothing
offsets it.

## Layout

```
src/balm/
  pricing.py     Black-Scholes, CRR binomial, IV solver, early-exercise premium
  quotes.py      two-sided markets and the side each transaction prices at
  cboe.py        delayed public quote feed: chain, greeks, spot, no credentials
  structure.py   calendar greeks, term-structure vol shocks, scenarios, roll rules
  campaign.py    signed cash-flow accounting over the trade history
  snapshot.py    JSON assembly shared by plan and sync
  cli.py         command line entry point
tests/           pricing checked against textbook values, accounting against fixtures
```

```bash
.venv/bin/python -m pytest
```

## Caveats

The feed is delayed by about fifteen minutes, so every price here is where the market was,
not where it is. Even live, a bid is only what someone will pay for the size shown, and a
wide market moves away from you between the quote and the order. Where no quote exists the
model value is labelled as such and is a valuation, never a fill.

The desk is a calculator, not a broker: it places no orders and holds no credentials, and
the holdings you enter are yours to keep accurate. Nothing here is investment advice.
