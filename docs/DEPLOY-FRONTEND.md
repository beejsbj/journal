# Exposing `journal serve` as a private `https://journal.example.com`

How to put the web app on a private name, reachable from your devices and refused from the internet. It is an
operator's task, not code: the server already enforces what it needs (its own auth, the Host and Origin checks,
which proxies it believes), and the steps below only route traffic to it. Names, addresses and the port are
placeholders; a deployment keeps its real values in its own overlay and notes, outside the repo.

## Decisions

1. **Bind a private address, never `0.0.0.0`.** The reverse proxy reaches the server on the host's Tailscale
   address (or another private interface) and the firewall admits only the proxy's subnet to that port.
   `serve.bind` stays `127.0.0.1` by default (dev, tests, the quarantine of step 1); the operator sets the real
   address in the config at deploy time (Decision 8), never in code.
2. **Deny the name publicly before routing it.** If a tunnel or CDN also fronts the host, add the name's public
   `403` before the proxy's router exists, so there is no window in which the router is reachable from outside.
3. **App-level auth stays on** (token + pairing cookie, [API.md](API.md)): a "local network only" rule at the proxy
   usually admits every container on the host and the tunnel too.
4. **A user unit**, rendered by `journal serve --print-unit`, with lingering enabled for the user
   (`loginctl enable-linger`), so it runs without a login session.
5. **Tailscale identity instead of pairing** (optional): `serve.socket` + `serve.tailscale_users`, published with
   `tailscale serve --https=<public port> unix:<socket>`; README *Web app* and DECISIONS.md, TSAUTH, have the details.

## Values

| Item | Example | Where it lives |
| --- | --- | --- |
| Bind / port | `100.64.0.1:8790` | `serve.bind`, `serve.port` in the deployment's config; `127.0.0.1` until the route is ready |
| Canonical host | `journal.example.com` | `serve.host` (Host check, cookie scope) |
| Public port | 443 (no port in URLs) | `serve.public_port`: set it when the app is published on another HTTPS port (`host.tailnet.ts.net:8790` behind `tailscale serve`); pairing links and the write `Origin` then carry exactly that port |
| Trusted proxy | `10.0.0.0/24` | `serve.trusted_proxies`: the reverse proxy's subnet, the only peers whose `X-Forwarded-*` is believed |
| Token | `serve.token_command: ["secret-env", "companion"]`, `serve.token_key: JOURNAL_SERVE_TOKEN` | the deployment's overlay; the secret itself in your secret store |
| Unit | `~/.config/systemd/user/journal-serve.service` | rendered by `journal serve --print-unit` |

## Steps

**1. Install and run on loopback first (quarantine).**

```sh
uv tool install --force 'journal[hermes,mcp] @ git+https://github.com/beejsbj/journal@<sha>'   # same sha as the plugin pin
CONF="$HOME/.config/journal/companion.json"; HH="$HOME/.hermes/profiles/companion"   # your config and profile
journal --config "$CONF" --hermes-home "$HH" serve check
U=~/.config/systemd/user/journal-serve.service
journal --config "$CONF" --hermes-home "$HH" serve --print-unit > "$U.new" && mv "$U.new" "$U"   # review it first
loginctl enable-linger "$USER"   # the user manager, and so the unit, runs without a login session
systemctl --user daemon-reload && systemctl --user enable --now journal-serve
curl -sS http://127.0.0.1:8790/healthz
```

