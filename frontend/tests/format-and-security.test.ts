import { describe, expect, it } from "vitest";
// @ts-expect-error -- next.config.mjs is untyped ESM JS while tsconfig sets allowJs: false
import nextConfig from "../next.config.mjs";
import {
  computeSavingsPercent,
  formatCents,
  formatCentsForInput,
  formatUtcTimestamp,
  parseDollarsToCents,
  sanitizeMediaUrl,
  sanitizeReturnTo,
} from "@/lib/format";

interface NextHeaderEntry {
  key: string;
  value: string;
}

interface NextHeaderRule {
  source: string;
  headers: NextHeaderEntry[];
}

describe("formatCents & formatUtcTimestamp", () => {
  it("formats minor-unit cents deterministically across currencies and fallbacks", () => {
    expect(formatCents(0)).toBe("$0.00");
    expect(formatCents(4500, "USD")).toBe("$45.00");
    expect(formatCents(1299, "usd")).toBe("$12.99");
    expect(formatCents(4500, "eur")).toContain("45.00");
    expect(formatCents(4500, "INVALID")).toBe("$45.00");
    expect(formatCents(Number.NaN)).toBe("$0.00");
    expect(formatCents(Number.POSITIVE_INFINITY)).toBe("$0.00");
  });

  it("formats UTC ISO timestamps and falls back to em-dash for invalid inputs", () => {
    expect(formatUtcTimestamp("2026-11-18T11:30:00Z")).toBe("Nov 18, 2026, 11:30 UTC");
    expect(formatUtcTimestamp(null)).toBe("—");
    expect(formatUtcTimestamp(undefined)).toBe("—");
    expect(formatUtcTimestamp("")).toBe("—");
    expect(formatUtcTimestamp("not-a-timestamp")).toBe("—");
  });
});

describe("computeSavingsPercent & price input helpers", () => {
  it("computes rounded integer savings percentages only for valid discounts", () => {
    expect(computeSavingsPercent(4500, 6000)).toBe(25);
    expect(computeSavingsPercent(4900, 6000)).toBe(18);
    expect(computeSavingsPercent(4500, null)).toBeNull();
    expect(computeSavingsPercent(4500, undefined)).toBeNull();
    expect(computeSavingsPercent(4500, 0)).toBeNull();
    expect(computeSavingsPercent(4500, 4500)).toBeNull();
    expect(computeSavingsPercent(5000, 4500)).toBeNull();
    expect(computeSavingsPercent(999, 1000)).toBeNull();
  });

  it("parses decimal dollar strings to integer cents and rejects malformed values", () => {
    expect(parseDollarsToCents("0")).toBe(0);
    expect(parseDollarsToCents("45")).toBe(4500);
    expect(parseDollarsToCents("45.5")).toBe(4550);
    expect(parseDollarsToCents("45.50")).toBe(4550);
    expect(parseDollarsToCents("  19.99  ")).toBe(1999);

    expect(parseDollarsToCents("")).toBeNull();
    expect(parseDollarsToCents("   ")).toBeNull();
    expect(parseDollarsToCents("-10.00")).toBeNull();
    expect(parseDollarsToCents("12.345")).toBeNull();
    expect(parseDollarsToCents("$12.00")).toBeNull();
    expect(parseDollarsToCents("abc")).toBeNull();
    expect(parseDollarsToCents("99999999999")).toBeNull();
  });

  it("formats cents cleanly for controlled dollar inputs", () => {
    expect(formatCentsForInput(0)).toBe("0");
    expect(formatCentsForInput(4500)).toBe("45");
    expect(formatCentsForInput(4550)).toBe("45.50");
    expect(formatCentsForInput(5)).toBe("0.05");
    expect(formatCentsForInput(null)).toBe("");
    expect(formatCentsForInput(undefined)).toBe("");
    expect(formatCentsForInput(-1)).toBe("");
    expect(formatCentsForInput(Number.NaN)).toBe("");
  });
});

