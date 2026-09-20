"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Home, Clock, BarChart3, Settings, Heart } from "lucide-react";
import { useDraft } from "@/lib/mock/draft";
import { ActionButton } from "@/components/seed-design/ui/action-button";
import { AlertDialogRoot, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter } from "@/components/seed-design/ui/alert-dialog";

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

export function BabyShell({ babyId, babyLabel, role, otherBabies, onSignOut, demo = false, children }: Readonly<{
  babyId: string; babyLabel: string; role: "OWNER" | "CAREGIVER";
  otherBabies: readonly OtherBaby[]; onSignOut: () => void | Promise<void>;
  demo?: boolean; children: React.ReactNode;
}>) {
  const pathname = usePathname();
  const router = useRouter();
  const draft = useDraft();
  const [pendingSwitch, setPendingSwitch] = useState<string | null>(null);
  const [signOutPending, setSignOutPending] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const signOutLock = useRef(false);
  const switchRef = useRef<HTMLSelectElement>(null);

  async function handleSignOut() {
    if (signOutLock.current) return;
    signOutLock.current = true;
    draft.clearBaby(babyId);
    setSignOutPending(true);
    setSignOutError(null);
    try { await onSignOut(); }
    catch { setSignOutError("이 기기의 로그아웃을 확인하지 못했어요. 다시 시도해 주세요."); }
    finally { signOutLock.current = false; setSignOutPending(false); }
  }
  function requestSwitch(nextBabyId: string) {
    if (nextBabyId === babyId) return;
    switchRef.current?.focus();
    if (draft.hasLiveText(babyId) || draft.hasCareEventDirty(babyId)) setPendingSwitch(nextBabyId);
    else { draft.clearBaby(babyId); router.push(`/babies/${nextBabyId}`); }
  }
  function closeSwitch() {
    setPendingSwitch(null);
  }
  function finishSwitch() {
    const target = pendingSwitch;
    if (!target) return;
    draft.clearBaby(babyId);
    closeSwitch();
    router.push(`/babies/${target}`);
  }

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">본문으로 이동</a>
      <header className="app-header">
        <div className="app-identity">
          <span className="brand-symbol"><Heart size={23} aria-hidden="true" /></span>
          <div className="min-w-0"><p className="truncate font-bold">{babyLabel}</p><p className="text-xs text-muted-foreground">{role === "OWNER" ? "관리 보호자" : "공동 보호자"}</p></div>
        </div>
        <div className="flex min-w-0 items-center gap-1">
          {otherBabies.length > 0 && <><label className="sr-only" htmlFor="baby-switch">다른 아기로 전환</label><select ref={switchRef} id="baby-switch" className="max-w-32 rounded-lg border px-2 text-sm" value={babyId} onChange={(event) => requestSwitch(event.target.value)}>
            <option value={babyId}>{babyLabel}</option>{otherBabies.map((other) => <option key={other.babyId} value={other.babyId}>{other.label}</option>)}
          </select></>}
          <ActionButton type="button" variant="ghost" size="small" onClick={handleSignOut} disabled={signOutPending}>{signOutPending ? "로그아웃 중…" : "로그아웃"}</ActionButton>
        </div>
      </header>
      {demo && <aside className="demo-notice"><strong>DEMO · 화면 체험</strong><span>예시 자료예요. 실제 감지·모델 실행 결과가 아니에요.</span></aside>}
      <AlertDialogRoot open={pendingSwitch !== null} onOpenChange={(open) => { if (!open) closeSwitch(); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>저장하지 않은 빠른 기록이 있어요</AlertDialogTitle>
            <AlertDialogDescription>다른 아기로 전환하면 지금 화면의 입력은 사라져요. 어떻게 할까요?</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <ActionButton type="button" onClick={closeSwitch}>계속 작성</ActionButton>
            <p className="text-sm text-muted-foreground">이 화면의 개인 초안은 공유 기록에 저장되지 않았어요.</p>
            <ActionButton type="button" variant="neutralWeak" onClick={finishSwitch}>버리고 전환</ActionButton>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialogRoot>
      <main id="main-content" tabIndex={-1} className="app-main">
        {signOutError && <p role="alert" className="mb-4 text-destructive">{signOutError}</p>}
        {signOutPending ? <p role="status">로그아웃하고 있어요.</p> : children}
      </main>
      <nav aria-label="주요 화면 이동" className="app-nav">
        {navItems(babyId).map(({ href, label, icon: Icon }) => {
          const base = `/babies/${babyId}`;
          const active = href === base
            ? pathname === href || ["detect", "quick-record", "entries", "results", "audio-measure"].some((part) => pathname.startsWith(`${base}/${part}`))
            : pathname.startsWith(href) || (label === "타임라인" && pathname.startsWith(`${base}/care-events`)) || (label === "설정" && pathname.startsWith(`${base}/care-team`));
          return <Link key={href} href={href} aria-current={active ? pathname === href ? "page" : "location" : undefined}><Icon aria-hidden="true" size={22} />{label}</Link>;
        })}
      </nav>
    </div>
  );
}

