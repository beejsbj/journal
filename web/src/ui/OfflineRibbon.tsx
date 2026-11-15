import { Offline } from "../icons";
import { ribbonText, useStaleAsOf } from "../offline";
import "./OfflineRibbon.css";

/** "offline, as of 09:12" at the head of the page while the view on screen was read from the worker's kept copy. */
export function OfflineRibbon() {
  const at = useStaleAsOf();
  if (at === null) return null;
  return (
    <p class="ribbon" role="status">
      <Offline aria-hidden="true" />
      <span>{ribbonText(at)}</span>
    </p>
  );
}
