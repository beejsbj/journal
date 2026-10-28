import { ribbonText, useStaleAsOf } from "../offline";

/** "offline, as of 09:12" under the bar while the view on screen was read from the worker's kept copy. */
export function OfflineRibbon() {
  const at = useStaleAsOf();
  if (at === null) return null;
  return (
    <p class="ribbon" role="status">
      {ribbonText(at)}
    </p>
  );
}
