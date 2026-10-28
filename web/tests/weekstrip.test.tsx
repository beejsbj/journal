import { describe, expect, it } from "vitest";
import { WeekStrip } from "../src/components/WeekStrip";
import { Today } from "../src/types";
import { fixtures, mount } from "./helpers";

const today = Today(fixtures.today);

describe("WeekStrip from today.week", () => {
  it("draws seven days, oldest first, each a link to that day's entries", () => {
    const host = mount(<WeekStrip week={today.week} today={today.date} />);
    const days = [...host.querySelectorAll("a.day")];
    expect(days).toHaveLength(7);
    expect(days.map((d) => d.getAttribute("href"))).toEqual(today.week.map((d) => `/entries?day=${d.date}`));
    expect(days[0]?.querySelector(".dom")?.textContent).toBe("9");
  });

  it("marks answered, nothing and today, and says each in words", () => {
    const host = mount(<WeekStrip week={today.week} today={today.date} />);
    const wed = host.querySelector('a[href="/entries?day=2026-10-14"]');
    expect(wed?.classList.contains("answered")).toBe(true);
    expect(wed?.getAttribute("aria-label")).toBe("Wed 14 Oct: answered");
    const thu = host.querySelector('a[href="/entries?day=2026-10-15"]');
    expect(thu?.classList.contains("today")).toBe(true);
    expect(thu?.getAttribute("aria-current")).toBe("date");
    expect(thu?.getAttribute("aria-label")).toBe("Thu 15 Oct: nothing sent");
  });

  it("tells partly answered and missed apart", () => {
    const week = [
      { date: "2026-10-12", weekday: "Mon", answered: 1, missed: 1, state: "partial" as const },
      { date: "2026-10-13", weekday: "Tue", answered: 0, missed: 1, state: "missed" as const },
    ];
    const host = mount(<WeekStrip week={week} today="2026-10-13" />);
    expect(host.querySelector("a.partial")?.getAttribute("aria-label")).toBe("Mon 12 Oct: partly answered");
    expect(host.querySelector("a.missed")?.getAttribute("aria-label")).toBe("Tue 13 Oct: missed");
  });
});
