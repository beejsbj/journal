import { dayLabel, parseDay } from "../format";
import type { WeekDay } from "../types";
import "./WeekStrip.css";

const STATE_WORDS: Record<WeekDay["state"], string> = {
  answered: "answered",
  partial: "partly answered",
  missed: "missed",
  none: "nothing sent",
};

/**
 * Seven days from the ledger, oldest first: the weekday, the day in the book's figures, and a mark under it in his ink
 * (answered solid, partly half-filled, missed a ring, nothing sent a dash: no colour says "missed"). Each links to that
 * day's entries; today stands on a raised ground.
 */
export function WeekStrip({ week, today }: { week: WeekDay[]; today: string }) {
  return (
    <nav class="week-strip" aria-label="This week">
      <ol>
        {week.map((d, i) => (
          <li key={d.date} class="rise" style={{ "--i": i }}>
            <a
              href={`/entries?day=${d.date}`}
              class={`day ${d.state}${d.date === today ? " today" : ""}`}
              aria-current={d.date === today ? "date" : undefined}
              title={`${dayLabel(d.date)}: ${STATE_WORDS[d.state]}`}
              aria-label={`${dayLabel(d.date)}: ${STATE_WORDS[d.state]}`}
            >
              <span class="dow">{d.weekday.slice(0, 3)}</span>
              <span class="dot" />
              <span class="dom">{parseDay(d.date)?.d ?? ""}</span>
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
