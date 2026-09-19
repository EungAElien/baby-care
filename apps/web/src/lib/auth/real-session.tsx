"use client";

// Real Supabase email-OTP session (A-03). Always mounted — unlike the mock
// nav session, this is the production auth path and is not flag-gated. When
// no real Supabase project is configured (plain local dev on the mock nav
// only) it stays in "signed-out" without ever touching the network.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { usePrivateScope } from "@/components/app-providers";
import { getSupabaseClient } from "@/lib/auth/supabase-client";
import { tryReadPublicConfig } from "@/lib/public-config";

export type RealSessionStatus = "loading" | "signed-out" | "signed-in";

type OtpResult = Readonly<{ ok: boolean; error: string | null }>;

type RealSessionValue = Readonly<{
  configured: boolean;
  status: RealSessionStatus;
  userId: string | null;
  email: string | null;
  requestOtp: (email: string) => Promise<OtpResult>;
  verifyOtp: (email: string, token: string) => Promise<OtpResult>;
  signOut: () => Promise<void>;
}>;

const RealSessionContext = createContext<RealSessionValue | null>(null);

export function RealSessionProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  const scope = usePrivateScope();
  const configured = tryReadPublicConfig() !== null;
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<RealSessionStatus>(configured ? "loading" : "signed-out");
  const realUserId = useRef<string | null>(null);

  useEffect(() => {
    if (!configured) return;
    const supabase = getSupabaseClient();
    const { data: subscription } = supabase.auth.onAuthStateChange((event, next) => {
      setSession(next);
      setStatus(next ? "signed-in" : "signed-out");
      // Supabase may emit SIGNED_IN again for the same recovered session.
      // Only an identity change discards the selected baby's scope.
      if (next?.user.id) {
        if (realUserId.current !== next.user.id || scope.snapshot().userId !== next.user.id) {
          scope.set(next.user.id, null);
        }
        realUserId.current = next.user.id;
      } else if (event === "SIGNED_OUT") {
        realUserId.current = null;
        scope.reset();
      }
    });
    return () => subscription.subscription.unsubscribe();
    // Deliberately omit `scope` from deps: PrivateScope is a stable singleton for the app's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configured]);

  const requestOtp = useCallback(
    async (email: string): Promise<OtpResult> => {
      if (!configured) return { ok: false, error: "실제 Supabase 환경이 설정되지 않았어요." };
      const { error } = await getSupabaseClient().auth.signInWithOtp({ email, options: { shouldCreateUser: true } });
      return { ok: !error, error: error?.message ?? null };
    },
    [configured],
  );

  const verifyOtp = useCallback(
    async (email: string, token: string): Promise<OtpResult> => {
      if (!configured) return { ok: false, error: "실제 Supabase 환경이 설정되지 않았어요." };
      const { error } = await getSupabaseClient().auth.verifyOtp({ email, token, type: "email" });
      return { ok: !error, error: error?.message ?? null };
    },
    [configured],
  );

  const signOut = useCallback(async () => {
    if (!configured) return;
    await getSupabaseClient().auth.signOut();
  }, [configured]);

  const value = useMemo<RealSessionValue>(
    () => ({
      configured,
      status,
      userId: session?.user.id ?? null,
      email: session?.user.email ?? null,
      requestOtp,
      verifyOtp,
      signOut,
    }),
    [configured, status, session, requestOtp, verifyOtp, signOut],
  );

  return <RealSessionContext.Provider value={value}>{children}</RealSessionContext.Provider>;
}

export function useRealSession(): RealSessionValue {
  const value = useContext(RealSessionContext);
  if (!value) throw new Error("useRealSession must be called below RealSessionProvider.");
  return value;
}
