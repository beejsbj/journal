import { dayLabel, parseDay } from "../format";
import type { WeekDay } from "../types";

const STATE_WORDS: Record<WeekDay["state"], string> = {
  answered: "answered",
  partial: "partly answered",
  missed: "missed",
  none: "nothing sent",
};

/** Seven days from the ledger: answered, partly, missed, or nothing. Each links to that day's entries. */
export function WeekStrip({ week, today }: { week: WeekDay[]; today: string }) {
  return (
    <nav class="week-strip" aria-label="This week">
      <ol>
        {week.map((d) => (
          <li key={d.date}>
            <a
              href={`/entries?day=${d.date}`}
              class={`day ${d.state}${d.date === today ? " today" : ""}`}
              aria-current={d.date === today ? "date" : undefined}
              title={`${dayLabel(d.date)}: ${STATE_WORDS[d.state]}`}
              aria-label={`${dayLabel(d.date)}: ${STATE_WORDS[d.state]}`}
            >
              <span class="dow">{d.weekday.slice(0, 1)}</span>
              <span class="dot" />
              <span class="dom">{parseDay(d.date)?.d ?? ""}</span>
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
