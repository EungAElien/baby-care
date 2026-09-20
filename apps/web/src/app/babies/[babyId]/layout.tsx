"use client";

// Shared shell for SC02~SC10. Real Supabase membership (A-03) is checked
// first; the mock nav path only exists at all when NEXT_PUBLIC_ENABLE_MOCK_NAV
// is on. `isMockNavEnabled()` is a build-time constant (inlined from env), so
// branching the whole subtree on it once — rather than conditionally calling
// useMockSession inside a single component — keeps every hook call
// unconditional within whichever component actually renders.
import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useRealSession } from "@/lib/auth/real-session";
import { useBabiesQuery, findBabyAccess } from "@/lib/api/babies";
import { useSharedChangePolling } from "@/lib/api/changes";
import { useMockSession } from "@/lib/mock/session";
import { isMockNavEnabled } from "@/lib/mock/config";
import { mockBabyLabel } from "@/lib/mock/fixtures";
import { useSyncPrivateScopeForBaby } from "@/components/app-providers";
import { BabyShell, type OtherBaby } from "@/components/baby-shell";
import { ErrorState, LoadingState } from "@/components/screen-state";
import { usePrivateScope } from "@/components/app-providers";
import { useApiClient } from "@/lib/api/real-client";
import { revokeAndSignOut } from "@/lib/auth/session-control";

function NotFoundShell() {
  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-3 px-6">
      <ErrorState label="찾을 수 없거나 접근할 수 없어요." />
      <p className="text-sm text-muted-foreground">
        이 아기의 활성 구성원이 아니면 실제 서버도 같은 404를 반환하고 존재 여부를 알리지 않습니다.
      </p>
    </main>
  );
}

/** Renders once `real.status === "signed-in"` — the babies query is only enabled then. */
function RealBabyContent({ babyId, children }: Readonly<{ babyId: string; children: React.ReactNode }>) {
  const router = useRouter();
  const real = useRealSession();
  const babies = useBabiesQuery(true);
  const scope = usePrivateScope();
  const client = useApiClient();
  // A-08 ①: foreground /changes polling for this baby scope. B-09 handoff §A의 안전한 폴링·복구 순서.
  useSharedChangePolling(babyId, true);

  if (babies.isLoading) return <LoadingState label="아기 정보를 불러오고 있어요" />;
  if (babies.isError) return <ErrorState label="아기 정보를 불러오지 못했어요." retryable />;

  const match = findBabyAccess(babies.data, babyId);
  if (!match) return <NotFoundShell />;

  const otherBabies: readonly OtherBaby[] = (babies.data?.items ?? [])
    .filter((item) => item.baby.baby_id !== babyId)
    .map((item) => ({ babyId: item.baby.baby_id, label: item.baby.alias }));

  return (
    <BabyShell
      babyId={babyId}
      babyLabel={match.baby.alias}
      role={match.membership.role}
      otherBabies={otherBabies}
      onSignOut={async () => {
        const outcome = await revokeAndSignOut(client, scope, real.signOut, "CURRENT");
        if (outcome === "local-failed") throw new Error("Local sign-out was not confirmed.");
        router.replace(`/login?logout=${outcome}`);
      }}
    >
      {children}
    </BabyShell>
  );
}

/** Used when NEXT_PUBLIC_ENABLE_MOCK_NAV is off — no MockSessionProvider exists, so this never touches mock hooks. */
function RealOnlyBabyLayout({ babyId, children }: Readonly<{ babyId: string; children: React.ReactNode }>) {
  const router = useRouter();
  const real = useRealSession();
  const scopeReady = useSyncPrivateScopeForBaby(real.userId, babyId);

  useEffect(() => {
    if (real.status === "signed-out") router.replace("/login");
  }, [real.status, router]);

  if (real.status !== "signed-in") return null;
  if (!scopeReady) return <LoadingState label="아기 정보를 준비하고 있어요" />;
  return <RealBabyContent babyId={babyId}>{children}</RealBabyContent>;
}

/** Used when NEXT_PUBLIC_ENABLE_MOCK_NAV is on — real identity still wins when both exist. */
function HybridBabyLayout({ babyId, children }: Readonly<{ babyId: string; children: React.ReactNode }>) {
  const router = useRouter();
  const real = useRealSession();
  const mock = useMockSession();
  const activeUserId = real.status === "signed-in" ? real.userId : mock.userId;
  const scopeReady = useSyncPrivateScopeForBaby(activeUserId, babyId);

  useEffect(() => {
    if (real.status === "loading") return;
    if (real.status === "signed-out" && !mock.alias) router.replace("/login");
  }, [real.status, mock.alias, router]);

  if (real.status === "loading") return null;
  if (activeUserId && !scopeReady) return <LoadingState label="아기 정보를 준비하고 있어요" />;
  if (real.status === "signed-in") return <RealBabyContent babyId={babyId}>{children}</RealBabyContent>;

  if (!mock.alias) return null;
  const membership = mock.membershipFor(babyId);
  if (!membership) return <NotFoundShell />;

  const otherBabies: readonly OtherBaby[] = mock.activeMemberships
    .filter((entry) => entry.baby_id !== babyId)
    .map((entry) => ({ babyId: entry.baby_id, label: mockBabyLabel(entry.baby_id) }));

  return (
    <BabyShell
      babyId={babyId}
      babyLabel={mockBabyLabel(babyId)}
      demo
      role={membership.role}
      otherBabies={otherBabies}
      onSignOut={() => {
        mock.signOut();
        router.push("/login");
      }}
    >
      {children}
    </BabyShell>
  );
}

export default function BabyLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const { babyId } = useParams<{ babyId: string }>();
  if (isMockNavEnabled()) {
    return <HybridBabyLayout babyId={babyId}>{children}</HybridBabyLayout>;
  }
  return <RealOnlyBabyLayout babyId={babyId}>{children}</RealOnlyBabyLayout>;
}
