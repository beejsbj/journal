import { describe, expect, it } from "vitest";
import { FIELDS, buildPatch, keyOf, problem, shown, valueAt } from "../src/settingsModel";
import { layerNote, lockedWhy } from "../src/routes/settings";

const settings = { windows: { collapse_hours: 6 }, today: { enabled: true }, location: "Hillside" };
const field = (key: string) => FIELDS.find((f) => keyOf(f) === key)!;

describe("the settings the web app writes", () => {
  it("reads a value from the tree by its path", () => {
    expect(valueAt(settings, ["windows", "collapse_hours"])).toBe(6);
    expect(valueAt(settings, ["reflections", "day", "enabled"])).toBeUndefined();
    expect(valueAt({ today: false }, ["today", "enabled"])).toBeUndefined();   // `today: false` is the block off, as a bare flag
  });

  it("sends nothing for what was not changed, and a nested object for what was", () => {
    expect(buildPatch(settings, {})).toEqual({});
    expect(buildPatch(settings, { "windows.collapse_hours": "6" })).toEqual({});
    expect(buildPatch(settings, { "windows.collapse_hours": "8", "reflections.day.enabled": "off", "reflections.day.fallback_time": "22:30" })).toEqual({
      windows: { collapse_hours: 8 },
      reflections: { day: { enabled: false, fallback_time: "22:30" } },
    });
  });

  it("leaves an emptied box alone: the web app has no way to unset", () => {
    expect(buildPatch(settings, { "windows.collapse_hours": "", location: "" })).toEqual({});
  });

  it("shows a flag as on, off or default, since the tree does not hold defaults", () => {
    expect(shown(field("today.enabled"), true)).toBe("on");
    expect(shown(field("today.enabled"), false)).toBe("off");
    expect(shown(field("today.enabled"), undefined)).toBe("");
  });

  it("names a value that is out of range before it is sent", () => {
    expect(problem(field("windows.collapse_hours"), "100")).toBe("At most 72.");
    expect(problem(field("windows.collapse_hours"), "0.1")).toBe("At least 0.25.");
    expect(problem(field("windows.opener_budget_seconds"), "2.5")).toBe("A whole number.");
    expect(problem(field("reflections.day.fallback_time"), "9pm")).toBe("A time like 21:30.");
    expect(problem(field("windows.collapse_hours"), "")).toBeNull();
    expect(problem(field("windows.collapse_hours"), "soon")).toBe("A number.");
  });

  it("does not offer 0 minutes where the server refuses it (its minimum is exclusive)", () => {
    for (const key of ["conversations.quiet_minutes", "programs.settle_minutes", "today.cache_minutes"]) {
      expect(problem(field(key), "0"), key).toBe("More than 0.");
      expect(problem(field(key), "0.5"), key).toBeNull();
      expect(problem(field(key), "2000"), key).toBe("At most 1440.");
    }
    expect(problem(field("windows.annotate_after_minutes"), "0")).toBeNull();   // that one takes 0
  });

  it("locks what Hermes or the environment owns, and says how to change it", () => {
    const f = field("windows.collapse_hours");
    expect(lockedWhy(f, { windows: "file" })).toBeNull();
    expect(lockedWhy(f, {})).toBeNull();
    expect(lockedWhy(f, { windows: "env" })).toContain("environment");
    expect(lockedWhy(f, { windows: "hermes" })).toContain("hermes config set plugins.entries.journal.settings.windows.collapse_hours");
    expect(lockedWhy(f, { windows: "hermes" }, "iris")).toContain("hermes -p iris config set plugins.entries.journal.settings.windows.collapse_hours");
  });

  it("says which layer a saveable field is in", () => {
    const f = field("windows.collapse_hours");
    expect(layerNote(f, { windows: "file" })).toBe("In the config file.");
    expect(layerNote(f, {})).toContain("default applies");
    expect(layerNote(f, { windows: "default" })).toContain("config file");
  });
});
