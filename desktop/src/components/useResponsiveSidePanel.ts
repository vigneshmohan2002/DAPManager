import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

const COMPACT_PANEL_MAX_WIDTH = 1050;

export function useResponsiveSidePanel(
  open: boolean,
  onClose: () => void,
) {
  const [compact, setCompact] = useState(
    () =>
      typeof window !== "undefined" &&
      window.innerWidth <= COMPACT_PANEL_MAX_WIDTH,
  );
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const updateCompactMode = () => {
      setCompact(window.innerWidth <= COMPACT_PANEL_MAX_WIDTH);
    };
    window.addEventListener("resize", updateCompactMode);
    return () => window.removeEventListener("resize", updateCompactMode);
  }, []);

  useEffect(() => {
    if (!open || !compact) return;

    returnFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const focusTimer = window.setTimeout(() => {
      closeButtonRef.current?.focus();
    }, 0);

    return () => {
      window.clearTimeout(focusTimer);
      const returnTarget = returnFocusRef.current;
      returnFocusRef.current = null;
      if (returnTarget?.isConnected) returnTarget.focus();
    };
  }, [compact, open]);

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    event.stopPropagation();
    onClose();
  };

  return {
    closeButtonRef,
    compact,
    handleKeyDown,
  };
}
