/** @type {import('next').NextConfig} */
function deriveApiConnectOrigin() {
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

const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  images: {
    unoptimized: true,
  },
  async headers() {
    const scriptSrc =
      process.env.NODE_ENV !== "production"
        ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
        : "script-src 'self' 'unsafe-inline'";

    const connectSources = ["'self'", deriveApiConnectOrigin()].filter(Boolean).join(" ");

    const csp = [
      "default-src 'self'",
      "object-src 'none'",
      "base-uri 'self'",
      "frame-ancestors 'none'",
      "form-action 'self'",
      "font-src 'self'",
      "img-src 'self' https:",
      "style-src 'self' 'unsafe-inline'",
      scriptSrc,
      `connect-src ${connectSources}`,
    ].join("; ");

    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
          { key: "Content-Security-Policy", value: csp },
        ],
      },
      {
        source: "/admin/:path*",
        headers: [
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Cache-Control", value: "no-store" },
        ],
      },
      {
        source: "/account/:path*",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
      {
        source: "/orders/:path*",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
      {
        source: "/checkout/:path*",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
      {
        source: "/login",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
      {
        source: "/register",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
    ];
  },
};

export default nextConfig;
