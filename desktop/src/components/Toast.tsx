import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

// A tiny app-wide flash. Messages auto-dismiss after 4s; consecutive
// show() calls replace the current message rather than stacking, so
// we never pile up a tower of banners.
type Variant = "ok" | "err";
type ToastMessage = { id: number; text: string; variant: Variant };
type ToastContextValue = {
  show: (text: string, variant?: Variant) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<ToastMessage | null>(null);
  const timeoutRef = useRef<number | null>(null);

  const show = useCallback((text: string, variant: Variant = "ok") => {
    setMsg({ id: Date.now(), text, variant });
  }, []);

  useEffect(() => {
    if (!msg) return;
    if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => setMsg(null), 4000);
    return () => {
      if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    };
  }, [msg]);

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      {msg ? (
        <div
          key={msg.id}
          role="status"
          className={`fixed bottom-20 left-1/2 -translate-x-1/2 z-[1100] px-4 py-2 rounded-md text-sm shadow-lg border ${
            msg.variant === "err"
              ? "bg-rose-950 text-rose-200 border-rose-900"
              : "bg-[var(--color-surface)] text-[var(--color-text)] border-[var(--color-border)]"
          }`}
        >
          {msg.text}
        </div>
      ) : null}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}
