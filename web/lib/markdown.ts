/** Helpers for the understanding document. Raw HTML is never rendered; engine anchors are rewritten. */
export const NODE_ID = /^(?:COMP|EP|ENT|RULE|EXT|CFG|EDGE|NODE)-\d{3,}$/;

export function preprocess(md: string): string {
  return md.replace(/<a id="[^"]*"><\/a>/g, "");
}
export function slugify(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}
export interface Heading {
  level: 2 | 3;
  text: string;
  id: string;
}
export function extractHeadings(md: string): Heading[] {
  const out: Heading[] = [];
  let fence = false;
  for (const line of md.split("\n")) {
    if (/^\s*```/.test(line)) fence = !fence;
    if (fence) continue;
    const m = /^(#{2,3})\s+(.*?)\s*#*\s*$/.exec(line);
    if (m) out.push({ level: m[1]!.length as 2 | 3, text: m[2]!, id: slugify(m[2]!) });
  }
  return out;
}
