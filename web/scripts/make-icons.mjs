/**
 * Make the PNG icons from the SVG mark: `npm run icons` (needs `@resvg/resvg-js`, a dev dependency). The PNGs are
 * committed beside the mark (`public/icons/`), so the build never needs this and a CI run does not rasterise anything;
 * run it when the mark changes. The mark's colours are the paper (`--bg`), his ink (`--ink`) and the default ink of hers
 * (`--accent`, blue-black) of `tokens.css`: an installed icon cannot follow the ink a device chose.
 *
 * - `icon-192.png`, `icon-512.png`: the mark as it is (the browser rounds or masks them as it likes).
 * - `icon-maskable-512.png`: the mark scaled into the middle 72% (Android masks to a circle at worst; the safe
 *   zone is the middle 80%), on the same paper.
 * - `apple-touch-icon.png` (180): iOS wants an opaque square and rounds it itself.
 */
import { Resvg } from "@resvg/resvg-js";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const dir = fileURLToPath(new URL("../public/icons/", import.meta.url));
const mark = readFileSync(`${dir}mark.svg`, "utf-8");

const maskable = mark.replace('<g id="mark"', '<g id="mark" transform="translate(256 256) scale(0.72) translate(-256 -256)"');
if (maskable === mark) throw new Error('mark.svg has no <g id="mark">');

function png(svg, size) {
  return new Resvg(svg, { fitTo: { mode: "width", value: size } }).render().asPng();
}

writeFileSync(`${dir}icon-192.png`, png(mark, 192));
writeFileSync(`${dir}icon-512.png`, png(mark, 512));
writeFileSync(`${dir}icon-maskable-512.png`, png(maskable, 512));
writeFileSync(`${dir}apple-touch-icon.png`, png(mark, 180));
console.log("icons written to", dir);
