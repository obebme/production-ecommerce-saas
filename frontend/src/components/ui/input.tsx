import * as React from "react";
import { cn } from "@/lib/utils";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  hasError?: boolean;
}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = "text", hasError = false, "aria-invalid": ariaInvalid, ...props }, ref) => {
    const isInvalid = hasError || ariaInvalid === true || ariaInvalid === "true";
    return (
      <input
        ref={ref}
        type={type}
        aria-invalid={isInvalid ? true : undefined}
        className={cn(
          "flex h-11 w-full rounded-md border bg-surface px-3 py-2 text-base text-foreground shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 md:h-10 md:text-sm",
          hasError
            ? "border-destructive focus-visible:ring-destructive"
            : "border-border-control focus-visible:ring-ring",
          className
        )}
        {...props}
      />
    );
  }
);
Input.displayName = "Input";

export { Input };
