import { describe, expect, it, vi } from "vitest";
import { useResource } from "../src/hooks";
import { Await } from "../src/components/Status";
import { ApiError } from "../src/api";
import { json, mount, must, stubFetch, until } from "./helpers";
import { api } from "../src/api";

function Probe() {
  const [res, reload] = useResource((signal) => api.settings(signal), []);
  return (
    <Await res={res} reload={reload}>
      {() => <p id="ok">loaded</p>}
    </Await>
  );
}

describe("a failed read says so, with Retry", () => {
  it("shows the server's line and a Retry that asks again", async () => {
    let calls = 0;
    stubFetch({
      "/api/v1/settings": () => {
        calls += 1;
        return calls === 1 ? json({ error: "engine-unavailable", detail: "config is unusable" }, 503) : { settings: {}, origins: {} };
      },
    });
    const host = mount(<Probe />);
    const alert = await until(() => must(host.querySelector('[role="alert"]')));
    expect(alert.textContent).toContain("config is unusable");
    expect(host.querySelector("#ok")).toBeNull();      // no stale answer is shown as current
    must(alert.querySelector<HTMLElement>("button")).click();
    await until(() => must(host.querySelector("#ok")));
    expect(calls).toBe(2);
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it("says the server cannot be reached when the network is down", async () => {
    const f = stubFetch({});
    f.fn.mockRejectedValue(new TypeError("Failed to fetch"));
    const host = mount(<Probe />);
    const alert = await until(() => must(host.querySelector('[role="alert"]')));
    expect(alert.textContent).toContain("Can't reach the journal server");
  });

  it("shows skeletons while the read is out", () => {
    vi.stubGlobal("fetch", () => new Promise(() => undefined));
    const host = mount(<Probe />);
    expect(host.querySelector('[role="status"]')).not.toBeNull();
  });
});

describe("ApiError", () => {
  it("is unpaired only for a 401", () => {
    expect(new ApiError(401, "token", "").unpaired).toBe(true);
    expect(new ApiError(403, "host", "").unpaired).toBe(false);
  });
});
