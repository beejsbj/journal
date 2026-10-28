/**
 * The fingerprint of what the build is made from, written to `static/build.json` and recomputed by
 * `tests/test_web_build.py` (the same algorithm in Python): the suite fails when `web/` changed and the committed
 * `static/` did not. Only the build's inputs count (not tests or e2e), so editing a test never forces a rebuild.
 *
 * Dot-files are skipped. sha256 over, for each input file in path order: `<relative posix path>\0<sha256 hex of its bytes>\n`.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

export const INPUT_FILES = ["index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts"];
export const INPUT_DIRS = ["src", "scripts", "public"];

function walk(root: string, dir: string, out: string[]): void {
  let names: string[];
  try {
    names = readdirSync(join(root, dir));
  } catch {
    return;
  }
  for (const name of names) {
    if (name.startsWith(".")) continue; // editor and OS litter is not an input
    const rel = dir ? `${dir}/${name}` : name;
    if (statSync(join(root, rel)).isDirectory()) walk(root, rel, out);
    else out.push(rel);
  }
}

export function inputFiles(root: string): string[] {
  const found: string[] = [];
  for (const f of INPUT_FILES) {
    try {
      if (statSync(join(root, f)).isFile()) found.push(f);
    } catch {
      /* absent: not an input */
    }
  }
  for (const d of INPUT_DIRS) walk(root, d, found);
  return found.map((f) => relative(root, join(root, f)).split(sep).join("/")).sort();
}

export function sourceHash(root: string): string {
  const all = createHash("sha256");
  for (const rel of inputFiles(root)) {
    const digest = createHash("sha256").update(readFileSync(join(root, rel))).digest("hex");
    all.update(`${rel}\0${digest}\n`);
  }
  return all.digest("hex");
}
