/** Deterministic monetary, UTC timestamp, and security-validated URL/redirect utilities. */

export const FREE_SHIPPING_THRESHOLD_CENTS = 10_000;
export const STANDARD_SHIPPING_CENTS = 1_000;

const CONTROL_CHAR_RE = /[\u0000-\u001F\u007F]/;
const WHITESPACE_RE = /\s/;
const SCHEME_PREFIX_RE = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

/**
 * Format integer minor-unit cents using Intl.NumberFormat for display only.
 */
export function formatCents(cents: number, currency = "USD"): string {
  const safeCents = Number.isFinite(cents) ? cents : 0;
  const normalizedCurrency =
    typeof currency === "string" && /^[A-Z]{3}$/.test(currency.trim().toUpperCase())
      ? currency.trim().toUpperCase()
      : "USD";

  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: normalizedCurrency,
    }).format(safeCents / 100);
  } catch {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
    }).format(safeCents / 100);
  }
}

/**
 * Format an ISO-8601 timestamp deterministically in UTC (e.g., "Nov 18, 2026, 11:30 UTC").
 */
export function formatUtcTimestamp(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  const formatted = new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
  return `${formatted} UTC`;
}

/**
 * Calculate integer savings percentage when compare_at_price_cents > price_cents.
 */
export function computeSavingsPercent(
  priceCents: number,
  compareAtPriceCents: number | null | undefined
): number | null {
  if (
    compareAtPriceCents == null ||
    compareAtPriceCents <= 0 ||
    compareAtPriceCents <= priceCents
  ) {
    return null;
  }
  const pct = Math.round(((compareAtPriceCents - priceCents) / compareAtPriceCents) * 100);
  return pct > 0 ? pct : null;
}

/**
 * Strictly validate an internal relative returnTo path to prevent open redirects.
 * Allows only single-leading-"/" relative paths without "//", "\\", control chars, or schemes.
 */
export function sanitizeReturnTo(
  raw: string | null | undefined,
  fallback = "/account"
): string {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 512) {
    return fallback;
  }
  if (CONTROL_CHAR_RE.test(raw) || raw.includes("\\") || WHITESPACE_RE.test(raw)) {
    return fallback;
  }
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) {
    return fallback;
  }
  if (SCHEME_PREFIX_RE.test(raw)) {
    return fallback;
  }

  let current = raw;
  try {
    if (new URL(current, "https://app.local").origin !== "https://app.local") {
      return fallback;
    }
  } catch {
    return fallback;
  }

  for (let i = 0; i < 2; i += 1) {
    try {
      current = decodeURIComponent(current);
    } catch {
      return fallback;
    }
    if (
      CONTROL_CHAR_RE.test(current) ||
      current.includes("\\") ||
      current.startsWith("//") ||
      current.startsWith("/\\") ||
      SCHEME_PREFIX_RE.test(current)
    ) {
      return fallback;
    }
    try {
      if (new URL(current, "https://app.local").origin !== "https://app.local") {
        return fallback;
      }
    } catch {
      return fallback;
    }
  }

  return raw;
}

/**
 * Strictly validate product/category media URLs to safe https:// URLs or /assets/... paths.
 * Mirrors backend catalog.schemas.validate_product_image_url.
 */
export function sanitizeMediaUrl(url: string | null | undefined): string | null {
  if (typeof url !== "string") {
    return null;
  }
  const candidate = url.trim();
  if (!candidate) {
    return null;
  }
  if (CONTROL_CHAR_RE.test(candidate) || WHITESPACE_RE.test(candidate)) {
    return null;
  }

  let decodedPass = candidate;
  for (let i = 0; i < 3; i += 1) {
    if (
      CONTROL_CHAR_RE.test(decodedPass) ||
      WHITESPACE_RE.test(decodedPass) ||
      decodedPass.includes("..") ||
      decodedPass.includes("\\")
    ) {
      return null;
    }
    try {
      decodedPass = decodeURIComponent(decodedPass);
    } catch {
      return null;
    }
    if (
      CONTROL_CHAR_RE.test(decodedPass) ||
      WHITESPACE_RE.test(decodedPass) ||
      decodedPass.includes("..") ||
      decodedPass.includes("\\")
    ) {
      return null;
    }
  }

  if (candidate.startsWith("https://")) {
    try {
      const parsed = new URL(candidate);
      if (
        parsed.protocol !== "https:" ||
        !parsed.hostname ||
        parsed.username !== "" ||
        parsed.password !== ""
      ) {
        return null;
      }
      return candidate;
    } catch {
      return null;
    }
  }

  if (candidate.startsWith("/assets/")) {
    if (candidate.startsWith("//")) {
      return null;
    }
    try {
      const parsed = new URL(candidate, "https://assets.local");
      if (
        parsed.origin !== "https://assets.local" ||
        !parsed.pathname.startsWith("/assets/") ||
        parsed.pathname.length <= "/assets/".length
      ) {
        return null;
      }
      return candidate;
    } catch {
      return null;
    }
  }

  return null;
}

/**
 * Parse a user-entered dollar string (e.g., "45.50") safely into non-negative integer cents.
 */
export function parseDollarsToCents(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }
  if (!/^\d+(?:\.\d{1,2})?$/.test(trimmed)) {
    return null;
  }
  const [dollarsPart, centsPart = ""] = trimmed.split(".");
  const dollars = Number.parseInt(dollarsPart, 10);
  const cents = Number.parseInt(centsPart.padEnd(2, "0"), 10);
  const total = dollars * 100 + cents;
  if (!Number.isSafeInteger(total) || total < 0 || total > 2_147_483_647) {
    return null;
  }
  return total;
}

/**
 * Format integer cents to a clean decimal string for input fields (e.g. 4500 -> "45.00" or "45").
 */
export function formatCentsForInput(cents: number | null | undefined): string {
  if (cents == null || !Number.isFinite(cents) || cents < 0) {
    return "";
  }
  if (cents % 100 === 0) {
    return String(cents / 100);
  }
  return (cents / 100).toFixed(2);
}
