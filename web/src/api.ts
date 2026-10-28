/**
 * The client for `/api/v1`: GET with a shape check on every answer, and the two non-journal writes (pair, logout).
 * Nothing is cached here: a view that wants fresh data asks again, and the service worker (`sw.ts`, rules in
 * `swCore.ts`) may answer a read view from its kept copy when the network cannot; `get` notes which it was
 * (`offline.ts`) so the page can say "offline, as of 09:12".
 */
import { rememberVia, setAuthLost } from "./auth";
import { OFFLINE_WRITE, isOnline, keptAt, noteRead } from "./offline";
import { ShapeError, arr, obj, str, type Json, type Parser } from "./schema";
import {
  Block202, BlockEdited, BlockRemoved, Created, DoctorRow, EntryDoc, EntryPage, Health, LibraryDefault, LibraryItem, LibraryOne, MetaWritten, Reflection, Run,
  Located, RunOne, Session, Settings, SettingsWritten, Spawned, Status, Today, UploadMade, Validation, Week,
  type RunAction,
} from "./types";

export const BASE = "/api/v1";

/** A read or write that did not succeed: the server's slug and line when it sent them, else what went wrong. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly slug: string,
    readonly detail: string,
    /** Every other field of the server's error body (`problems`, `where`, `run`, `runs`). */
    readonly extra: Record<string, unknown> = {},
  ) {
    super(detail || slug);
    this.name = "ApiError";
  }

  /** What is wrong with a definition, one line each (422 and the validation answer). */
  get problems(): string[] {
    const p = this.extra.problems;
    return Array.isArray(p) ? p.filter((x): x is string => typeof x === "string") : [];
  }

  /** Whether the page should send the reader to pairing. */
  get unpaired(): boolean {
    return this.status === 401;
  }
}

async function failure(res: Response): Promise<ApiError> {
  let slug = "http";
  let detail = `The server answered ${res.status}.`;
  let extra: Record<string, unknown> = {};
  try {
    const body = (await res.json()) as Record<string, unknown>;
    if (typeof body.error === "string") slug = body.error;
    if (typeof body.detail === "string" && body.detail) detail = body.detail;
    extra = { ...body };
    delete extra.error;
    delete extra.detail;
  } catch {
    /* not JSON: the status line stands */
  }
  return new ApiError(res.status, slug, detail, extra);
}

async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(BASE + path, { credentials: "same-origin", cache: "no-store", ...init });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    if (!isOnline()) {
      const reading = (init.method ?? "GET") === "GET";
      throw new ApiError(0, "offline", reading ? "You're offline, and this was not read before." : OFFLINE_WRITE);
    }
    throw new ApiError(0, "network", "The server could not be reached.");
  }
}

export function query(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "" && v !== false) q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** GET `path` and check the answer against `parser`. A 401 marks the browser unpaired; only pairing clears that
 *  (a late answer to a request sent before an unpair must not undo it). */
export async function get<T>(path: string, parser: Parser<T>, signal?: AbortSignal): Promise<T> {
  const res = await send(path, { method: "GET", headers: { Accept: "application/json" }, signal });
  if (res.status === 401) setAuthLost(true);
  if (!res.ok) throw await failure(res);
  noteRead(keptAt(res.headers), path);   // a read from the worker's kept copy puts up the ribbon; one from the server takes it down
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new ApiError(res.status, "shape", "The server answered something that is not JSON.");
  }
  try {
    return parser(body);
  } catch (e) {
    if (e instanceof ShapeError) throw new ApiError(res.status, "shape", `The server's answer has an unexpected shape (${e.message}).`);
    throw e;
  }
}

/** POST, PUT, PATCH or DELETE `path` with a JSON body and check the answer (a 204 is checked as `undefined`). The
 *  browser adds `Origin`, which the server requires on every write. Nothing is retried here: a failed write is shown
 *  where it was made, with its draft, and the person retries. */
async function write<T>(method: string, path: string, body: unknown, parser: Parser<T>): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return finish(await send(path, init), parser);
}

