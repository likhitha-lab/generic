/**
 * Defensive, format-agnostic helpers for rendering resume fields whose
 * runtime shape can't be fully trusted from the TypeScript type alone -
 * the backend is a separate service (Python) that can send a shape the
 * frontend type doesn't (yet) know about, has changed recently (skills/
 * tools went from a flat list to an optional category->list object - see
 * CategorizedList in types/resume.ts), or a field can simply be missing/
 * null on a partially-loaded or older stored version. Every place that
 * used to call `.map()` directly on skills/tools/experience/education/
 * certifications/projects should go through one of these instead, so a
 * shape mismatch degrades to "renders nothing for this section" rather
 * than crashing the whole preview with "X.map is not a function".
 */

export interface SkillGroup {
  /** null means "no category - render as one flat group" (the old
   * flat-array format, and the only format the manual Resume Generator
   * flow still produces). */
  category: string | null;
  items: string[];
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

/**
 * Normalizes a skills/tools field into a list of category groups,
 * regardless of whether the backend sent:
 *   - a flat array of strings (old format) -> one group with category: null
 *   - a category-name -> string[] object (new format) -> one group per key
 *   - undefined / null / {} / anything else -> []
 * Never throws - any shape that isn't recognized is treated as empty
 * rather than crashing the caller.
 */
export function normalizeSkillGroups(value: unknown): SkillGroup[] {
  if (Array.isArray(value)) {
    const items = value.filter(isNonEmptyString);
    return items.length > 0 ? [{ category: null, items }] : [];
  }

  if (value !== null && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([category, rawItems]) => ({
        category,
        items: Array.isArray(rawItems) ? rawItems.filter(isNonEmptyString) : [],
      }))
      .filter((group) => group.items.length > 0);
  }

  // undefined, null, a bare string/number, etc.
  return [];
}

/**
 * Coerces any value into a safe array to call .map()/.length on -
 * undefined, null, or any non-array value becomes [] instead of throwing.
 * Use this for fields that are ALWAYS supposed to be a plain array
 * (experience, education, certifications, projects) but whose actual
 * runtime value shouldn't be trusted unconditionally.
 */
export function toSafeArray<T>(value: T[] | null | undefined | unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}
