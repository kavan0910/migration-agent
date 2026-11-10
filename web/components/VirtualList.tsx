"use client";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

export interface VirtualListProps<T> {
  items: readonly T[];
  total?: number;
  rowHeight: number;
  height: number | string;
  label: string;
  getKey: (item: T) => string;
  renderRow: (item: T, state: { selected: boolean; active: boolean }) => React.ReactNode;
  selectedKey?: string | null;
  onSelect: (item: T) => void;
  onEndReached?: () => void;
  overscan?: number;
  loadingMore?: boolean;
}

/** Windowed listbox: renders only visible rows, supports arrow/Home/End/PageUp/PageDown, Enter to select. */
export function VirtualList<T>({ items, total, rowHeight, height, label, getKey, renderRow, selectedKey, onSelect, onEndReached, overscan = 6, loadingMore }: VirtualListProps<T>) {
  const ref = useRef<HTMLDivElement>(null);
  const uid = useId();
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(typeof height === "number" ? height : 480);
  const [active, setActive] = useState(0);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setViewport(el.clientHeight || 480);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setViewport(el.clientHeight || 480));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const count = items.length;
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const last = Math.min(count, Math.ceil((scrollTop + viewport) / rowHeight) + overscan);

  useEffect(() => {
    if (onEndReached && count > 0 && last >= count - 2) onEndReached();
  }, [last, count, onEndReached]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setActive((a) => Math.min(a, Math.max(0, count - 1)));
  }, [count]);

  const reveal = useCallback((i: number) => {
    const el = ref.current;
    if (!el) return;
    const top = i * rowHeight;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (top + rowHeight > el.scrollTop + el.clientHeight) el.scrollTop = top + rowHeight - el.clientHeight;
  }, [rowHeight]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (count === 0) return;
    const page = Math.max(1, Math.floor(viewport / rowHeight) - 1);
    let n = active;
    switch (e.key) {
      case "ArrowDown": n = Math.min(count - 1, active + 1); break;
      case "ArrowUp": n = Math.max(0, active - 1); break;
      case "PageDown": n = Math.min(count - 1, active + page); break;
      case "PageUp": n = Math.max(0, active - page); break;
      case "Home": n = 0; break;
      case "End": n = count - 1; break;
      case "Enter": case " ": e.preventDefault(); { const it = items[active]; if (it !== undefined) onSelect(it); } return;
      default: return;
    }
    e.preventDefault();
    setActive(n);
    reveal(n);
  }

  const activeItem = items[active];
  return (
    <div
      ref={ref} role="listbox" tabIndex={0} aria-label={label}
      aria-activedescendant={activeItem !== undefined ? `${uid}-${getKey(activeItem)}` : undefined}
      onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)} onKeyDown={onKeyDown}
      style={{ height, overflowY: "auto", position: "relative" }}
      className="rounded-lg"
    >
      <div style={{ height: count * rowHeight + (loadingMore ? rowHeight : 0), position: "relative" }}>
        {items.slice(first, last).map((item, k) => {
          const i = first + k;
          const key = getKey(item);
          return (
            <div
              key={key} id={`${uid}-${key}`} role="option" aria-selected={selectedKey === key} aria-posinset={i + 1} aria-setsize={total ?? count}
              onClick={() => { setActive(i); onSelect(item); }}
              style={{ position: "absolute", top: i * rowHeight, left: 0, right: 0, height: rowHeight }}
              className="cursor-pointer"
            >
              {renderRow(item, { selected: selectedKey === key, active: active === i })}
            </div>
          );
        })}
        {loadingMore && <div style={{ position: "absolute", top: count * rowHeight, height: rowHeight }} className="grid w-full place-items-center text-sm text-muted" role="status">Loading more</div>}
      </div>
    </div>
  );
}
