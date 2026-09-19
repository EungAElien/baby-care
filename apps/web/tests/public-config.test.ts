import { afterEach, describe, expect, it, vi } from "vitest";
import { readPublicConfig, validateApiBaseUrl } from "../src/lib/public-config";

afterEach(() => vi.unstubAllEnvs());

describe("public browser configuration", () => {
  it("accepts only the configured /v1 HTTPS endpoint or local development HTTP", () => {
    expect(validateApiBaseUrl("https://api.example.invalid/v1/")).toBe("https://api.example.invalid/v1");
    expect(validateApiBaseUrl("http://localhost:8000/v1")).toBe("http://localhost:8000/v1");
    expect(() => validateApiBaseUrl("http://api.example.invalid/v1")).toThrow();
    expect(() => validateApiBaseUrl("https://api.example.invalid/other")).toThrow();
    expect(() => validateApiBaseUrl("https://name:password@api.example.invalid/v1")).toThrow();
  });

  it("rejects privileged keys from public configuration", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.invalid/v1");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "sb_secret_not_public");
    expect(() => readPublicConfig()).toThrow();
  });
});
