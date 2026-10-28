import { request } from "@playwright/test";
import { readFileSync } from "node:fs";
import { BASE, PAIR_FILE } from "../playwright.config";

/** Pair once through the API (the first of the fixture server's codes) and keep the cookie for every test. */
export default async function setup(): Promise<void> {
  const code = readFileSync(PAIR_FILE, "utf-8").split("\n")[0]?.trim();
  const ctx = await request.newContext({ baseURL: BASE, extraHTTPHeaders: { Origin: BASE } });
  const res = await ctx.post("/api/v1/pair", { data: { token: code } });
  if (res.status() !== 204) throw new Error(`pairing failed: ${res.status()} ${await res.text()}`);
  await ctx.storageState({ path: "e2e/.paired.json" });
  await ctx.dispose();
}
