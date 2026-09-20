/** Explicit origins only; no credentials, arbitrary CDN or production unsafe-eval. */
export function securityHeaders(nonce: string, development: boolean, endpoints: readonly string[]) {
  const origins = new Set<string>();
  for (const endpoint of endpoints) {
    if (!endpoint) continue;
    const url = new URL(endpoint);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Invalid public endpoint");
    origins.add(url.origin);
    if (url.hostname.endsWith(".supabase.co") && !url.hostname.endsWith(".storage.supabase.co")) {
      origins.add(`${url.protocol}//${url.hostname.replace(".supabase.co", ".storage.supabase.co")}`);
    }
  }
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ""}`,
    // SEED and React use inline style properties for layout and overlay positioning.
    "style-src 'self' 'unsafe-inline'",
    `connect-src 'self' ${[...origins].join(" ")}${development ? " ws://localhost:* ws://127.0.0.1:*" : ""}`,
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "media-src 'self' blob:",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
  return {
    "Content-Security-Policy": csp,
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store, max-age=0",
  };
}
