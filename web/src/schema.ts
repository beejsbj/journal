/**
 * Runtime shape checks for what the server says (FRONTEND.md decision 7): a parser keeps the fields it knows and
 * drops every other, so a field a later API version adds (or one that should never have been sent) cannot reach the
 * page by accident. A field of the wrong type is an error naming where, not a silent `undefined`.
 */

export class ShapeError extends Error {
  constructor(
    readonly path: string,
    readonly expected: string,
  ) {
    super(`${path || "response"}: expected ${expected}`);
    this.name = "ShapeError";
  }
}

export type Parser<T> = (value: unknown, path?: string) => T;
export type Infer<P> = P extends Parser<infer T> ? T : never;

export const str: Parser<string> = (v, path = "") => {
  if (typeof v !== "string") throw new ShapeError(path, "a string");
  return v;
};

export const num: Parser<number> = (v, path = "") => {
  if (typeof v !== "number" || Number.isNaN(v)) throw new ShapeError(path, "a number");
  return v;
};

export const bool: Parser<boolean> = (v, path = "") => {
  if (typeof v !== "boolean") throw new ShapeError(path, "true or false");
  return v;
};

/** Any JSON value, kept as it is: only for trees the app shows generically (the settings). */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export const json: Parser<Json> = (v, path = "") => {
  if (v === null || typeof v === "boolean" || typeof v === "string") return v;
  if (typeof v === "number") return v;
  if (Array.isArray(v)) return v.map((x, i) => json(x, `${path}[${i}]`));
  if (typeof v === "object") {
    const out: { [key: string]: Json } = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) out[k] = json(x, path ? `${path}.${k}` : k);
    return out;
  }
  throw new ShapeError(path, "a JSON value");
};

/** Absent or null is `undefined`; anything else must parse. */
export const opt =
  <T>(p: Parser<T>): Parser<T | undefined> =>
  (v, path = "") =>
    v === undefined || v === null ? undefined : p(v, path);

/** Absent or null is `null`; anything else must parse. */
export const nullable =
  <T>(p: Parser<T>): Parser<T | null> =>
  (v, path = "") =>
    v === undefined || v === null ? null : p(v, path);

export const arr =
  <T>(p: Parser<T>): Parser<T[]> =>
  (v, path = "") => {
    if (!Array.isArray(v)) throw new ShapeError(path, "a list");
    return v.map((x, i) => p(x, `${path}[${i}]`));
  };

export const rec =
  <T>(p: Parser<T>): Parser<Record<string, T>> =>
  (v, path = "") => {
    if (v === null || typeof v !== "object" || Array.isArray(v)) throw new ShapeError(path, "an object");
    const out: Record<string, T> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) out[k] = p(x, path ? `${path}.${k}` : k);
    return out;
  };

export const oneOf =
  <const T extends readonly string[]>(...allowed: T): Parser<T[number]> =>
  (v, path = "") => {
    if (typeof v !== "string" || !allowed.includes(v)) throw new ShapeError(path, `one of ${allowed.join(", ")}`);
    return v as T[number];
  };

/** An object with exactly these fields: others are dropped, a missing field is its parser's call (opt, nullable). */
export const obj =
  <S extends Record<string, Parser<unknown>>>(shape: S): Parser<{ [K in keyof S]: Infer<S[K]> }> =>
  (v, path = "") => {
    if (v === null || typeof v !== "object" || Array.isArray(v)) throw new ShapeError(path, "an object");
    const src = v as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(shape)) {
      out[key] = (shape[key] as Parser<unknown>)(src[key], path ? `${path}.${key}` : key);
    }
    return out as { [K in keyof S]: Infer<S[K]> };
  };
