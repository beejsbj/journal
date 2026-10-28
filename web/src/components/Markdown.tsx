import { Fragment } from "preact";
import { parseMarkdown, type Inline } from "../markdown";

function Run({ parts }: { parts: Inline[] }) {
  return (
    <>
      {parts.map((p, i) =>
        p.bold ? <strong key={i}>{p.text}</strong> : p.italic ? <em key={i}>{p.text}</em> : <Fragment key={i}>{p.text}</Fragment>,
      )}
    </>
  );
}

/** The companion's Markdown through the safe renderer: elements are built from data, never from an HTML string. */
export function Markdown({ text }: { text: string }) {
  return (
    <div class="md">
      {parseMarkdown(text).map((b, i) =>
        b.type === "p" ? (
          <p key={i}>
            <Run parts={b.inline} />
          </p>
        ) : (
          <ul key={i}>
            {b.items.map((item, j) => (
              <li key={j}>
                <Run parts={item} />
              </li>
            ))}
          </ul>
        ),
      )}
    </div>
  );
}
