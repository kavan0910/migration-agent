"use client";
import Link from "next/link";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Children, isValidElement, type ReactNode } from "react";
import { NODE_ID, preprocess, slugify } from "@/lib/markdown";

function textOf(node: ReactNode): string {
  return Children.toArray(node).map((c) => (typeof c === "string" || typeof c === "number" ? String(c) : isValidElement<{ children?: ReactNode }>(c) ? textOf(c.props.children) : "")).join("");
}

export function Markdown({ source, projectId }: { source: string; projectId: string }) {
  const components: Components = {
    h2: ({ children }) => <h2 id={slugify(textOf(children))}>{children}</h2>,
    h3: ({ children }) => <h3 id={slugify(textOf(children))}>{children}</h3>,
    strong: ({ children }) => {
      const t = textOf(children).trim();
      return NODE_ID.test(t)
        ? <Link href={`/projects/${projectId}/graph?node=${t}`} className="font-mono text-[0.9em] font-semibold !no-underline rounded bg-accent-soft px-1 text-accent hover:underline">{t}</Link>
        : <strong>{children}</strong>;
    },
    a: ({ href, children }) => /^https?:\/\//.test(href ?? "") ? <a href={href} rel="noopener noreferrer" target="_blank">{children}</a> : <a href={href}>{children}</a>,
    table: ({ children }) => <div className="overflow-x-auto"><table>{children}</table></div>,
  };
  return (
    <div className="prose-doc">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>{preprocess(source)}</ReactMarkdown>
    </div>
  );
}
