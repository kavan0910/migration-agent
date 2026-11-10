import type { Evidence, GraphEdge, GraphNode } from "../types";

const SECTIONS: Array<{ title: string; types: string[]; intro: string }> = [
  { title: "Purpose & Capabilities", types: [], intro: "" },
  { title: "Boundary & External Dependencies", types: ["external_dependency"], intro: "External systems and libraries the service depends on." },
  { title: "Component Responsibilities", types: ["component"], intro: "Components grouped by module." },
  { title: "Endpoints & API Surface", types: ["endpoint"], intro: "Every route the service exposes." },
  { title: "Data Model & Storage", types: ["data_entity"], intro: "Entities the service persists." },
  { title: "Business Rules", types: ["business_rule"], intro: "Rules enforced in code. Each carries the quoted evidence it was derived from." },
  { title: "Configuration & Environment", types: ["config"], intro: "Configuration keys read by the service." },
];

const cite = (e: Evidence) => `\`${e.file}${e.line_start ? `:${e.line_start}${e.line_end && e.line_end !== e.line_start ? `-${e.line_end}` : ""}` : ""}\``;
const PER_SECTION = 14;

export function renderUnderstanding(project: string, version: number, nodes: GraphNode[], edges: GraphEdge[]): string {
  const counts: Record<string, number> = {};
  nodes.forEach((n) => (counts[n.type] = (counts[n.type] ?? 0) + 1));
  const lines: string[] = [
    `# Understanding: ${project}`, "",
    `_Rendered from graph v${version} · ${nodes.length.toLocaleString("en-US")} nodes, ${edges.length.toLocaleString("en-US")} edges._`, "",
    "## Contents", "", ...SECTIONS.map((s, i) => `${i + 1}. ${s.title}`), "",
  ];
  SECTIONS.forEach((s, i) => {
    lines.push(`## ${i + 1}. ${s.title}`, "");
    if (i === 0) {
      lines.push("_No purpose statement recorded in project metadata._", "", "Graph summary:", "");
      Object.keys(counts).sort().forEach((t) => lines.push(`- ${t}: ${counts[t]}`));
      lines.push("");
      return;
    }
    lines.push(s.intro, "");
    const subset = nodes.filter((n) => s.types.includes(n.type));
    subset.slice(0, PER_SECTION).forEach((n) => {
      lines.push(`- **${n.id}** ${n.name}`);
      if (n.responsibility) lines.push(`  - Responsibility: ${n.responsibility}`);
      const e = n.evidence[0];
      if (e) lines.push(`  - Evidence: ${cite(e)}`);
      lines.push(`  - Confidence: ${n.confidence}`);
      if (e && s.types[0] === "business_rule") lines.push("", "  ```", ...e.quote.split("\n").map((l) => `  ${l}`), "  ```");
    });
    if (subset.length > PER_SECTION) {
      lines.push("", `_${(subset.length - PER_SECTION).toLocaleString("en-US")} more not shown here. Open the graph explorer to browse all ${subset.length.toLocaleString("en-US")}._`);
    }
    if (subset.length === 0) lines.push("_Nothing recorded._");
    lines.push("");
  });
  const quirks = nodes.filter((n) => n.quirk || n.suspected_bug);
  lines.push("## Quirks & Suspicious Code", "");
  quirks.slice(0, PER_SECTION).forEach((n) => lines.push(`- **${n.id}** ${n.name}${n.suspected_bug ? " (suspected bug)" : ""} ${n.evidence[0] ? "· " + cite(n.evidence[0]) : ""}`));
  if (quirks.length === 0) lines.push("_Nothing flagged._");
  lines.push("", "## Coverage Matrix", "", "| Type | Nodes |", "| --- | ---: |");
  Object.keys(counts).sort().forEach((t) => lines.push(`| ${t} | ${counts[t]} |`));
  return lines.join("\n") + "\n";
}
