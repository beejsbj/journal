import { describe, expect, it } from "vitest";
import { addDays, cadence, clip, dayLabel, duration, longDay, parseDay, plainTitle, weekHeader, weekRange, weekStart, weekdayIndex } from "../src/format";

describe("days are calendar days", () => {
  it("labels", () => {
    expect(dayLabel("2026-10-14")).toBe("Wed 14 Oct");
    expect(longDay("2026-10-14")).toBe("Wednesday 14 October 2026");
    expect(dayLabel("nonsense")).toBe("nonsense");
    expect(parseDay("2026-02-30")).toBeNull();
  });

  it("weeks start on Monday", () => {
    expect(weekdayIndex("2026-10-12")).toBe(0);
    expect(weekdayIndex("2026-10-18")).toBe(6);
    expect(weekStart("2026-10-18")).toBe("2026-10-12");
    expect(weekHeader("2026-10-14")).toBe("Week of 12 Oct");
    expect(weekRange("2026-10-18")).toBe("12 – 18 Oct");
    expect(weekRange("2026-11-01")).toBe("26 Oct – 1 Nov");
  });

  it("adds days across months and years", () => {
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(addDays("2026-01-01", -1)).toBe("2025-12-31");
  });
});

describe("labels", () => {
  it("cadence names the schedule", () => {
    expect(cadence([])).toBe("On demand");
    expect(cadence([{ time: "23:00", weekday: null }])).toBe("Daily 23:00");
    expect(cadence([{ time: "12:00", weekday: 2 }])).toBe("Wed 12:00");
    expect(cadence([{ time: "08:00", weekday: null }, { time: "20:30", weekday: 6 }])).toBe("Daily 08:00, Sun 20:30");
  });

  it("titles lose the date the engine appends", () => {
    expect(plainTitle("Evening journal — Wed 14 Oct 2026")).toBe("Evening journal");
    expect(plainTitle("Fix your life · Session 1 — Thu 15 Oct 2026")).toBe("Fix your life · Session 1");
    expect(plainTitle("Plain")).toBe("Plain");
    expect(plainTitle("Mind — Body — Wed 14 Oct 2026")).toBe("Mind — Body");
    expect(plainTitle("Mind — Body")).toBe("Mind — Body");
  });

  it("clips on a word and says so", () => {
    expect(clip("short", 20)).toBe("short");
    expect(clip("one two three four five six", 15)).toBe("one two three…");
    expect(clip("line one\n\nline two", 100)).toBe("line one line two");
  });

  it("durations", () => {
    expect(duration(151)).toBe("2:31");
    expect(duration(3723)).toBe("1:02:03");
  });
});
