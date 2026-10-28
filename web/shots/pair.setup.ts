import { request } from "@playwright/test";
import { readFileSync } from "node:fs";
import { SHOTS_BASE, SHOTS_PAIR_FILE } from "../playwright.shots.config";

/** Pair once through the API and keep the cookie for every paired screenshot. */
export default async function setup(): Promise<void> {
  const code = readFileSync(SHOTS_PAIR_FILE, "utf-8").split("\n")[0]?.trim();
  const ctx = await request.newContext({ baseURL: SHOTS_BASE, extraHTTPHeaders: { Origin: SHOTS_BASE } });
  const res = await ctx.post("/api/v1/pair", { data: { token: code } });
  if (res.status() !== 204) throw new Error(`pairing failed: ${res.status()} ${await res.text()}`);
  await ctx.storageState({ path: "shots/.paired.json" });
  await ctx.dispose();
}
