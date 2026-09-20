"use client";
import { useRealSession } from "@/lib/auth/real-session";
import { isMockNavEnabled } from "@/lib/mock/config";
/** A real session never falls back to fixtures, even when demo navigation is enabled. */
export function DemoOnly({
  children,
  fallback = null,
}: Readonly<{ children: React.ReactNode; fallback?: React.ReactNode }>) {
  const real = useRealSession();
  return isMockNavEnabled() && real.status === "signed-out"
    ? children
    : fallback;
}
