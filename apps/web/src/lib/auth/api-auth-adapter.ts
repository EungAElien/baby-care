import type { ApiAuthAdapter, ApiSession } from "@/lib/api/client";
import { getSupabaseClient } from "@/lib/auth/supabase-client";

/** Bridges the real Supabase session into createApiClient's 1-retry-after-401 refresh contract. */
export const supabaseAuthAdapter: ApiAuthAdapter = {
  async getSession(): Promise<ApiSession | null> {
    const { data, error } = await getSupabaseClient().auth.getSession();
    if (error || !data.session) return null;
    return { userId: data.session.user.id, accessToken: data.session.access_token };
  },

  async refreshSession(): Promise<ApiSession | null> {
    const { data, error } = await getSupabaseClient().auth.refreshSession();
    if (error || !data.session) return null;
    return { userId: data.session.user.id, accessToken: data.session.access_token };
  },
};
