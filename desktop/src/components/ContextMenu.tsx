import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

// A ContextMenuEntry is rendered inside the menu in the order given.
// `list` entries are a scrollable pick-list (e.g. "add to which
// playlist?"), matching the web /library page's "Add to playlist"
// submenu shape.
export type ContextMenuEntry =
  | {
      kind: "item";
      label: string;
      onSelect: () => void;
      danger?: boolean;
      disabled?: boolean;
    }
  | { kind: "label"; text: string }
  | { kind: "separator" }
  | {
      kind: "list";
      heading?: string;
      emptyText?: string;
      items: Array<{ key: string; label: string; onSelect: () => void }>;
    };

type Props = {
  x: number;
  y: number;
  entries: ContextMenuEntry[];
  onClose: () => void;
};

const ENABLED_MENU_ITEM_SELECTOR = '[role="menuitem"]:not(:disabled)';

function enabledMenuItems(container: HTMLElement | null): HTMLButtonElement[] {
  if (!container) return [];
  return Array.from(
    container.querySelectorAll<HTMLButtonElement>(ENABLED_MENU_ITEM_SELECTOR),
  );
}

export default function ContextMenu({ x, y, entries, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLElement | null>(
    typeof document !== "undefined" &&
      document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  // Render at (x, y) first, then nudge in a layout effect if it
  // would overflow the viewport — matches the web page's placeMenu.
  const [pos, setPos] = useState({ x, y });

  useLayoutEffect(() => {
    enabledMenuItems(ref.current)[0]?.focus();
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    let nx = x;
    let ny = y;
    if (x + r.width > window.innerWidth) nx = window.innerWidth - r.width - 4;
    if (y + r.height > window.innerHeight)
      ny = window.innerHeight - r.height - 4;
    if (nx !== pos.x || ny !== pos.y) setPos({ x: nx, y: ny });
  }, [x, y, pos.x, pos.y]);

  useEffect(() => {
    // Outside-click (pointerdown, not click, so the menu doesn't
    // catch its own dismissing click on another interactive element)
    // and Escape both close the menu.
    const onPointer = (e: PointerEvent) => {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Tab" && ref.current?.contains(document.activeElement)) {
        onClose();
        return;
      }
      if (e.key !== "Escape") return;
      e.preventDefault();
      onClose();
      queueMicrotask(() => {
        if (triggerRef.current?.isConnected) triggerRef.current.focus();
      });
    };
    window.addEventListener("pointerdown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = enabledMenuItems(ref.current);
    if (items.length === 0) return;

    const currentIndex = items.findIndex(
      (item) => item === document.activeElement,
    );
    let targetIndex: number | null = null;

    switch (event.key) {
      case "ArrowDown":
        targetIndex = currentIndex < 0 ? 0 : (currentIndex + 1) % items.length;
        break;
      case "ArrowUp":
        targetIndex =
          currentIndex < 0
            ? items.length - 1
            : (currentIndex - 1 + items.length) % items.length;
        break;
      case "Home":
        targetIndex = 0;
        break;
      case "End":
        targetIndex = items.length - 1;
        break;
      default:
        return;
    }

    event.preventDefault();
    items[targetIndex].focus();
  };

  return createPortal(
    <div
      ref={ref}
      role="menu"
      onKeyDown={handleKeyDown}
      style={{ position: "fixed", top: pos.y, left: pos.x, zIndex: 1000 }}
      className="min-w-48 max-w-80 bg-[var(--color-surface)] border border-[var(--color-border)] rounded-md shadow-lg py-1 text-sm text-[var(--color-text)]"
    >
      {entries.map((entry, i) => {
        if (entry.kind === "separator") {
          return (
            <div
              key={`sep-${i}`}
              className="my-1 border-t border-[var(--color-border)]"
            />
          );
        }
        if (entry.kind === "label") {
          return (
            <div
              key={`lbl-${i}`}
              className="px-3 py-1 text-xs uppercase tracking-wider text-[var(--color-text-muted)]"
            >
              {entry.text}
            </div>
          );
        }
        if (entry.kind === "list") {
          return (
            <div key={`list-${i}`}>
              {entry.heading ? (
                <div className="px-3 py-1 text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
                  {entry.heading}
                </div>
              ) : null}
              {entry.items.length === 0 ? (
                <div className="px-3 py-1.5 text-[var(--color-text-muted)] italic">
                  {entry.emptyText ?? "None"}
                </div>
              ) : (
                <div className="max-h-56 overflow-y-auto">
                  {entry.items.map((it) => (
                    <button
                      type="button"
                      role="menuitem"
                      tabIndex={-1}
                      key={it.key}
                      onClick={() => {
                        it.onSelect();
                        onClose();
                      }}
                      className="block w-full text-left px-3 py-1.5 hover:bg-[var(--color-bg)]"
                    >
                      {it.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        }
        // item
        return (
          <button
            type="button"
            role="menuitem"
            tabIndex={-1}
            key={`item-${i}`}
            onClick={() => {
              if (entry.disabled) return;
              entry.onSelect();
              onClose();
            }}
            disabled={entry.disabled}
            className={`block w-full text-left px-3 py-1.5 disabled:opacity-50 disabled:cursor-not-allowed ${
              entry.danger
                ? "text-rose-300 hover:bg-rose-900/30"
                : "hover:bg-[var(--color-bg)]"
            }`}
          >
            {entry.label}
          </button>
        );
      })}
    </div>,
    document.body,
  );
}
