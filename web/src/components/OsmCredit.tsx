import type { Json } from "../schema";

/** `coordinates.source` of a place the reverse lookup named (location.py `OSM`). */
export const OSM = "openstreetmap";

/**
 * The credit a place named by OpenStreetMap's data carries wherever the page shows it (Nominatim's usage policy asks
 * for it to be clearly displayed). A caption, not a banner: it sits under the place, in the quietest type.
 */
export function OsmCredit() {
  return (
    <p class="credit">
      Place name © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a>
    </p>
  );
}

/** Whether the stored place is the one OpenStreetMap named (the server's `location.place_source`, read from the
 *  settings tree: a hand edit of the place alone does not inherit the credit). */
export function osmNamed(settings: Record<string, Json>): boolean {
  const c = settings.coordinates;
  if (c === null || typeof c !== "object" || Array.isArray(c)) return false;
  return c.source === OSM && typeof settings.location === "string" && settings.location !== "" && c.place === settings.location;
}
