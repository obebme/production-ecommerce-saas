import { ApiError, extractValidationErrors } from "@/lib/api/client";

export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const CURRENCY_RE = /^[A-Z]{3}$/;
export const INT32_MAX = 2_147_483_647;
export const MAX_ANALYTICS_SPAN_DAYS = 366;

/**
 * Convert free text into a backend-compatible lowercase kebab-case slug.
 */
export function slugify(input: string): string {
  return input
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Map FastAPI/Pydantic 422 VALIDATION_ERROR details onto field keys.
 */
export function extractFieldErrorMap(error: ApiError): Record<string, string> {
  const fieldErrors: Record<string, string> = {};
  for (const item of extractValidationErrors(error)) {
    const rawField = item.loc[item.loc.length - 1];
    if (typeof rawField === "string" && rawField && !fieldErrors[rawField]) {
      fieldErrors[rawField] = item.msg;
    }
  }
  return fieldErrors;
}

/**
 * Return UTC YYYY-MM-DD for a given Date.
 */
export function formatUtcDateInput(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Shift a UTC YYYY-MM-DD date string by deltaDays without local-timezone drift.
 */
export function utcDateShift(dateStr: string, deltaDays: number): string {
  const [y, m, d] = dateStr.split("-").map((part) => Number.parseInt(part, 10));
  const shifted = new Date(Date.UTC(y, m - 1, d + deltaDays));
  return formatUtcDateInput(shifted);
}

/**
 * Compute exact integer UTC calendar days between two YYYY-MM-DD strings (to - from).
 */
export function utcDaysBetween(fromStr: string, toStr: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(fromStr) || !/^\d{4}-\d{2}-\d{2}$/.test(toStr)) {
    return null;
  }
  const [fy, fm, fd] = fromStr.split("-").map((part) => Number.parseInt(part, 10));
  const [ty, tm, td] = toStr.split("-").map((part) => Number.parseInt(part, 10));
  const fromUtc = Date.UTC(fy, fm - 1, fd);
  const toUtc = Date.UTC(ty, tm - 1, td);
  if (Number.isNaN(fromUtc) || Number.isNaN(toUtc)) {
    return null;
  }
  return Math.round((toUtc - fromUtc) / 86_400_000);
}

/**
 * Convert a YYYY-MM-DD UTC date string or ISO timestamp to UTC start-of-day ISO string.
 */
export function toUtcRangeStartIso(raw: string): string | undefined {
  const trimmed = raw.trim();
  if (!trimmed) {
    return undefined;
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
    return `${trimmed}T00:00:00Z`;
  }
  return trimmed;
}

/**
 * Convert a YYYY-MM-DD UTC date string or ISO timestamp to UTC end-of-day ISO string.
 */
export function toUtcRangeEndIso(raw: string): string | undefined {
  const trimmed = raw.trim();
  if (!trimmed) {
    return undefined;
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) {
    return `${trimmed}T23:59:59.999Z`;
  }
  return trimmed;
}

/**
 * Extract YYYY-MM-DD date prefix from a date or ISO timestamp for <input type="date">.
 */
export function toDateInputValue(raw: string): string {
  const trimmed = raw.trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(trimmed)) {
    return trimmed.slice(0, 10);
  }
  return "";
}

/**
 * Derive StatusBadge stock domain key from server boolean flags.
 */
export function computeStockBadgeState(
  inStock: boolean,
  lowStock: boolean
): "out_of_stock" | "low_stock" | "in_stock" {
  if (!inStock) {
    return "out_of_stock";
  }
  return lowStock ? "low_stock" : "in_stock";
}

/**
 * Resolve a safe display message from an unknown thrown error.
 */
export function resolveErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return err.message || fallback;
  }
  if (err instanceof Error && err.message) {
    return err.message;
  }
  return fallback;
}
