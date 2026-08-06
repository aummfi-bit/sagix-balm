import { readFile } from "node:fs/promises";
import path from "node:path";
import { CalendarDesk } from "@/components/CalendarDesk";
import {
  deskDataFromSnapshot,
  type BalmSnapshot,
  type DeskData,
} from "@/lib/snapshot";

// The snapshot is a committed file, so it only changes on deploy; re-read it
// periodically rather than baking it into the build output forever.
export const revalidate = 300;

async function loadDesk(): Promise<DeskData | undefined> {
  try {
    const raw = await readFile(
      path.join(process.cwd(), "public", "data", "latest.json"),
      "utf8",
    );
    return deskDataFromSnapshot(JSON.parse(raw) as BalmSnapshot, "snapshot");
  } catch {
    // No snapshot committed yet: the desk falls back to its seeded markets,
    // which carry no quotes and therefore show no tradable prices.
    return undefined;
  }
}

export default async function Home() {
  const initial = await loadDesk();
  return (
    <main className="flex-1">
      <CalendarDesk initial={initial} />
    </main>
  );
}
