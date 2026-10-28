import { useState } from "preact/hooks";
import { useUpdate } from "../pwa";

/**
 * A new build is installed and waiting: say so, and let the person choose the moment (the page reloads, which would
 * end a recording in progress). "Later" puts it away until the next load. On a phone it rides at the top, clear of the
 * sticky composer and the bottom bar, whose buttons it would otherwise cover.
 */
export function UpdateToast() {
  const apply = useUpdate();
  const [later, setLater] = useState(false);
  if (!apply || later) return null;
  return (
    <div class="toast" role="status">
      <span>Update available</span>
      <button type="button" class="button" onClick={() => setLater(true)}>
        Later
      </button>
      <button type="button" class="button primary" onClick={apply}>
        Reload
      </button>
    </div>
  );
}
