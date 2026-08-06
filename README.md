# sagix-balm

Tracks a **put calendar / diagonal campaign** against an Interactive Brokers account: a
long-dated put "anchor" financed by a rolling series of short weekly puts, with the
central question being *how much of the anchor's debit has the weekly premium actually
paid off*.

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
There is no refresh button — refreshing means `balm quotes` (or `balm sync`) writing a new
snapshot, which a page view cannot do. The desk renders the committed snapshot.

The holdings panel shows the share count and cash from the last `balm sync`, plus any
option lines you add by hand. Manual lines are stored in your browser and never sent
anywhere; the balm connection to IBKR is read-only and places no orders.

The calls panel is the same analysis pointed at the other half of the chain — a long-dated
call anchor financed by weekly short calls. `balm sync` pulls both rights so it has a real
book behind it; set `model.include_calls = false` to skip the extra market data.

Deployed on Vercel from the `web/` root.

```bash
cd web && npm install && npm run dev
```

## What it does

- **Prices and risks the structure.** Black-Scholes for speed, plus a Cox-Ross-Rubinstein
  binomial for the short leg, because American early exercise is what actually determines
  assignment risk.
- **Tracks the campaign.** Signed cash-flow accounting over your real fills, so rolls,
  partial closes, assignments and commissions all fold in without special cases. Reports
  realized premium, recovery ratio, and an honest weeks-to-payoff based on the rate you
  have actually achieved rather than a theoretical full-premium capture.
- **Models scenarios properly.** Volatility shocks are damped across the term structure by
  the inverse square root of time, so a crash correctly inflates the weekly you are short
  far more than the anchor you own.
- **Prices the side you actually trade.** Buys are marked at the ask, sales at the bid, so
  every cost, credit and valuation is one you could transact at rather than a mid the book
  never offers. See [Which price](#which-price).
- **Runs the weekly checklist.** Roll deadline, harvest target, strike drift, assignment
  cushion, and bid/ask spread, each evaluated against the current position.

## Install

Requires Python 3.11+ (`ib_async` needs 3.10 or newer).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp config.example.toml config.toml
.venv/bin/balm doctor
```

`doctor` reports what is wired up and what is missing:

```
Underlyings   : GLXY, IBIT
TWS socket    : reachable at 127.0.0.1:7497
Flex token    : present (from $IBKR_FLEX_TOKEN)
ib_async      : installed
```

## Connecting to IBKR

Two sources are needed, and neither is sufficient alone: TWS only retains executions for
the current session, so a multi-week campaign history has to come from a Flex statement.
The tool reconciles them, de-duplicating on execution id.

### 1. TWS or IB Gateway (live marks, greeks, positions)

Start TWS or IB Gateway and enable
**Configure → API → Settings → Enable ActiveX and Socket Clients**. Set the matching port
in `config.toml`:

| Application | Paper | Live |
| --- | --- | --- |
| TWS | 7497 | 7496 |
| IB Gateway | 4002 | 4001 |

The connection is opened **read-only**. This tool never places an order.

### 2. Flex Web Service (settled trade history)

In Client Portal go to **Performance & Reports → Flex Queries** and create a *Trades*
query including `underlyingSymbol`, `assetCategory`, `putCall`, `strike`, `expiry`,
`quantity`, `tradePrice`, `ibCommission`, `tradeDate`, `multiplier`, `notes` and
`tradeID`. Then enable **Flex Web Service Configuration** and generate a token.

```bash
export IBKR_FLEX_TOKEN=...          # never put this in config.toml
# set flex.query_id in config.toml
```

## Commands

```bash
balm doctor                  # check config, TWS reachability, Flex credentials
balm plan                    # model-only snapshot from [underlyings.plan], no TWS needed
balm quotes                  # delayed-quote snapshot from Cboe: no TWS, no credentials
balm sync                    # live snapshot: TWS marks and greeks + Flex history
balm sync --flex-file f.xml  # use a saved statement instead of downloading
balm flex --out f.xml        # download the raw Flex statement
```

Both `plan` and `sync` write the same JSON schema to `data/snapshots/latest.json` plus a
dated copy, so a plan can be diffed against reality later.

`sync` infers the structure from your actual holdings — the anchor is the longest-dated
long put, the income leg is the nearest-dated short put — and falls back to the
`[underlyings.plan]` block when it finds no calendar.

## Two quote sources

| | `balm sync` | `balm quotes` |
| --- | --- | --- |
| Source | TWS / IB Gateway | Cboe's public delayed feed |
| Needs | A logged-in gateway on the same machine | Nothing — an unauthenticated GET |
| Freshness | Live | ~15 minutes behind, self-timestamped |
| Gives | Chain, positions, cash, today's fills | Chain and the underlying's price only |
| Runs on | Wherever the gateway runs | Anywhere: laptop, cron, cloud runner |

`balm quotes` is what makes an unattended refresh possible. It takes spot, implied
volatility and both sides of every quote from Cboe, writes the same snapshot schema, and
labels everything `cboe` with the feed's own timestamp so nothing gets mistaken for live.
It knows nothing about your account: no positions, no cash, no fills — those need `sync`
or a Flex statement (`balm quotes --flex-file statement.xml` folds in a saved one).

Delayed quotes are for knowing where you stand, not for pricing a roll to the cent. The
desk says so on every page.

## Configuration

Everything lives in `config.toml`, which is gitignored because the Flex token grants read
access to your whole account history. See `config.example.toml`; the parts worth knowing:

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

`balm plan` has no market data behind it, so a model snapshot quotes nothing at all. In a
live `sync` a missing quote fails the liquidity check and leaves the harvest target
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
  tws.py         ib_async client: account, positions, chains with greeks, today's fills
  flex.py        Flex Web Service two-step download and trade parsing
  snapshot.py    JSON assembly shared by plan and sync
  cli.py         command line entry point
tests/           pricing checked against textbook values, accounting against fixtures
```

```bash
.venv/bin/python -m pytest
```

## Caveats

Quoted prices are the best available answer to "what would this trade at", not a promise of
one: a bid is what someone is willing to pay for the size shown, and a wide market can move
away from you between the snapshot and the order. Where no quote exists the model value is
labelled as such and should be treated as a valuation, never as a fill. Nothing in this
repository is investment advice.
