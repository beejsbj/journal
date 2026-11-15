import { useState } from "preact/hooks";
import { useUpdate } from "../pwa";
import { Button } from "../ui/Button";
import { Toast } from "../ui/Toast";

/**
 * A new build is installed and waiting: say so, and let the person choose the moment (the page reloads, which would
 * end a recording in progress). "Later" puts it away until the next load. On a phone it rides at the top, clear of the
 * tab bar and of anything being typed (ui/Toast.css).
 */
export function UpdateToast() {
  const apply = useUpdate();
  const [later, setLater] = useState(false);
  if (!apply || later) return null;
  return (
    <Toast
      actions={
        <>
          <Button size="small" onClick={() => setLater(true)}>
            Later
          </Button>
          <Button size="small" variant="primary" onClick={apply}>
            Reload
          </Button>
        </>
      }
    >
      Update available
    </Toast>
  );
}