describe("sanitizeReturnTo", () => {
  it("accepts safe internal relative paths", () => {
    expect(sanitizeReturnTo("/checkout")).toBe("/checkout");
    expect(sanitizeReturnTo("/admin/products?status=ACTIVE")).toBe(
      "/admin/products?status=ACTIVE"
    );
    expect(sanitizeReturnTo("/products/merino-coat#sizing")).toBe(
      "/products/merino-coat#sizing"
    );
  });

  it("rejects open redirects, protocols, encoded slashes/backslashes, and control characters", () => {
    const unsafeInputs: Array<string | null | undefined> = [
      "//evil.com",
      "https://evil.com",
      "http://localhost:3000/x",
      "javascript:alert(1)",
      "/\\evil.com",
      "/%2f/evil.com",
      "/%2fevil.com",
      "/%252fevil.com",
      "/%5cevil.com",
      "/checkout\x00",
      "/checkout\n",
      "/checkout%0d%0a",
      "/checkout with space",
      "/%E0%A4%A",
      "",
      null,
      undefined,
    ];

    for (const candidate of unsafeInputs) {
      expect(sanitizeReturnTo(candidate)).toBe("/account");
      expect(sanitizeReturnTo(candidate, "/admin")).toBe("/admin");
    }
  });
});

describe("sanitizeMediaUrl", () => {
  it("allows safe https:// URLs and /assets/... paths", () => {
    expect(sanitizeMediaUrl("https://cdn.example.com/a.webp")).toBe(
      "https://cdn.example.com/a.webp"
    );
    expect(sanitizeMediaUrl("/assets/tee.png")).toBe("/assets/tee.png");
    expect(sanitizeMediaUrl("  https://cdn.example.com/lookbook/coat.jpg?v=2  ")).toBe(
      "https://cdn.example.com/lookbook/coat.jpg?v=2"
    );
  });

  it("rejects insecure schemes, embedded credentials, path traversal, backslashes, and control characters", () => {
    const unsafeUrls: Array<string | null | undefined> = [
      "http://cdn.example.com/a.webp",
      "javascript:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "data:text/html,hello",
      "https://user:pass@cdn.example.com/a.png",
      "https://user@cdn.example.com/a.png",
      "/assets/../secret.png",
      "/assets/%2e%2e/secret.png",
      "/assets/%252e%252e/secret.png",
      "https://cdn.example.com/%25252e%25252e/secret.png",
      "/assets/img%25250a.png",
      "https://cdn.example.com/a.png%0d%0a",
      "/assets/sub\\file.png",
      "https://cdn.example.com/a\\b.png",
      "https://cdn.example.com/a.png\x00",
      "/assets/a\n.png",
      "/assets/a\r.png",
      "/assets/a\t.png",
      "/assets/a.png\x00",
      "/assets/",
      "/other/tee.png",
      "",
      null,
      undefined,
    ];

    for (const candidate of unsafeUrls) {
      expect(sanitizeMediaUrl(candidate)).toBeNull();
    }
  });
});

describe("Next.js security headers (next.config.mjs)", () => {
  it("disables X-Powered-By and sets required baseline and admin security headers", async () => {
    expect(nextConfig.poweredByHeader).toBe(false);

    const rules = (await nextConfig.headers()) as NextHeaderRule[];
    const globalRule = rules.find((rule) => rule.source === "/:path*");
    const adminRule = rules.find((rule) => rule.source === "/admin/:path*");

    expect(globalRule).toBeDefined();
    expect(adminRule).toBeDefined();

    const globalMap = new Map(
      (globalRule?.headers ?? []).map((entry) => [entry.key, entry.value])
    );
    expect(globalMap.get("X-Frame-Options")).toBe("DENY");
    expect(globalMap.get("X-Content-Type-Options")).toBe("nosniff");
    expect(globalMap.get("Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(globalMap.get("Permissions-Policy")).toBeTruthy();

    const csp = globalMap.get("Content-Security-Policy") ?? "";
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("form-action 'self'");

    const adminMap = new Map(
      (adminRule?.headers ?? []).map((entry) => [entry.key, entry.value])
    );
    expect(adminMap.get("Referrer-Policy")).toBe("no-referrer");
    expect(adminMap.get("Cache-Control")).toBe("no-store");
  });
});
