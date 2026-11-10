import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfidenceMeter, Dialog, EmptyState, ErrorState, StatusPill } from "@/components/ui";
import { VirtualList } from "@/components/VirtualList";
import { EvidenceQuote } from "@/components/NodeDetail";
import { RunLauncher } from "@/components/RunLauncher";
import { ToastProvider } from "@/components/Toast";
import { api } from "@/lib/api";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: { href: string; children: React.ReactNode }) => <a href={href} {...p}>{children}</a> }));

describe("primitives", () => {
  it("ConfidenceMeter exposes the value to assistive tech", () => {
    render(<ConfidenceMeter value="medium" />);
    expect(screen.getByText("medium")).toBeInTheDocument();
    render(<ConfidenceMeter value="high" showLabel={false} />);
    expect(screen.getByText("Confidence high")).toHaveClass("sr-only");
  });
  it("StatusPill shows human labels", () => {
    render(<StatusPill status="stopped_budget" />);
    expect(screen.getByText("Stopped at budget")).toBeInTheDocument();
  });
  it("EmptyState and ErrorState render, error state retries", async () => {
    const retry = vi.fn();
    render(<><EmptyState title="Nothing here">Explain</EmptyState><ErrorState error={new Error("boom")} onRetry={retry} /></>);
    expect(screen.getByRole("heading", { name: "Nothing here" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalled();
  });
  it("EvidenceQuote cites file and line range", () => {
    render(<EvidenceQuote ev={{ file: "a/B.java", line_start: 4, line_end: 9, quote: "int x = 1;" }} />);
    expect(screen.getByText("a/B.java:4-9")).toBeInTheDocument();
    expect(screen.getByText("int x = 1;")).toBeInTheDocument();
  });
  it("Dialog is labelled by its title", () => {
    HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) { this.setAttribute("open", ""); });
    render(<Dialog open onClose={() => {}} title="Delete it?"><p>body</p></Dialog>);
    expect(screen.getByRole("dialog", { name: "Delete it?" })).toBeInTheDocument();
  });
});

describe("VirtualList", () => {
  const items = Array.from({ length: 5000 }, (_, i) => ({ id: `n${i}` }));
  it("renders only a window of rows and supports keyboard selection", async () => {
    const onSelect = vi.fn();
    render(<VirtualList items={items} total={5000} rowHeight={60} height={300} label="Nodes" getKey={(i) => i.id} onSelect={onSelect} renderRow={(i) => <span>{i.id}</span>} />);
    const rows = screen.getAllByRole("option");
    expect(rows.length).toBeLessThan(30);
    expect(rows[0]).toHaveAttribute("aria-setsize", "5000");
    const list = screen.getByRole("listbox", { name: "Nodes" });
    list.focus();
    await userEvent.keyboard("{ArrowDown}{ArrowDown}{Enter}");
    expect(onSelect).toHaveBeenCalledWith({ id: "n2" });
    await userEvent.keyboard("{End}{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith({ id: "n4999" });
  });
  it("asks for more when the end is reached", () => {
    const onEnd = vi.fn();
    render(<VirtualList items={items.slice(0, 3)} total={5000} rowHeight={60} height={300} label="Nodes" getKey={(i) => i.id} onSelect={() => {}} onEndReached={onEnd} renderRow={(i) => <span>{i.id}</span>} />);
    expect(onEnd).toHaveBeenCalled();
  });
});

describe("RunLauncher", () => {
  beforeEach(() => vi.restoreAllMocks());
  it("shows the estimate and reason, and disables start when denied", async () => {
    vi.spyOn(api.projects, "estimate").mockResolvedValue({ chunks: 275, usd: 32.2, allowed: false, reason: "Estimated cost exceeds the monthly allowance." });
    const start = vi.spyOn(api.runs, "start");
    render(<ToastProvider><RunLauncher projectId="p" hasGraph onStarted={() => {}} /></ToastProvider>);
    await waitFor(() => expect(screen.getByText("Not allowed")).toBeInTheDocument());
    expect(screen.getByText(/exceeds the monthly allowance/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start run" })).toBeDisabled();
    expect(start).not.toHaveBeenCalled();
  });
  it("starts a run with the chosen options when allowed", async () => {
    vi.spyOn(api.projects, "estimate").mockResolvedValue({ chunks: 3, usd: 0.08, allowed: true, reason: null });
    const start = vi.spyOn(api.runs, "start").mockResolvedValue({ id: "r1" } as never);
    const onStarted = vi.fn();
    render(<ToastProvider><RunLauncher projectId="p" hasGraph onStarted={onStarted} /></ToastProvider>);
    await waitFor(() => expect(screen.getByText("Allowed on your plan")).toBeInTheDocument());
    await userEvent.click(screen.getByLabelText(/Fast/));
    await userEvent.click(screen.getByRole("button", { name: "Start run" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(start).toHaveBeenCalledWith("p", { type: "analyze", model_profile: "fast", max_usd: 1, incremental: false });
  });
  it("warns when the budget is below the estimate", async () => {
    vi.spyOn(api.projects, "estimate").mockResolvedValue({ chunks: 3, usd: 5, allowed: true, reason: null });
    render(<ToastProvider><RunLauncher projectId="p" hasGraph onStarted={() => {}} /></ToastProvider>);
    await waitFor(() => expect(screen.getByText(/will stop early/)).toBeInTheDocument());
    const section = screen.getByText("Estimate before you start").parentElement!;
    expect(within(section).getByText("$5.00")).toBeInTheDocument();
  });
});
