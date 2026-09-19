"use client";

// Dev-only screen-navigation stand-in for real Supabase auth. It picks one of
// the contract's fixed test aliases so SC01~SC10 routes are reachable with
// the same role/membership rules the contract defines — it never calls a
// server and never claims to be authentication. Real login lands in A-03.
import { createContext, useCallback, useContext, useMemo, useState } from "react";
import {
  activeMembershipsFor,
  membershipFor,
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
}>;

const MockSessionContext = createContext<MockSessionValue | null>(null);

export function MockSessionProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  const [alias, setAlias] = useState<MockTestUserAlias | null>(null);

  const userId = alias ? testUserByAlias(alias).user_id : null;
  const activeMemberships = useMemo(() => (userId ? activeMembershipsFor(userId) : []), [userId]);

  const signIn = useCallback((next: MockTestUserAlias) => setAlias(next), []);
  const signOut = useCallback(() => setAlias(null), []);
  const membershipForBaby = useCallback(
    (babyId: string) => (userId ? membershipFor(userId, babyId) : null),
    [userId],
  );

  const value = useMemo<MockSessionValue>(
    () => ({ alias, userId, activeMemberships, signIn, signOut, membershipFor: membershipForBaby }),
    [alias, userId, activeMemberships, signIn, signOut, membershipForBaby],
  );

  return <MockSessionContext.Provider value={value}>{children}</MockSessionContext.Provider>;
}

export function useMockSession(): MockSessionValue {
  const value = useContext(MockSessionContext);
  if (!value) throw new Error("useMockSession must be called below MockSessionProvider.");
  return value;
}
