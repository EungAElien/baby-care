"use client";

// Dev-only screen-navigation stand-in for real Supabase auth. It picks one of
// the contract's fixed test aliases so SC01~SC10 routes are reachable with
// the same role/membership rules the contract defines — it never calls a
// server and never claims to be authentication. Real login lands in A-03's
// follow-up once B provides live Supabase test accounts.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePrivateScope } from "@/components/app-providers";
import {
  activeMembershipsFor,
  testUserByAlias,
  type MockRoleAssignment,
  type MockTestUserAlias,
} from "@/lib/mock/fixtures";

type MockSessionValue = Readonly<{
  alias: MockTestUserAlias | null;
  userId: string | null;
  activeMemberships: readonly MockRoleAssignment[];
  signIn: (alias: MockTestUserAlias) => void;
  signOut: () => void;
  membershipFor: (babyId: string) => MockRoleAssignment | null;
  /** Ephemeral, tab-memory only — replays the invite_accepted fixture's own membership, never a new rule. */
  acceptInvite: (babyId: string, role: "CAREGIVER") => void;
  /** Ephemeral, tab-memory only — mirrors the member_left fixture's LEFT status for this session. */
  leaveBaby: (babyId: string) => void;
}>;

const MockSessionContext = createContext<MockSessionValue | null>(null);

export function MockSessionProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  const [alias, setAlias] = useState<MockTestUserAlias | null>(null);
  const [addedMemberships, setAddedMemberships] = useState<readonly MockRoleAssignment[]>([]);
  const [leftBabyIds, setLeftBabyIds] = useState<ReadonlySet<string>>(new Set());
  const scope = usePrivateScope();

  const userId = alias ? testUserByAlias(alias).user_id : null;

  const activeMemberships = useMemo(() => {
    if (!userId) return [];
    const base = activeMembershipsFor(userId).filter((entry) => !leftBabyIds.has(entry.baby_id));
    const added = addedMemberships.filter((entry) => entry.user_id === userId);
    return [...base, ...added];
  }, [userId, addedMemberships, leftBabyIds]);

  const signIn = useCallback(
    (next: MockTestUserAlias) => {
      setAlias(next);
      setAddedMemberships([]);
      setLeftBabyIds(new Set());
      scope.set(testUserByAlias(next).user_id, null);
    },
    [scope],
  );

  const signOut = useCallback(() => {
    setAlias(null);
    setAddedMemberships([]);
    setLeftBabyIds(new Set());
    scope.reset();
  }, [scope]);

  const membershipForBaby = useCallback(
    (babyId: string) => activeMemberships.find((entry) => entry.baby_id === babyId) ?? null,
    [activeMemberships],
  );

  const acceptInvite = useCallback(
    (babyId: string, role: "CAREGIVER") => {
      if (!userId) return;
      setLeftBabyIds((prev) => {
        if (!prev.has(babyId)) return prev;
        const next = new Set(prev);
        next.delete(babyId);
        return next;
      });
      setAddedMemberships((prev) =>
        prev.some((entry) => entry.baby_id === babyId && entry.user_id === userId)
          ? prev
          : [...prev, { user_id: userId, baby_id: babyId, role, status: "ACTIVE" }],
      );
    },
    [userId],
  );

  const leaveBaby = useCallback((babyId: string) => {
    setLeftBabyIds((prev) => new Set(prev).add(babyId));
    setAddedMemberships((prev) => prev.filter((entry) => entry.baby_id !== babyId));
  }, []);

  const value = useMemo<MockSessionValue>(
    () => ({
      alias,
      userId,
      activeMemberships,
      signIn,
      signOut,
      membershipFor: membershipForBaby,
      acceptInvite,
      leaveBaby,
    }),
    [alias, userId, activeMemberships, signIn, signOut, membershipForBaby, acceptInvite, leaveBaby],
  );

  return <MockSessionContext.Provider value={value}>{children}</MockSessionContext.Provider>;
}

export function useMockSession(): MockSessionValue {
  const value = useContext(MockSessionContext);
  if (!value) throw new Error("useMockSession must be called below MockSessionProvider.");
  return value;
}

/** Keeps the real PrivateScope's (user_id, baby_id) snapshot in sync with the current route's baby. */
export function useSyncPrivateScope(babyId: string): void {
  const session = useMockSession();
  const scope = usePrivateScope();
  const lastKey = useRef<string | null>(null);

  useEffect(() => {
    const key = `${session.userId ?? ""}:${babyId}`;
    if (lastKey.current === key) return;
    lastKey.current = key;
    if (session.userId) scope.set(session.userId, babyId);
  }, [session.userId, babyId, scope]);
}
