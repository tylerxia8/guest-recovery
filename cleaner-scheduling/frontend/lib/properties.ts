import { api } from "./api";

/** Follow the server cursor so houses added after the first 100 remain selectable. */
export async function properties<T>(activeOnly = false): Promise<{items: T[]}> {
  const items: T[] = [];
  let cursor: string | null = null;
  const seen = new Set<string>();
  do {
    const query = new URLSearchParams({limit: "100"});
    if(activeOnly) query.set("active", "true");
    if(cursor) query.set("cursor", cursor);
    const page = await api<{items: T[]; next_cursor: string | null}>(`/properties?${query}`);
    items.push(...page.items); cursor = page.next_cursor;
    if(cursor && seen.has(cursor)) throw new Error("Property list could not be completed.");
    if(cursor) seen.add(cursor);
  } while(cursor);
  return {items};
}