async function finish<T>(res: Response, parser: Parser<T>): Promise<T> {
  if (res.status === 401) setAuthLost(true);
  if (!res.ok) throw await failure(res);
  let parsed: unknown;
  if (res.status !== 204) {
    try {
      parsed = await res.json();
    } catch {
      throw new ApiError(res.status, "shape", "The server answered something that is not JSON.");
    }
  }
  try {
    return parser(parsed);
  } catch (e) {
    if (e instanceof ShapeError) throw new ApiError(res.status, "shape", `The server's answer has an unexpected shape (${e.message}).`);
    throw e;
  }
}

const nothing: Parser<void> = () => undefined;
const enc = encodeURIComponent;

/** What a block or an entry is posted with: text, a kept upload, or both. `client_id` makes a retry the same write. */
export interface Words {
  text?: string;
  upload?: string;
  client_id?: string;
}

/** The block as the person last read it: its words and its time. */
export interface BlockSeen {
  was: string;
  at?: string;
}

/** What the editor sends to save a practice: the file's text, or the form's definition, with the mtime it was read at.
 *  A program or situational definition is saved one file at a time: its text, and (a program) which file it is. */
export type DefinitionBody =
  | { raw: string; file?: string; mtime?: number | null }
  | { definition: Record<string, Json>; mtime?: number | null };

const withMtime = <B extends { mtime?: number | null }>(b: B): B => {
  const { mtime, ...rest } = b;
  return (mtime === null || mtime === undefined ? rest : { ...rest, mtime }) as B;
};

