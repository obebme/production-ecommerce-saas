"use client";

import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  hasNext: boolean;
  onPageChange: (nextPage: number) => void;
  itemLabel?: string;
  className?: string;
}

function getVisiblePages(currentPage: number, totalPages: number): (number | "ellipsis")[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, idx) => idx + 1);
  }
  const pages: (number | "ellipsis")[] = [1];
  const start = Math.max(2, currentPage - 1);
  const end = Math.min(totalPages - 1, currentPage + 1);

  if (start > 2) {
    pages.push("ellipsis");
  }
  for (let p = start; p <= end; p += 1) {
    pages.push(p);
  }
  if (end < totalPages - 1) {
    pages.push("ellipsis");
  }
  pages.push(totalPages);
  return pages;
}

export function Pagination({
  page,
  pageSize,
  total,
  hasNext,
  onPageChange,
  itemLabel = "products",
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  const startItem = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const endItem = total === 0 ? 0 : Math.min(total, page * pageSize);
  const pages = getVisiblePages(page, totalPages);

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-between gap-4 border-t border-border pt-6 sm:flex-row",
        className
      )}
    >
      <p className="text-sm text-muted-foreground tabular-nums" aria-live="polite">
        {total === 0
          ? `Showing 0 ${itemLabel}`
          : `Showing ${startItem}–${endItem} of ${total} ${itemLabel}`}
      </p>

      {totalPages > 1 ? (
        <nav aria-label="Pagination" className="flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="min-h-[44px] min-w-[44px] lg:min-h-0 lg:min-w-0"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            aria-label="Previous page"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            <span className="hidden sm:inline">Previous</span>
          </Button>

          {pages.map((entry, idx) =>
            entry === "ellipsis" ? (
              <span
                key={`ellipsis-${idx}`}
                className="px-2 text-sm text-muted-foreground"
                aria-hidden="true"
              >
                …
              </span>
            ) : (
              <Button
                key={entry}
                variant={entry === page ? "default" : "outline"}
                size="sm"
                className="min-h-[44px] min-w-[44px] tabular-nums lg:min-h-0 lg:min-w-[36px]"
                aria-label={`Page ${entry}`}
                aria-current={entry === page ? "page" : undefined}
                onClick={() => onPageChange(entry)}
              >
                {entry}
              </Button>
            )
          )}

          <Button
            variant="outline"
            size="sm"
            className="min-h-[44px] min-w-[44px] lg:min-h-0 lg:min-w-0"
            disabled={!hasNext}
            onClick={() => onPageChange(page + 1)}
            aria-label="Next page"
          >
            <span className="hidden sm:inline">Next</span>
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </nav>
      ) : null}
    </div>
  );
}
