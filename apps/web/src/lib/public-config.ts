import { z } from "zod";

const localHostnames = new Set(["localhost", "127.0.0.1", "[::1]"]);

function safePublicUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      (url.protocol === "https:" || (url.protocol === "http:" && localHostnames.has(url.hostname)))
    );
  } catch {
    return false;
  }
}

const publicConfigSchema = z.object({
  apiBaseUrl: z.url().refine(safePublicUrl).refine((value) => new URL(value).pathname.replace(/\/$/, "") === "/v1"),
  supabaseUrl: z.url().refine(safePublicUrl),
  supabasePublishableKey: z.string().min(1).refine((value) => !/secret|service_role/i.test(value)),
});

export type PublicConfig = z.infer<typeof publicConfigSchema>;

/** Evaluated in the browser when Auth/API wiring is added; not needed for the public shell build. */
export function readPublicConfig(): PublicConfig {
  return publicConfigSchema.parse({
    apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL,
    supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL,
    supabasePublishableKey: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
  });
}

export function validateApiBaseUrl(baseUrl: string): string {
  const result = publicConfigSchema.shape.apiBaseUrl.parse(baseUrl);
  return result.replace(/\/$/, "");
}
