import { describe, expect, it } from "vitest";
import { securityHeaders } from "../src/lib/security-headers";

describe("production browser boundaries", () => {
  it("uses a nonce, exact API/storage origins, no shared cache, and microphone-only permission", () => {
    const headers = securityHeaders("request-nonce", false, [
      "https://api.example.test/v1",
      "https://project.supabase.co",
    ]);
    expect(headers["Content-Security-Policy"]).toContain(
      "'nonce-request-nonce'",
    );
    expect(headers["Content-Security-Policy"]).toContain(
      "https://project.storage.supabase.co",
    );
    expect(headers["Content-Security-Policy"]).not.toMatch(
      /unsafe-eval|https:\/\/\*/,
    );
    expect(headers["Cache-Control"]).toContain("no-store");
    expect(headers["Referrer-Policy"]).toBe("no-referrer");
    expect(headers["Permissions-Policy"]).toBe(
      "microphone=(self), camera=(), geolocation=()",
    );
    expect(
      securityHeaders("different", false, [])["Content-Security-Policy"],
    ).not.toBe(headers["Content-Security-Policy"]);
  });
  it("rejects credential-bearing or non-HTTP endpoints", () => {
    expect(() =>
      securityHeaders("n", false, ["https://secret@example.test"]),
    ).toThrow();
    expect(() =>
      securityHeaders("n", false, ["javascript:alert(1)"]),
    ).toThrow();
  });
  it("restricts debugging eval to development", () => {
    expect(securityHeaders("n", true, [])["Content-Security-Policy"]).toContain(
      "'unsafe-eval'",
    );
  });
});
