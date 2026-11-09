/**
 * When a practice comes, as a small clock face (Night Room's, for the Library rows): the hand at its hour, the face
 * shaded for the evening and the night, the weekday in the face for a weekly one, a dashed rim for "when you need one",
 * and for a program a progress arc with "2/10". Decorative: the row says the same in words.
 */
import "./Clock.css";

export function Clock({ at, weekday, progress, size = 40 }: {
  /** "HH:MM"; none for a practice he starts himself. */
  at?: string;
  weekday?: string;
  progress?: { done: number; total: number };
  size?: number;
}) {
  if (progress) {
    const c = 2 * Math.PI * 15;
    const f = progress.total ? progress.done / progress.total : 0;
    return (
      <svg class="clock" viewBox="0 0 40 40" width={size} height={size} aria-hidden="true">
        <circle class="rim" cx="20" cy="20" r="15" />
        <circle class="arc" cx="20" cy="20" r="15" stroke-dasharray={`${(c * f).toFixed(1)} ${c.toFixed(1)}`} />
        <text x="20" y="23">{progress.done}/{progress.total}</text>
      </svg>
    );
  }
  if (!at) {
    return (
      <svg class="clock" viewBox="0 0 40 40" width={size} height={size} aria-hidden="true">
        <circle class="rim dash" cx="20" cy="20" r="15" />
        <circle class="hub" cx="20" cy="20" r="2" />
      </svg>
    );
  }
  const [h, m] = at.split(":").map(Number) as [number, number];
  const a = (((h % 12) + m / 60) / 12) * 2 * Math.PI - Math.PI / 2;
  const x = 20 + Math.cos(a) * 10;
  const y = 20 + Math.sin(a) * 10;
  const night = h >= 18 || h < 6;
  return (
    <svg class="clock" viewBox="0 0 40 40" width={size} height={size} aria-hidden="true">
      <circle class={night ? "rim night" : "rim"} cx="20" cy="20" r="15" />
      {[0, 3, 6, 9].map((k) => {
        const t = (k / 12) * 2 * Math.PI - Math.PI / 2;
        return <circle key={k} class="tick" cx={(20 + Math.cos(t) * 12.5).toFixed(1)} cy={(20 + Math.sin(t) * 12.5).toFixed(1)} r=".9" />;
      })}
      <line x1="20" y1="20" x2={x.toFixed(1)} y2={y.toFixed(1)} />
      <circle class="hub" cx="20" cy="20" r="2" />
      {weekday && <text x="20" y={y > 22 ? 15 : 31}>{weekday}</text>}
    </svg>
  );
}
