# sagix-balm

Tracks a **put calendar / diagonal campaign** against an Interactive Brokers account: a
long-dated put "anchor" financed by a rolling series of short weekly puts, with the
central question being *how much of the anchor's debit has the weekly premium actually
paid off*.

Two underlyings, two tabs. Both are configurable — nothing is hardcoded to a ticker.

## Live desk (website)

**Shareable URL:** [https://sagix-balm.vercel.app](https://sagix-balm.vercel.app)

**Repo:** [github.com/aummfi-bit/sagix-balm](https://github.com/aummfi-bit/sagix-balm)

Interactive Next.js desk lives in [`web/`](web/) — greeks, term-structure callouts,
scenario chart, strike ladder, and roll checklist. Deployed on Vercel from the `web/` root.

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
balm sync                    # live snapshot: TWS marks and greeks + Flex history
balm sync --flex-file f.xml  # use a saved statement instead of downloading
balm flex --out f.xml        # download the raw Flex statement
```

Both `plan` and `sync` write the same JSON schema to `data/snapshots/latest.json` plus a
dated copy, so a plan can be diffed against reality later.

`sync` infers the structure from your actual holdings — the anchor is the longest-dated
long put, the income leg is the nearest-dated short put — and falls back to the
`[underlyings.plan]` block when it finds no calendar.

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

## The canvas

`put-calendar-desk.canvas.tsx` is the interactive front end: two tabs, live strike and
expiry selection, a scenario chart, a strike ladder, and the roll checklist. It reimplements
Black-Scholes in TypeScript so it recomputes as you change strikes rather than displaying a
frozen table.

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

Model prices are not fills. Every quoted premium here assumes you transact at mid, and
weekly options on both of these names have spreads wide enough that the difference is
material to the payoff horizon. Nothing in this repository is investment advice.
