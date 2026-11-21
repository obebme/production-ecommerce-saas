const CONTROL_CHAR_RE = /[\u0000-\u001F\u007F]/;
const DEPLOYED_RUNTIME_ENVS = new Set(["staging", "production"]);
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"]);

/**
 * Returns true only when an explicit runtime environment variable is set to
 * "staging" or "production" (never inferred from NODE_ENV alone).
 */
export function isDeployedRuntimeEnv(envValue?: string): boolean {
  const raw =
    envValue !== undefined
      ? envValue
      : process.env.NEXT_PUBLIC_APP_ENV || process.env.APP_ENV || process.env.ENVIRONMENT || "";
  const normalized = raw.trim().toLowerCase();
  return DEPLOYED_RUNTIME_ENVS.has(normalized);
}

/**
 * Resolve and validate NEXT_PUBLIC_SITE_URL into an origin-only URL for metadataBase.
 */
export function resolveSiteUrl(
  rawUrl?: string,
  options?: { deployedEnv?: boolean }
): URL {
  const deployedEnv = options?.deployedEnv ?? isDeployedRuntimeEnv();
  const candidate = (rawUrl !== undefined ? rawUrl : process.env.NEXT_PUBLIC_SITE_URL ?? "").trim();

  if (!candidate) {
    if (deployedEnv) {
      throw new Error(
        "NEXT_PUBLIC_SITE_URL must be configured to a non-localhost https:// URL in staging/production."
      );
    }
    return new URL("http://localhost:3000");
  }

  if (CONTROL_CHAR_RE.test(candidate)) {
    throw new Error("NEXT_PUBLIC_SITE_URL must not contain control characters.");
  }

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error("NEXT_PUBLIC_SITE_URL must be a valid http:// or https:// URL.");
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("NEXT_PUBLIC_SITE_URL must use http:// or https://.");
  }

  if (parsed.username || parsed.password) {
    throw new Error("NEXT_PUBLIC_SITE_URL must not include credentials.");
  }

  if (parsed.search || parsed.hash) {
    throw new Error("NEXT_PUBLIC_SITE_URL must not include query parameters or fragments.");
  }

  if (deployedEnv) {
    if (parsed.protocol !== "https:") {
      throw new Error("NEXT_PUBLIC_SITE_URL must use https:// in staging/production.");
    }
    const hostname = parsed.hostname.toLowerCase();
    if (LOOPBACK_HOSTS.has(hostname) || hostname.endsWith(".localhost")) {
      throw new Error(
        "NEXT_PUBLIC_SITE_URL must not target a loopback or wildcard host in staging/production."
      );
    }
  }

  return new URL(parsed.origin);
}
