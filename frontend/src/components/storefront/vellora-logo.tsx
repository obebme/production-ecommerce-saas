import { cn } from "@/lib/utils";

export interface VelloraLogoProps {
  className?: string;
  showWordmark?: boolean;
  iconClassName?: string;
  wordmarkClassName?: string;
}

export function VelloraLogo({
  className,
  showWordmark = true,
  iconClassName,
  wordmarkClassName,
}: VelloraLogoProps) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <svg
        viewBox="0 0 32 32"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        className={cn("h-7 w-7 shrink-0 text-accent", iconClassName)}
      >
        <rect
          x="1.5"
          y="1.5"
          width="29"
          height="29"
          rx="8"
          className="fill-accent/10 stroke-accent/35"
          strokeWidth="1.25"
        />
        <path
          d="M8.5 9.5L16 23.5L23.5 9.5"
          stroke="currentColor"
          strokeWidth="2.3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d="M12.75 9.5L16 15.8L19.25 9.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity="0.7"
        />
      </svg>
      {showWordmark ? (
        <span
          className={cn(
            "text-lg font-semibold tracking-tight text-foreground sm:text-xl",
            wordmarkClassName
          )}
        >
          Vellora Store
        </span>
      ) : null}
    </span>
  );
}
