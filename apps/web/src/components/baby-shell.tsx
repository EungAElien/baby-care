"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Home, Clock, BarChart3, Settings } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDraft } from "@/lib/mock/draft";

type NavItem = Readonly<{ href: string; label: string; icon: typeof Home }>;

function navItems(babyId: string): readonly NavItem[] {
  return [
    { href: `/babies/${babyId}`, label: "홈", icon: Home },
    { href: `/babies/${babyId}/timeline`, label: "타임라인", icon: Clock },
    { href: `/babies/${babyId}/summary`, label: "요약", icon: BarChart3 },
    { href: `/babies/${babyId}/settings`, label: "설정", icon: Settings },
  ];
}

export type OtherBaby = Readonly<{ babyId: string; label: string }>;

/**
 * Mobile-first shell shared by SC02~SC10: sticky baby header + bottom tab
 * bar. Purely presentational — the real and mock baby layouts each resolve
 * identity and pass the result in, so this component doesn't care which one
 * is active.
 */
export function BabyShell({
  babyId,
  babyLabel,
  role,
  otherBabies,
  onSignOut,
  children,
}: Readonly<{
  babyId: string;
  babyLabel: string;
  role: "OWNER" | "CAREGIVER";
  otherBabies: readonly OtherBaby[];
  onSignOut: () => void;
  children: React.ReactNode;
}>) {
  const pathname = usePathname();
  const router = useRouter();
  const draft = useDraft();
  const items = navItems(babyId);
  const [pendingSwitch, setPendingSwitch] = useState<string | null>(null);

  function requestSwitch(nextBabyId: string) {
    if (nextBabyId === babyId) return;
    if (draft.hasLiveText(babyId)) {
      setPendingSwitch(nextBabyId);
    } else {
      router.push(`/babies/${nextBabyId}`);
    }
  }

  return (
    <div className="mx-auto flex min-h-svh max-w-xl flex-col bg-background">
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-border bg-card px-4 py-3"
        style={{ paddingTop: "max(0.75rem, env(safe-area-inset-top, 0px))" }}
      >
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-semibold text-foreground">{babyLabel}</span>
          <span className="text-xs text-muted-foreground">{role === "OWNER" ? "관리 보호자" : "공동 보호자"}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {otherBabies.length > 0 && (
            <label className="sr-only" htmlFor="baby-switch">
              다른 아기로 전환
            </label>
          )}
          {otherBabies.length > 0 && (
            <select
              id="baby-switch"
              className="h-11 rounded-md border border-border bg-background px-2 text-sm"
              value={babyId}
              onChange={(event) => requestSwitch(event.target.value)}
            >
              <option value={babyId}>{babyLabel}</option>
              {otherBabies.map((other) => (
                <option key={other.babyId} value={other.babyId}>
                  {other.label}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            onClick={onSignOut}
            className="h-11 rounded-md px-3 text-sm font-medium text-muted-foreground hover:bg-muted"
          >
            로그아웃
          </button>
        </div>
      </header>

      {pendingSwitch && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="switch-draft-title"
          className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-6"
        >
          <div className="flex w-full max-w-sm flex-col gap-3 rounded-lg border border-border bg-card p-4">
            <h2 id="switch-draft-title" className="text-sm font-semibold text-foreground">
              저장하지 않은 빠른 기록이 있어요
            </h2>
            <p className="text-sm text-muted-foreground">
              다른 아기로 전환하면 지금 화면의 입력은 사라져요. 어떻게 할까요?
            </p>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setPendingSwitch(null)}
                className="min-h-11 rounded-md border border-border px-4 text-sm text-foreground"
              >
                계속 작성
              </button>
              <button
                type="button"
                onClick={() => {
                  draft.saveLiveAsDraft(babyId);
                  const target = pendingSwitch;
                  setPendingSwitch(null);
                  router.push(`/babies/${target}`);
                }}
                className="min-h-11 rounded-md border border-border px-4 text-sm text-foreground"
              >
                개인 초안 저장 후 전환
              </button>
              <button
                type="button"
                onClick={() => {
                  draft.discardLive(babyId);
                  const target = pendingSwitch;
                  setPendingSwitch(null);
                  router.push(`/babies/${target}`);
                }}
                className="min-h-11 rounded-md border border-destructive/40 px-4 text-sm text-destructive"
              >
                버리고 전환
              </button>
            </div>
          </div>
        </div>
      )}

      <main className="flex-1 px-4 py-4 pb-24">{children}</main>

      <nav
        aria-label="주요 화면 이동"
        className="fixed inset-x-0 bottom-0 z-10 mx-auto flex max-w-xl border-t border-border bg-card"
        style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
      >
        {items.map(({ href, label, icon: Icon }) => {
          const active = href === `/babies/${babyId}` ? pathname === href : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex min-h-11 flex-1 flex-col items-center justify-center gap-0.5 py-2 text-xs",
                active ? "text-primary" : "text-muted-foreground",
              )}
            >
              <Icon aria-hidden size={20} />
              {label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
