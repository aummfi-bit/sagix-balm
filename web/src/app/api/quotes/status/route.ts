import { NextResponse } from "next/server";

export const runtime = "nodejs";

const CBOE_URL =
  "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json";

/**
 * Is the delayed-quote feed reachable, and how old is what it is publishing?
 *
 * Runs server-side because cdn.cboe.com sends no CORS header, so the browser
 * cannot ask it directly. Reports only reachability and the feed's own
 * timestamp — the prices on the desk come from the committed snapshot, not
 * from here.
 */
export async function GET(request: Request) {
  const symbol = (
    new URL(request.url).searchParams.get("symbol") ?? "IBIT"
  ).toUpperCase();

  if (!/^[A-Z.]{1,10}$/.test(symbol)) {
    return NextResponse.json(
      { ok: false, error: "Bad symbol" },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(CBOE_URL.replace("{symbol}", symbol), {
      headers: { "User-Agent": "sagix-balm/0.1" },
      // Enough caching that a room full of viewers does not hammer the CDN,
      // little enough that the light means something.
      next: { revalidate: 60 },
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
      data?: { current_price?: number };
    };

    return NextResponse.json({
      ok: true,
      symbol,
      // Naive US/Eastern, exactly as Cboe stamps it.
      feedAsOf: body.timestamp ?? null,
      spot: body.data?.current_price ?? null,
    });
  } catch (err) {
    return NextResponse.json({
      ok: false,
      symbol,
      error: err instanceof Error ? err.message : "Network error",
    });
  }
}
