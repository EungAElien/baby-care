"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Home, Clock, BarChart3, Settings } from "lucide-react";
import { cn } from "@/lib/utils";
import { mockBabyLabel } from "@/lib/mock/fixtures";
import { useMockSession } from "@/lib/mock/session";

type NavItem = Readonly<{ href: string; label: string; icon: typeof Home }>;

function navItems(babyId: string): readonly NavItem[] {
  return [
    { href: `/babies/${babyId}`, label: "홈", icon: Home },
    { href: `/babies/${babyId}/timeline`, label: "타임라인", icon: Clock },
    { href: `/babies/${babyId}/summary`, label: "요약", icon: BarChart3 },
    { href: `/babies/${babyId}/settings`, label: "설정", icon: Settings },
  ];
}

/** Mobile-first shell shared by SC02~SC10: sticky baby header + bottom tab bar. */
export function BabyShell({
  babyId,
  role,
  children,
}: Readonly<{ babyId: string; role: "OWNER" | "CAREGIVER"; children: React.ReactNode }>) {
  const pathname = usePathname();
  const router = useRouter();
  const session = useMockSession();
  const items = navItems(babyId);
  const otherBabies = session.activeMemberships.filter((membership) => membership.baby_id !== babyId);

  return (
    <div className="mx-auto flex min-h-svh max-w-xl flex-col bg-background">
      <header
        className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-border bg-card px-4 py-3"
        style={{ paddingTop: "max(0.75rem, env(safe-area-inset-top, 0px))" }}
      >
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-semibold text-foreground">{mockBabyLabel(babyId)}</span>
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
              onChange={(event) => router.push(`/babies/${event.target.value}`)}
            >
              <option value={babyId}>{mockBabyLabel(babyId)}</option>
              {otherBabies.map((membership) => (
                <option key={membership.baby_id} value={membership.baby_id}>
                  {mockBabyLabel(membership.baby_id)}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            onClick={() => {
              session.signOut();
              router.push("/login");
            }}
            className="h-11 rounded-md px-3 text-sm font-medium text-muted-foreground hover:bg-muted"
          >
            로그아웃
          </button>
        </div>
      </header>

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