export const api = {
  today: (signal?: AbortSignal) => get("/today", Today, signal),
  entries: (params: Record<string, string | number | boolean | undefined>, signal?: AbortSignal) =>
    get(`/entries${query(params)}`, EntryPage, signal),
  entry: (stem: string, signal?: AbortSignal) => get(`/entries/${encodeURIComponent(stem)}`, EntryDoc, signal),
  reflections: (params: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    get(`/reflections${query(params)}`, arr(Reflection), signal),
  weeks: (params: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    get(`/insights/weeks${query(params)}`, arr(Week), signal),
  library: (signal?: AbortSignal) => get("/library", arr(LibraryItem), signal),
  libraryOne: (id: string, signal?: AbortSignal) => get(`/library/${encodeURIComponent(id)}`, LibraryOne, signal),
  libraryDefault: (id: string, signal?: AbortSignal) => get(`/library/${encodeURIComponent(id)}/default`, LibraryDefault, signal),
  settings: (signal?: AbortSignal) => get("/settings", Settings, signal),
  status: (signal?: AbortSignal) => get("/status", Status, signal),
  doctor: (signal?: AbortSignal) => get("/doctor", arr(DoctorRow), signal),
  health: (signal?: AbortSignal) => get("/healthz", Health, signal),
  runs: (signal?: AbortSignal) => get("/runs", arr(Run), signal),
  run: (id: string, signal?: AbortSignal) => get(`/runs/${enc(id)}`, RunOne, signal),
  runInsights: (id: string, signal?: AbortSignal) => get(`/runs/${enc(id)}/insights`, obj({ stem: str, body: str }), signal),

  // ── writes ──
  createEntry: (body: Words & { practice?: string }) => write("POST", "/entries", body, Created),
  addBlock: (stem: string, body: Words & { phase?: number }) => write("POST", `/entries/${enc(stem)}/blocks`, body, Block202),
  /** Change the words of one block of his. `was` (and `at`) are the block as it was read: the server refuses a block
   *  that reads differently now (409 `changed`) rather than overwrite it. */
  editBlock: (stem: string, key: string, body: BlockSeen & { text: string }) =>
    write("PATCH", `/entries/${enc(stem)}/blocks/${enc(key)}`, body, BlockEdited),
  deleteBlock: (stem: string, key: string, body: BlockSeen) =>
    write("DELETE", `/entries/${enc(stem)}/blocks/${enc(key)}`, body, BlockRemoved),
  patchMeta: (stem: string, body: { mood?: string[]; people?: string[]; starred?: boolean; mtime?: number }) =>
    write("PATCH", `/entries/${enc(stem)}/meta`, body, MetaWritten),
  saveLibrary: (id: string, body: DefinitionBody) => write("PUT", `/library/${enc(id)}`, withMtime(body), LibraryOne),
  validateLibrary: (id: string, body: DefinitionBody) =>
    write("PUT", `/library/${enc(id)}?validate=1`, withMtime(body), Validation),
  forkLibrary: (id: string) => write("POST", `/library/${enc(id)}/fork`, undefined, LibraryOne),
  resetLibrary: (id: string) => write("DELETE", `/library/${enc(id)}`, undefined, nothing),
  sendNow: (id: string) => write("POST", `/library/${enc(id)}/send`, undefined, Spawned),
  startRun: (program: string, session?: number) =>
    write("POST", "/runs", session ? { program, session } : { program }, Spawned),
  runAction: (id: string, action: RunAction | "insights", expect?: string | null) =>
    write("POST", `/runs/${enc(id)}/${action}`, expect ? { expect } : {}, Spawned),
  patchSettings: (body: Record<string, Json>) => write("PATCH", "/settings", body, SettingsWritten),
};

/** Post a recording; the answer names it for `createEntry` or `addBlock`. The browser sets the multipart boundary. */
export async function uploadAudio(blob: Blob): Promise<string> {
  const form = new FormData();
  const ext = /webm/.test(blob.type) ? "webm" : /mp4|aac/.test(blob.type) ? "m4a" : /ogg|opus/.test(blob.type) ? "ogg" : "webm";
  form.append("file", blob, `recording.${ext}`);
  const made = await finish(await send("/uploads", { method: "POST", body: form }), UploadMade);
  return made.upload;
}

/** What the server said when asked how this browser is signed in: who (`session`), that it signs no one in here
 *  (`refused`: a 401), or nothing usable (`unreachable`: no network, a restart's 5xx, an older server's 404). The
 *  Tailscale screen tells the last two apart: a refusal will not pass by retrying, a restart will. */
export type Asked = { session: Session } | { refused: true } | { unreachable: true };

export async function askSession(signal?: AbortSignal): Promise<Asked> {
  let res: Response;
  try {
    res = await send("/session", { method: "GET", headers: { Accept: "application/json" }, signal });
  } catch {
    return { unreachable: true };
  }
  if (!res.ok) {
    await res.text().catch(() => undefined);   // an unread body leaves the request open, and the page never goes idle
    return res.status === 401 ? { refused: true } : { unreachable: true };
  }
  try {
    const s = Session(await res.json());
    rememberVia(s.via);   // the screen a refused read leads to depends on it (auth.ts)
    return { session: s };
  } catch {
    return { unreachable: true };
  }
}

/** How this browser is signed in, or null when that cannot be told (not signed in, an older server, no network).
 *  Unlike `get`, a 401 here does not mark the browser unpaired: the pairing page asks before it pairs. */
export async function session(signal?: AbortSignal): Promise<Session | null> {
  const asked = await askSession(signal);
  return "session" in asked ? asked.session : null;
}

/** Whether this browser is signed in by its Tailscale identity: pairing and Unpair have nothing to do then. */
export function byTailscale(s: Session | null): s is Session & { login: string } {
  return s?.via === "tailscale" && !!s.login;
}

/** Trade a one-time pairing code for the session cookie. */
export async function pair(code: string): Promise<void> {
  const res = await send("/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: code }),
  });
  if (!res.ok) throw await failure(res);
  rememberVia("cookie");
  setAuthLost(false);
}

/** Forget the cookie in this browser. */
export async function logout(): Promise<void> {
  const res = await send("/logout", { method: "POST" });
  if (!res.ok && res.status !== 401) throw await failure(res);
  rememberVia(null);
  setAuthLost(true);
}

/** Where the device is, as a place and a time zone, saved like a typed place ("Use my location"). The position is
 *  sent once, in the body, and not kept here. */
export const locate = (lat: number, lon: number) => write("POST", "/settings/location", { lat, lon }, Located);
