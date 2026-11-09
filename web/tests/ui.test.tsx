import { useState } from "preact/hooks";
import { describe, expect, it, vi } from "vitest";
import { Calendar, Disclosure, Letter, Menu, Segmented, Sheet, Stepper, Switch, TextField } from "../src/ui";
import { useShellMode } from "../src/shell/mode";
import { App } from "../src/app";
import { click, mount, must, stubFetch, tick, until } from "./helpers";

const key = async (el: EventTarget | null | undefined, k: string, opts: KeyboardEventInit = {}) => {
  must(el).dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, ...opts }));
  await tick();
};

describe("the design system's primitives", () => {
  it("Switch is a switch: its state is aria-checked and a press flips it", async () => {
    const seen: boolean[] = [];
    function S() {
      const [on, set] = useState(false);
      return <Switch label="Today block" checked={on} onChange={(v) => { seen.push(v); set(v); }} />;
    }
    const host = mount(<S />);
    const sw = must(host.querySelector('[role="switch"]'));
    expect(sw.getAttribute("aria-checked")).toBe("false");
    expect(sw.getAttribute("aria-label")).toBe("Today block");
    await click(sw);
    expect(sw.getAttribute("aria-checked")).toBe("true");
    expect(seen).toEqual([true]);
  });

  it("Segmented is a radio group with one tab stop; the arrows move the choice", async () => {
    function S() {
      const [v, set] = useState<"a" | "b" | "c">("a");
      return <Segmented label="Page" value={v} onChange={set} options={[{ value: "a", label: "System" }, { value: "b", label: "Paper" }, { value: "c", label: "Night" }]} />;
    }
    const host = mount(<S />);
    const group = must(host.querySelector('[role="radiogroup"]'));
    const radios = () => [...host.querySelectorAll('[role="radio"]')];
    expect(radios().map((r) => r.getAttribute("tabindex"))).toEqual(["0", "-1", "-1"]);
    await key(group.querySelector('[aria-checked="true"]'), "ArrowRight");
    expect(radios().map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "true", "false"]);
    await key(group.querySelector('[aria-checked="true"]'), "End");
    expect(radios()[2]?.getAttribute("aria-checked")).toBe("true");
    await key(group.querySelector('[aria-checked="true"]'), "ArrowRight");   // round to the first
    expect(radios()[0]?.getAttribute("aria-checked")).toBe("true");
  });

  it("Stepper moves by its step, stops at its ends, and its value is a spinbutton", async () => {
    function S() {
      const [v, set] = useState(2);
      return <Stepper label="Hours" value={v} onChange={set} min={1} max={3} unit="hours" />;
    }
    const host = mount(<S />);
    const spin = must(host.querySelector('[role="spinbutton"]'));
    const [less, more] = [...host.querySelectorAll("button")] as [HTMLButtonElement, HTMLButtonElement];
    expect(spin.getAttribute("aria-valuetext")).toBe("2 hours");
    await click(more);
    expect(spin.getAttribute("aria-valuenow")).toBe("3");
    expect(more.disabled).toBe(true);
    await key(spin, "Home");
    expect(spin.getAttribute("aria-valuenow")).toBe("1");
    expect(less.disabled).toBe(true);
    await key(spin, "ArrowUp");
    expect(spin.getAttribute("aria-valuenow")).toBe("2");
  });

  it("Disclosure says whether it is open, and its closed body is inert", async () => {
    const host = mount(<Disclosure head="Her reflection" peek="To carry forward">Her words.</Disclosure>);
    const head = must(host.querySelector<HTMLButtonElement>(".disclosure-head"));
    const body = must(document.getElementById(must(head.getAttribute("aria-controls"))));
    expect(head.getAttribute("aria-expanded")).toBe("false");
    expect(body.hasAttribute("inert")).toBe(true);
    await click(head);
    expect(head.getAttribute("aria-expanded")).toBe("true");
    expect(body.hasAttribute("inert")).toBe(false);
    expect(host.querySelector(".disclosure")?.getAttribute("data-open")).toBe("true");
  });

  it("TextField ties its label, hint and error to the box", () => {
    const host = mount(<TextField id="tz" label="Time zone" hint="An IANA name." error="Not a zone." />);
    const input = must(host.querySelector("input"));
    expect(host.querySelector('label[for="tz"]')?.textContent).toBe("Time zone");
    expect(input.getAttribute("aria-describedby")).toBe("tz-hint tz-error");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(host.querySelector("#tz-error")?.getAttribute("role")).toBe("alert");
  });

  it("Menu opens from its trigger, the arrows move through it, Escape closes it back to the trigger", async () => {
    const picked = vi.fn();
    const host = mount(
      <Menu label="Which entries" value="all" onSelect={picked} items={[{ value: "all", label: "every entry" }, { value: "audio", label: "with your voice", count: 3 }]}>
        every entry
      </Menu>,
    );
    const trigger = must(host.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]'));
    await click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const items = () => [...host.querySelectorAll<HTMLElement>('[role="menuitemradio"]')];
    expect(items().map((i) => i.getAttribute("aria-checked"))).toEqual(["true", "false"]);
    await until(() => expect(document.activeElement).toBe(items()[0]));
    await key(document.activeElement, "ArrowDown");
    expect(document.activeElement).toBe(items()[1]);
    await click(items()[1]);
    expect(picked).toHaveBeenCalledWith("audio");
    expect(host.querySelector('[role="menu"]')).toBeNull();
    await click(trigger);
    await key(host.querySelector('[role="menu"]'), "Escape");
    expect(host.querySelector('[role="menu"]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("Sheet is a modal dialog: Tab goes round inside it, Escape closes it, focus goes back", async () => {
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();
    function S() {
      const [open, setOpen] = useState(true);
      return open ? (
        <Sheet title="Mood, people, star" onClose={() => setOpen(false)}>
          <input aria-label="Mood" />
          <button type="button">Save</button>
        </Sheet>
      ) : null;
    }
    const host = mount(<S />);
    const dialog = must(host.querySelector('[role="dialog"]'));
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("Mood, people, star");
    const save = must([...dialog.querySelectorAll("button")].find((b) => b.textContent === "Save"));
    save.focus();
    await key(save, "Tab");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Close");   // round to the first control
    await key(document.activeElement, "Tab", { shiftKey: true });
    expect(document.activeElement).toBe(save);
    await key(document, "Escape");
    await until(() => expect(host.querySelector('[role="dialog"]')).toBeNull());
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });

  it("Calendar makes the written days buttons with one tab stop, and the arrows walk them", async () => {
    const chose = vi.fn();
    const host = mount(<Calendar month="2026-10" marked={["2026-10-05", "2026-10-12", "2026-10-14"]} today="2026-10-15" selected="2026-10-12" onSelect={chose} />);
    const days = [...host.querySelectorAll<HTMLButtonElement>("button[data-day]")];
    expect(days.map((d) => d.dataset.day)).toEqual(["2026-10-05", "2026-10-12", "2026-10-14"]);
    expect(days.map((d) => d.tabIndex)).toEqual([-1, 0, -1]);
    expect(days[1]?.getAttribute("aria-pressed")).toBe("true");
    expect(days[2]?.getAttribute("aria-label")).toBe("Wednesday 14 October, written");
    expect(host.querySelector('[aria-current="date"]')?.textContent).toBe("15");
    days[1]?.focus();
    await key(days[1], "ArrowRight");
    expect(document.activeElement).toBe(days[2]);
    await key(days[2], "ArrowUp");
    expect(document.activeElement).toBe(days[0]);   // a week up from the 14th: the nearest written day on or before the 7th
    await click(days[1]);
    expect(chose).toHaveBeenCalledWith(null);   // the chosen day again clears it
  });

  it("Letter is hers: from her, the question asked, the rest folded, nothing to edit", () => {
    const host = mount(
      <Letter from="Ada" at="23:00" opener={"Evening.\n\nLet the day land."} asked={["What went well?"]} askedIds={["2.1"]}
        questions={[{ id: "1.1", text: "What's on your mind?", section: "One" }, { id: "2.1", text: "What went well?", section: "Two" }]} />,
    );
    const letter = must(host.querySelector(".letter"));
    expect(letter.getAttribute("aria-label")).toBe("From Ada");
    expect(host.querySelector(".letter-from")?.textContent).toBe("From Adasent 23:00");
    expect([...host.querySelectorAll(".letter-body p")].map((p) => p.textContent)).toEqual(["Evening.", "Let the day land.", "What went well?"]);
    expect(host.querySelector(".letter-questions .disclosure-head")?.textContent).toContain("All 2 questions, in 2 parts");
    expect(host.querySelector("li.asked .letter-qt")?.textContent).toBe("What went well? asked");
    expect(host.querySelector("textarea, input, [contenteditable]")).toBeNull();
  });
});

describe("the shell", () => {
  it("folds the rail while a route asks, and lets go when it goes", async () => {
    stubFetch({});
    function Asks() {
      useShellMode({ fold: true });
      return null;
    }
    const host = mount(<App />);
    const shell = await until(() => must(host.querySelector(".shell")));
    expect(shell.getAttribute("data-rail")).toBe("full");
    const extra = mount(<Asks />);
    await until(() => expect(shell.getAttribute("data-rail")).toBe("compact"));
    extra.remove();
    const { render } = await import("preact");
    render(null, extra);
    await until(() => expect(shell.getAttribute("data-rail")).toBe("full"));
  });

  it("has one Write in the tab bar and one in the rail, both opening the write surface", async () => {
    stubFetch({});
    const host = mount(<App />);
    await until(() => must(host.querySelector("button.plus")));
    expect(host.querySelector("button.plus")?.textContent).toBe("Write");
    expect(host.querySelector(".rail-write")?.textContent).toBe("WriteN");
    expect(host.querySelector(".rail-write kbd")?.getAttribute("aria-hidden")).toBe("true");
    await click(host.querySelector(".rail-write"));
    await until(() => must(host.querySelector('[role="dialog"]')));
  });
});