The rendered unit is [deploy/journal-serve.service.example](../deploy/journal-serve.service.example) with your
paths. It has no filesystem sandbox (DECISIONS.md, SERVE-UNSANDBOXED): under a user manager `PrivateTmp`,
`ProtectSystem`, `ProtectHome` and `ReadWritePaths` put the service in a user namespace where tailscaled (root)
reads as uid 65534, so Tailscale identity sign-in fails (#49). The server writes `root`, `state_dir`,
`practices_dir` and the config file's directory as the service user, under ordinary file permissions; it never needs
`state.db`. The mounts of those paths are gated (`RequiresMountsFor`, `mountpoint -q`). The unit leaves out
`After=tailscaled.service` (a user unit cannot order itself after a system unit); `Restart=on-failure` plus the
server's 60-second bind retry cover a Tailscale address that is not up at boot. Memory follows Decision 7
(`384M`/`768M`, revised from measured peaks).

**2. Firewall.** Admit the proxy's subnet to the bound address and port only, then set `serve.bind` and restart the
unit. Prove the proxy reaches `/healthz` and that another LAN host cannot reach the port directly. Do not widen the
source to a whole private range or rebind to `0.0.0.0`.

**3. Back up every file you are about to change** (proxy routes, tunnel config, local DNS), with a dated suffix.

**4. Public deny first** (Decision 2): in the tunnel's ingress, the name answers `http_status:403`, placed before any
wildcard entry; validate the ingress before restarting the tunnel.

**5. The proxy's routers:** an HTTPS router for `Host(journal.example.com)` with the "local network only"
middleware, an HTTP router that redirects to HTTPS, and a service pointing at `http://<bound address>:<port>`. No
compression middleware on this router (see *Constraints*, SSE). Use the wildcard certificate the proxy already has
for private names rather than an HTTP-01 resolver, which cannot pass a `403` boundary.

**6. Local DNS:** a rewrite of `journal.example.com` to the host's LAN address, and the tailnet's DNS answering the
same, so the name resolves on the LAN and over Tailscale.

**7. Verify:**

```sh
dig journal.example.com +short                                                                             # the host
curl -sS -o /dev/null -w '%{http_code} verify=%{ssl_verify_result}\n' https://journal.example.com/healthz    # 200 verify=0
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' http://journal.example.com                        # 301/308 → https
curl -sS -o /dev/null -w '%{http_code}\n' https://journal.example.com/api/v1/today                           # 401 (token required)
# from outside: the public path answers 403
# a real client: pair a phone over Tailscale and over the LAN; install the PWA; open an entry; post a note
```

Clients can hold a stale answer after a DNS rewrite: reconnect or flush DNS before debugging the proxy.

## Constraints the API and PWA already honour

- **Trust:** the proxy is the only network gate, and a "local network only" rule admits containers and the tunnel;
  the app enforces its own token (API.md Decision 5). The app sees the proxy as the peer; `X-Forwarded-*` is
  believed only from `serve.trusted_proxies`. `Host` is checked against `serve.host`; `Origin` on writes.
- **No content in logs:** content stays out of URLs, and bodies are never logged; leave the proxy's access log off
  or free of query strings.
- **Routing:** `Host`-based, no path prefix; the PWA is served at `/` with `start_url`/`scope` `/`. Cookies are
  `Secure; HttpOnly; SameSite=Lax`, host-only. A valid certificate means service workers, `getUserMedia` and iOS
  add-to-home-screen run in a secure context. No CORS needed (same origin).
- **Compression and streaming:** `/events` sends `Cache-Control: no-transform`; keep compression off the router, or
  make sure it skips `text/event-stream`. Static assets are fingerprinted by the build.
- **Timeouts and bodies:** a proxy read timeout caps a slow voice upload over a poor link: uploads are capped at
  25 MB and the PWA records in clips of at most 5 minutes; test a real phone upload. The app sets its own body limits
  (1 MB JSON, 25 MB upload). SSE pings every 25 s stay under a typical idle timeout.
- **Process:** the private address may not exist at boot; the server retries binding for 60 s, then exits 1 so the
  unit loops. It works with no model reachable (serve never calls a model), and its children (`journal send`,
  `journal program`) are the same small Python.

## Risk and rollback

| Step | Risk | Rollback |
| --- | --- | --- |
| Unit install/start | Low; a crash-looping unit is invisible | `systemctl --user disable --now journal-serve`; remove the unit |
| Firewall rule | A too-wide source exposes the port to every container | Delete the rule (record the prior rule list first) |
| Public deny + tunnel restart | A restart drops every tunnel-routed name briefly; a deny after a wildcard does nothing | Restore the backup, restart, rerun the public `403` check |
| Proxy routers | A public window if they land before the deny; a config error breaks other routes in the file | Restore the dated backup |
| Local DNS | A bad edit breaks LAN DNS | Restore the backup, prove `dig` for another name |

Roll back in the reverse order of the steps; rerun the public `403` check after each restore.
