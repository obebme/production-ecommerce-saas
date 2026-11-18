"use client";

import * as React from "react";
import Image from "next/image";
import { sanitizeMediaUrl } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface ProductImageProps {
  src: string | null | undefined;
  alt: string;
  fallbackLabel?: string;
  className?: string;
  imgClassName?: string;
  sizes?: string;
  priority?: boolean;
}

export function ProductImage({
  src,
  alt,
  fallbackLabel,
  className,
  imgClassName,
  sizes = "(max-width: 768px) 50vw, 25vw",
  priority = false,
}: ProductImageProps) {
  const safeUrl = sanitizeMediaUrl(src);
  const [failedUrl, setFailedUrl] = React.useState<string | null>(null);
  const hasError = safeUrl !== null && failedUrl === safeUrl;

  if (!safeUrl || hasError) {
    return (
      <div
        className={cn(
          "flex h-full w-full items-center justify-center bg-muted px-4 text-center",
          className
        )}
      >
        <span className="font-display text-sm font-medium text-muted-foreground line-clamp-2">
          {fallbackLabel || alt || "Atelier Piece"}
        </span>
      </div>
    );
  }

  return (
    <div className={cn("relative h-full w-full overflow-hidden bg-muted", className)}>
      <Image
        src={safeUrl}
        alt={alt}
        fill
        sizes={sizes}
        priority={priority}
        unoptimized
        onError={() => setFailedUrl(safeUrl)}
        className={cn("object-cover object-center", imgClassName)}
      />
    </div>
  );
}
