/**
 * The way back: "‹ Library", "‹ Entries", "‹ Today". One look on every page that is deeper than a place (a practice,
 * the editor, a run, an entry): a quiet link in the sans with a chevron, 44 px tall, the page's pop transition.
 * `BackLink` is the link alone (for a bar, as the entry's); `PageBack` sets it on its own line at the head of a page.
 */
import type { ComponentChildren } from "preact";
import { ChevronLeft } from "../icons";
import "./BackLink.css";

export function BackLink({ href, children, class: cls }: { href: string; children: ComponentChildren; class?: string }) {
  return (
    <a class={["back-link", cls].filter(Boolean).join(" ")} href={href} data-vt="pop">
      <ChevronLeft aria-hidden="true" />
      {children}
    </a>
  );
}

export function PageBack({ href, children }: { href: string; children: ComponentChildren }) {
  return (
    <div class="page-back">
      <BackLink href={href}>{children}</BackLink>
    </div>
  );
}
