"use client";

import { useMemo } from "react";
import { usePrivateScope } from "@/components/app-providers";
import { supabaseAuthAdapter } from "@/lib/auth/api-auth-adapter";
import { tryReadPublicConfig } from "@/lib/public-config";
import { createApiClient } from "@/lib/api/client";

export type RealApiClient = ReturnType<typeof createApiClient>;

/**
 * The real, Supabase-backed API client, or null when no real Supabase/API
 * environment is configured (plain mock-nav-only local dev). Callers must
 * check for null rather than assume it's always available.
 */
export function useApiClient(): RealApiClient | null {
  const scope = usePrivateScope();
  return useMemo(() => {
    const config = tryReadPublicConfig();
    if (!config) return null;
    return createApiClient({ baseUrl: config.apiBaseUrl, auth: supabaseAuthAdapter, scope });
  }, [scope]);
}
