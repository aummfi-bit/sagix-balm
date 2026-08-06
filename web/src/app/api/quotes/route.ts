import { NextResponse } from "next/server";

export const runtime = "nodejs";

const CBOE_URL =
  "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json";

/** Strikes this far either side of spot; beyond that the desk never looks. */
const STRIKE_WINDOW = 0.25;

type CboeOption = {
  option?: string;
  bid?: number;
  ask?: number;
  iv?: number;
};

/** `GLXY260814P00022000` → root, expiry, right, strike. Parsed from the right. */
function parseOsi(
  osi: string,
): { expiry: string; kind: "put" | "call"; strike: number } | null {
  const body = osi.slice(-15);
  if (body.length !== 15) return null;
  const right = body[6];
  if (right !== "P" && right !== "C") return null;
  const yy = body.slice(0, 2);
  const expiry = `20${yy}-${body.slice(2, 4)}-${body.slice(4, 6)}`;
  const strike = Number(body.slice(7)) / 1000;
  if (!Number.isFinite(strike)) return null;
  return { expiry, kind: right === "P" ? "put" : "call", strike };
}

/**
 * Fresh quotes for one underlying, shaped for the desk.
 *
 * The desk polls this instead of waiting for a snapshot to be committed and
 * redeployed. It is a server route because cdn.cboe.com sends no CORS header,
 * and because one cached fetch can serve every viewer.
 *
 * Only the near-the-money slice is returned: the full chain is thousands of
 * contracts and the desk shows a couple of dozen strikes.
 */
export async function GET(request: Request) {
  const symbol = (
    new URL(request.url).searchParams.get("symbol") ?? ""
  ).toUpperCase();

  if (!/^[A-Z.]{1,10}$/.test(symbol)) {
    return NextResponse.json({ ok: false, error: "Bad symbol" }, { status: 400 });
  }

  try {
    const res = await fetch(CBOE_URL.replace("{symbol}", symbol), {
      headers: { "User-Agent": "sagix-balm/0.1" },
      // The feed is fifteen minutes delayed, so polling it harder than this
      // buys nothing but load.
      next: { revalidate: 45 },
    });

    if (!res.ok) {
      return NextResponse.json({
        ok: false,
        symbol,
        error: `Cboe returned HTTP ${res.status}`,
      });
    }

    const body = (await res.json()) as {
      timestamp?: string;
      data?: { current_price?: number; options?: CboeOption[] };
    };

    const spot = body.data?.current_price;
    const options = body.data?.options;
    if (!spot || !options?.length) {
      return NextResponse.json({
        ok: false,
        symbol,
        error: "Cboe returned no usable chain",
      });
    }

    const lo = spot * (1 - STRIKE_WINDOW);
    const hi = spot * (1 + STRIKE_WINDOW);

    const quotes: Record<string, { bid: number | null; ask: number | null }> = {};
    // Implied vols near the money, per expiry, to rebuild the term structure.
    const ivs = new Map<string, number[]>();

    for (const row of options) {
      if (!row.option) continue;
      const parsed = parseOsi(row.option);
      if (!parsed || parsed.strike < lo || parsed.strike > hi) continue;

      const bid = typeof row.bid === "number" && row.bid > 0 ? row.bid : null;
      const ask = typeof row.ask === "number" && row.ask > 0 ? row.ask : null;
      if (bid != null || ask != null) {
        quotes[`${parsed.kind}|${parsed.expiry}|${parsed.strike}`] = { bid, ask };
      }

      // A zero iv is the feed failing to compute one, not a real zero.
      if (row.iv && Math.abs(parsed.strike - spot) / spot <= 0.08) {
        const list = ivs.get(parsed.expiry) ?? [];
        list.push(row.iv);
        ivs.set(parsed.expiry, list);
      }
    }

    const tenors = [...ivs.entries()]
      .map(([expiry, list]) => ({
        expiry,
        iv: list.sort((a, b) => a - b)[Math.floor(list.length / 2)],
      }))
      .sort((a, b) => a.expiry.localeCompare(b.expiry));

    return NextResponse.json({
      ok: true,
      symbol,
      spot,
      // Naive US/Eastern, exactly as Cboe stamps it.
      quotesAsOf: body.timestamp ?? null,
      quotes,
      tenors,
    });
  } catch (err) {
    return NextResponse.json({
      ok: false,
      symbol,
      error: err instanceof Error ? err.message : "Network error",
    });
  }
}
