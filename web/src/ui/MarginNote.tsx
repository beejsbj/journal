/**
 * Her note beside a passage of his: "Ada · 08:02" with her dot, then her words typed in her ink behind a hairline of
 * it. It stands in the right margin beside the passage it belongs to, and under the passage when the pane is narrow
 * or on a phone (the entry's grid places it; design/DESIGN.md, "The page"). Read-only, in light and in dark alike.
 */
import type { ComponentChildren } from "preact";
import "./MarginNote.css";

export function MarginNote({ who, at, children, class: cls }: { who: string; at?: ComponentChildren; children: ComponentChildren; class?: string }) {
  return (
    <aside class={["margin-note", cls].filter(Boolean).join(" ")} aria-label={`${who}'s note`}>
      <p class="margin-note-lab">
        <span class="her-mark" aria-hidden="true" />
        {who}
        {at && <> · {at}</>}
      </p>
      <div class="margin-note-body her">{children}</div>
    </aside>
  );
}
