import { NextResponse, type NextRequest } from "next/server";

const NO_STORE_PREFIXES = [
  "/admin",
  "/account",
  "/cart",
  "/orders",
  "/checkout",
] as const;

const NO_STORE_EXACT = new Set(["/login", "/register"]);

function deriveApiConnectOrigin(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL;
  const trimmed = raw ? raw.trim() : "";
  if (!trimmed) {
    return "http://localhost:8000";
  }
  if (trimmed.startsWith("/")) {
    return "";
  }
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.origin;
    }
  } catch {
    // Fall back to default local origin if env value is invalid
  }
  return "http://localhost:8000";
}

function generateNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

function buildContentSecurityPolicy(nonce: string): string {
  const scriptSrc =
    process.env.NODE_ENV !== "production"
      ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
      : `script-src 'self' 'nonce-${nonce}'`;

  const connectSources = ["'self'", deriveApiConnectOrigin()]
    .filter(Boolean)
    .join(" ");

  return [
    "default-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "font-src 'self'",
    "img-src 'self' https:",
    // Required for Recharts SVG inline styles and Radix UI portal positioning
    "style-src 'self' 'unsafe-inline'",
    scriptSrc,
    `connect-src ${connectSources}`,
  ].join("; ");
}

function shouldDisableStore(pathname: string): boolean {
  if (NO_STORE_EXACT.has(pathname)) {
    return true;
  }
  return NO_STORE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function middleware(request: NextRequest) {
  const nonce = generateNonce();
  const csp = buildContentSecurityPolicy(nonce);

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({
    request: {
      headers: requestHeaders,
    },
  });

  const { pathname } = request.nextUrl;
  const isAdminRoute = pathname === "/admin" || pathname.startsWith("/admin/");

  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set(
    "Referrer-Policy",
    isAdminRoute ? "no-referrer" : "strict-origin-when-cross-origin",
  );
  response.headers.set(
    "Permissions-Policy",
    "camera=(), microphone=(), geolocation=()",
  );

  if (shouldDisableStore(pathname)) {
    response.headers.set("Cache-Control", "no-store");
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|assets/|favicon\\.ico).*)"],
};
