"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useTheme } from "./theme-provider";

export interface ThemeToggleProps {
  className?: string;
  showLabel?: boolean;
  variant?: "icon" | "pill";
}

export function ThemeToggle({
  className,
  showLabel = false,
  variant = "icon",
}: ThemeToggleProps) {
  const { theme, setTheme, toggleTheme } = useTheme();
  const nextLabel = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";

  if (variant === "pill") {
    return (
      <div
        role="group"
        aria-label="Toggle color theme"
        className={cn(
          "inline-flex items-center rounded-full border border-border bg-muted/80 p-1",
          className
        )}
      >
        <button
          type="button"
          onClick={() => setTheme("light")}
          aria-pressed={theme === "light"}
          aria-label="Light theme"
          title="Switch to light theme"
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring xl:px-3",
            theme === "light"
              ? "bg-background text-foreground shadow-xs"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          <Sun className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="hidden xl:inline">Light</span>
        </button>
        <button
          type="button"
          onClick={() => setTheme("dark")}
          aria-pressed={theme === "dark"}
          aria-label="Dark theme"
          title="Switch to dark theme"
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring xl:px-3",
            theme === "dark"
              ? "bg-background text-foreground shadow-xs"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          <Moon className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="hidden xl:inline">Dark</span>
        </button>
      </div>
    );
  }

  return (
    <Button
      variant="ghost"
      size={showLabel ? "default" : "icon"}
      onClick={toggleTheme}
      aria-label="Toggle color theme"
      title={nextLabel}
      className={className}
    >
      <Sun className="h-4 w-4 dark:hidden" aria-hidden="true" />
      <Moon className="hidden h-4 w-4 dark:block" aria-hidden="true" />
      {showLabel ? (
        <span className="text-sm">{theme === "dark" ? "Light mode" : "Dark mode"}</span>
      ) : null}
    </Button>
  );
}
