/**
 * A short word about something that just happened, with at most two actions (Undo; Later and Reload). The lamp's
 * colours, a pill, rising on the control spring at the foot of a wide screen; on a phone it rides at the top, clear of
 * the tab bar and of anything he is typing into (web/e2e/mobile.spec.ts holds that). `role="status"`: it is read out
 * without taking focus. The parent decides how long it stays (Undo needs ~6 s; an update waits for him).
 */
import type { ComponentChildren } from "preact";
import "./Toast.css";

export function Toast({ children, actions, open = true }: {
  children: ComponentChildren;
  /** Buttons after the message (Button, variant quiet or primary). */
  actions?: ComponentChildren;
  /** Kept mounted and false, it leaves with its exit instead of vanishing. */
  open?: boolean;
}) {
  return (
    <div class="toast" role="status" data-open={open ? "true" : "false"}>
      <span>{children}</span>
      {actions}
    </div>
  );
}
