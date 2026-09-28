import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { tryReadPublicConfig } from "@/lib/public-config";

let cached: SupabaseClient | null = null;

export class SupabaseNotConfiguredError extends Error {
  constructor() {
    super("Supabase URL/publishable key are not configured for this build.");
    this.name = "SupabaseNotConfiguredError";
  }
}

/**
 * Lazy singleton — only reads env and constructs the client on first real
 * auth action, so a build running purely on the dev mock nav (no real
 * Supabase project configured) never throws just by rendering /login.
 *
 * Token storage note: this uses supabase-js's default (persisted, refreshed
 * automatically). The dev contract lists browser token storage as an open,
 * A-owned decision still pending review (tab-session vs persisted vs
 * memory-only) — treat this default as provisional, not a final answer.
 */
export function getSupabaseClient(): SupabaseClient {
  if (typeof window === "undefined") {
    throw new Error("The Supabase client must be created in the browser.");
  }
  if (cached) return cached;
  const config = tryReadPublicConfig();
  if (!config) throw new SupabaseNotConfiguredError();
  cached = createClient(config.supabaseUrl, config.supabasePublishableKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
  });
  return cached;
}
