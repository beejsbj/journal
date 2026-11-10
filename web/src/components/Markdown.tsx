import { Fragment } from "preact";
import { parseMarkdown, type Inline, type MdBlock } from "../markdown";
import "./Markdown.css";

function Run({ parts }: { parts: Inline[] }) {
  return (
    <>
      {parts.map((p, i) =>
        p.bold ? <strong key={i}>{p.text}</strong> : p.italic ? <em key={i}>{p.text}</em> : <Fragment key={i}>{p.text}</Fragment>,
      )}
    </>
  );
}

function Blocks({ blocks }: { blocks: MdBlock[] }) {
  return (
    <>
      {blocks.map((b, i) =>
        b.type === "p" ? (
          <p key={i}>
            <Run parts={b.inline} />
          </p>
        ) : b.type === "quote" ? (
          <blockquote key={i}>
            <Blocks blocks={b.blocks} />
          </blockquote>
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
    </>
  );
}

/** The companion's Markdown through the safe renderer: elements are built from data, never from an HTML string. */
export function Markdown({ text }: { text: string }) {
  return (
    <div class="md">
      <Blocks blocks={parseMarkdown(text)} />
    </div>
  );
}
