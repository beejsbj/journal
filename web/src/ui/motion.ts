/** Whether to move at all: the reader's setting (prefers-reduced-motion). Instant when it is set; tokens.css makes every
 *  duration 1 ms then too, so CSS and script agree. */
export function reducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}
