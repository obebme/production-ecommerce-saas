"use client";

import * as React from "react";
import { AlertCircle, CheckCircle2, Info, AlertTriangle, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToastVariant = "info" | "success" | "warning" | "destructive";

export interface ToastInput {
  title: string;
  description?: string;
  variant?: ToastVariant;
  durationMs?: number;
}

interface ToastRecord extends ToastInput {
  id: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (input: ToastInput) => void;
  dismissToast: (id: string) => void;
}

const ToastContext = React.createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<ToastRecord[]>([]);
  const timersRef = React.useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const dismissToast = React.useCallback((id: string) => {
    const existing = timersRef.current.get(id);
    if (existing) {
      clearTimeout(existing);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const toast = React.useCallback(
    ({ title, description, variant = "info", durationMs = 4500 }: ToastInput) => {
      const id = `toast_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      setToasts((prev) => [...prev.slice(-3), { id, title, description, variant }]);
      if (durationMs > 0) {
        const timer = setTimeout(() => {
          dismissToast(id);
        }, durationMs);
        timersRef.current.set(id, timer);
      }
    },
    [dismissToast]
  );

  React.useEffect(() => {
    const map = timersRef.current;
    return () => {
      for (const timer of map.values()) {
        clearTimeout(timer);
      }
      map.clear();
    };
  }, []);

  const value = React.useMemo(() => ({ toast, dismissToast }), [toast, dismissToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-label="Notifications"
        className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[calc(100%-2rem)] max-w-sm flex-col gap-2"
      >
        {toasts.map((item) => {
          const isAssertive = item.variant === "destructive" || item.variant === "warning";
          const Icon =
            item.variant === "success"
              ? CheckCircle2
              : item.variant === "warning"
                ? AlertTriangle
                : item.variant === "destructive"
                  ? AlertCircle
                  : Info;

          return (
            <div
              key={item.id}
              role={isAssertive ? "alert" : "status"}
              aria-live={isAssertive ? "assertive" : "polite"}
              className={cn(
                "pointer-events-auto flex items-start gap-3 rounded-lg border bg-surface p-4 text-sm shadow-md",
                item.variant === "success" && "border-success/35 bg-success-subtle text-success",
                item.variant === "warning" &&
                  "border-warning/35 bg-warning-subtle text-warning-subtle-foreground",
                item.variant === "destructive" &&
                  "border-destructive/35 bg-destructive-subtle text-destructive",
                item.variant === "info" && "border-border text-foreground"
              )}
            >
              <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="font-semibold leading-snug">{item.title}</p>
                {item.description ? (
                  <p className="mt-0.5 text-xs opacity-90">{item.description}</p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => dismissToast(item.id)}
                aria-label={`Dismiss notification: ${item.title}`}
                className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-sm opacity-75 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = React.useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within a ToastProvider.");
  }
  return ctx;
}
