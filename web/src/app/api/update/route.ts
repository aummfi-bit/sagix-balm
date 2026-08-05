import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { NextResponse } from "next/server";

const execFileAsync = promisify(execFile);

export const runtime = "nodejs";
export const maxDuration = 120;

function exists(p: string): boolean {
  return existsSync(/* turbopackIgnore: true */ p);
}

function repoRoot(): string {
  const cwd = process.cwd();
  if (exists(path.join(cwd, "pyproject.toml"))) return cwd;
  const parent = path.resolve(cwd, "..");
  if (exists(path.join(parent, "pyproject.toml"))) return parent;
  return cwd;
}

function balmBin(root: string): string | null {
  const candidates = [
    path.join(root, ".venv", "bin", "balm"),
    path.join(root, ".venv", "Scripts", "balm.exe"),
  ];
  for (const c of candidates) {
    if (exists(c)) return c;
  }
  return null;
}

async function runBalm(
  bin: string,
  root: string,
  args: string[],
): Promise<{ ok: boolean; stdout: string; stderr: string }> {
  try {
    const { stdout, stderr } = await execFileAsync(bin, args, {
      cwd: root,
      timeout: 90_000,
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
      maxBuffer: 4 * 1024 * 1024,
    });
    return { ok: true, stdout, stderr };
  } catch (err) {
    const e = err as {
      stdout?: string;
      stderr?: string;
      message?: string;
    };
    return {
      ok: false,
      stdout: e.stdout ?? "",
      stderr: e.stderr ?? e.message ?? String(err),
    };
  }
}

async function loadSnapshot(root: string): Promise<object | null> {
  const snapPath = path.join(root, "data", "snapshots", "latest.json");
  if (!exists(snapPath)) return null;
  return JSON.parse(await readFile(snapPath, "utf8"));
}

async function loadPublicCache(): Promise<object | null> {
  const publicSnap = path.join(process.cwd(), "public", "data", "latest.json");
  if (!exists(publicSnap)) return null;
  return JSON.parse(await readFile(publicSnap, "utf8"));
}

/**
 * Runs `balm sync` when TWS is reachable, otherwise `balm plan`.
 * On Vercel (no Python), serves the last published snapshot from public/data.
 */
export async function POST() {
  // Hosted deploy: no local balm / TWS — return the committed cache.
  if (process.env.VERCEL) {
    const snapshot = await loadPublicCache();
    if (!snapshot) {
      return NextResponse.json(
        {
          ok: false,
          error:
            "No cached snapshot on this deploy. Run `balm plan` or `balm sync` locally, then redeploy.",
        },
        { status: 503 },
      );
    }
    return NextResponse.json({
      ok: true,
      command: "cached",
      message:
        "Hosted site loaded the published snapshot. For a live refresh, run balm sync locally with TWS up, then redeploy.",
      snapshot,
    });
  }

  const root = repoRoot();
  const bin = balmBin(root);

  if (!bin) {
    const snapshot = await loadPublicCache();
    if (snapshot) {
      return NextResponse.json({
        ok: true,
        command: "cached",
        message:
          "balm CLI not found. Loaded cached snapshot. Install with: .venv/bin/pip install -e .",
        snapshot,
      });
    }
    return NextResponse.json(
      {
        ok: false,
        error:
          "balm CLI not found. Locally: python3.12 -m venv .venv && .venv/bin/pip install -e .",
      },
      { status: 503 },
    );
  }

  let command = "sync";
  let result = await runBalm(bin, root, ["sync", "--name", "latest"]);
  if (!result.ok) {
    command = "plan";
    result = await runBalm(bin, root, ["plan", "--name", "latest"]);
  }

  if (!result.ok) {
    return NextResponse.json(
      {
        ok: false,
        error: `balm ${command} failed`,
        detail: result.stderr || result.stdout,
      },
      { status: 500 },
    );
  }

  const snapshot = await loadSnapshot(root);
  if (!snapshot) {
    return NextResponse.json(
      {
        ok: false,
        error: "balm finished but data/snapshots/latest.json is missing",
      },
      { status: 500 },
    );
  }

  try {
    const publicDir = path.join(process.cwd(), "public", "data");
    await mkdir(publicDir, { recursive: true });
    await writeFile(
      path.join(publicDir, "latest.json"),
      JSON.stringify(snapshot, null, 2),
    );
  } catch {
    // Non-fatal
  }

  return NextResponse.json({
    ok: true,
    command: `balm ${command}`,
    message:
      command === "sync"
        ? "Refreshed from TWS / Flex via balm sync."
        : "TWS unavailable — refreshed model snapshot via balm plan.",
    stdout: result.stdout.trim(),
    snapshot,
  });
}
