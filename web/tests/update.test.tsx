import { afterEach, describe, expect, it, vi } from "vitest";
import { UpdateToast } from "../src/components/UpdateToast";
import { isAuthLost, setAuthLost } from "../src/auth";
import { reloadViews, useLive } from "../src/events";
import { offerUpdate, onWorkerMessage, registerWorker } from "../src/pwa";
import { byText, click, mount, until } from "./helpers";

afterEach(() => {
  offerUpdate(null);
  setAuthLost(false);
});

describe("Update available", () => {
  it("is not shown until a new build is waiting, then offers a reload, and goes when it is taken back", async () => {
    const host = mount(<UpdateToast />);
    expect(host.querySelector(".toast")).toBeNull();
    const apply = vi.fn();
    offerUpdate(apply);
    await until(() => expect(host.querySelector(".toast")?.textContent).toContain("Update available"));
    await click(byText(host, "button", "Reload"));
    expect(apply).toHaveBeenCalledTimes(1);
    offerUpdate(null);
    await until(() => expect(host.querySelector(".toast")).toBeNull());
  });

  it("can be put away with Later, and does not reload", async () => {
    const host = mount(<UpdateToast />);
    const apply = vi.fn();
    offerUpdate(apply);
    await until(() => expect(host.querySelector(".toast")).not.toBeNull());
    await click(byText(host, "button", "Later"));
    await until(() => expect(host.querySelector(".toast")).toBeNull());
    expect(apply).not.toHaveBeenCalled();
  });

  it("learns from the worker that the session ended while a kept copy was shown", () => {
    onWorkerMessage({ type: "SOMETHING_ELSE" });
    onWorkerMessage(undefined);
    expect(isAuthLost()).toBe(false);
    onWorkerMessage({ type: "AUTH_LOST" });
    expect(isAuthLost()).toBe(true);
  });

  it("makes the views that listen read again when the worker takes control for the first time", async () => {
    const reload = vi.fn();
    function Listener() {
      useLive(["tick"], reload);
      return null;
    }
    mount(<Listener />);
    await until(() => {   // the listener subscribes in an effect, after the paint
      reloadViews();
      expect(reload).toHaveBeenCalled();
    });
  });

  it("registers no worker outside a production build (tests, the dev server)", () => {
    const register = vi.fn();
    Object.defineProperty(navigator, "serviceWorker", { value: { register }, configurable: true });
    try {
      registerWorker();
      expect(register).not.toHaveBeenCalled();
    } finally {
      Reflect.deleteProperty(navigator, "serviceWorker");
    }
  });
});
