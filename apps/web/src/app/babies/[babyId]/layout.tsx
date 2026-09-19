"use client";

// Shared shell for SC02~SC10. Membership comes only from the mock session's
// fixture-derived ACTIVE role assignments — a non-member sees the same
// hidden-existence shape the contract requires for real 404s (§2).
import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { useMockSession, useSyncPrivateScope } from "@/lib/mock/session";
import { BabyShell } from "@/components/baby-shell";
import { ErrorState } from "@/components/screen-state";

export default function BabyLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const { babyId } = useParams<{ babyId: string }>();
  const router = useRouter();
  const session = useMockSession();
  useSyncPrivateScope(babyId);

  useEffect(() => {
    if (!session.alias) router.replace("/login");
  }, [session.alias, router]);

  if (!session.alias) return null;

  const membership = session.membershipFor(babyId);
  if (!membership) {
    return (
      <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-3 px-6">
        <ErrorState label="찾을 수 없거나 접근할 수 없어요." />
        <p className="text-sm text-muted-foreground">
          이 아기의 활성 구성원이 아니면 실제 서버도 같은 404를 반환하고 존재 여부를 알리지 않습니다.
        </p>
      </main>
    );
  }

  return (
    <BabyShell babyId={babyId} role={membership.role}>
      {children}
    </BabyShell>
  );
}
