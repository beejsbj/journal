import { describe, expect, it } from "vitest";
import { ShapeError, arr, bool, json, nullable, num, obj, oneOf, opt, rec, str } from "../src/schema";

describe("obj", () => {
  const p = obj({ name: str, age: opt(num), nick: nullable(str), tags: arr(str) });

  it("keeps the fields it knows and drops every other, at any depth", () => {
    const got = p({ name: "a", age: 3, nick: null, tags: ["x"], secret: "/x off the record" });
    expect(got).toEqual({ name: "a", age: 3, nick: null, tags: ["x"] });
    expect("secret" in got).toBe(false);
    const nested = obj({ inner: obj({ a: str }) })({ inner: { a: "1", leaked: true }, extra: 1 });
    expect(nested).toEqual({ inner: { a: "1" } });
  });

  it("treats absent optional and nullable fields as undefined and null", () => {
    expect(p({ name: "a", tags: [] })).toEqual({ name: "a", age: undefined, nick: null, tags: [] });
  });

  it("names where a wrong type is", () => {
    expect(() => p({ name: 5, tags: [] })).toThrowError(new ShapeError("name", "a string"));
    expect(() => p({ name: "a", tags: ["x", 2] })).toThrow(/tags\[1\]: expected a string/);
    expect(() => p("nope")).toThrow(/expected an object/);
    expect(() => p(null)).toThrow(ShapeError);
  });
});

describe("the small parsers", () => {
  it("accepts only what they name", () => {
    expect(num(1.5)).toBe(1.5);
    expect(() => num("1")).toThrow(ShapeError);
    expect(() => num(Number.NaN)).toThrow(ShapeError);
    expect(bool(false)).toBe(false);
    expect(() => bool(0)).toThrow(ShapeError);
    const state = oneOf("a", "b");
    expect(state("a")).toBe("a");
    expect(() => state("c")).toThrow(/one of a, b/);
  });

  it("parses records and lists item by item", () => {
    expect(rec(num)({ a: 1, b: 2 })).toEqual({ a: 1, b: 2 });
    expect(() => rec(num)({ a: "x" })).toThrow(/a: expected a number/);
    expect(() => arr(str)("x")).toThrow(/a list/);
  });

  it("keeps a JSON tree whole and refuses what JSON cannot hold", () => {
    expect(json({ a: [1, "b", null, { c: true }] })).toEqual({ a: [1, "b", null, { c: true }] });
    expect(() => json({ a: () => 1 })).toThrow(ShapeError);
  });
});
