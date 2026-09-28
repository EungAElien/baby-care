import { cn } from "@/lib/utils";

/**
 * Minimal shared presentational states for the SC01~SC10 route shells.
 * Covers the "로딩 / 빈 화면 / 오류" baseline from 기능명세 v2 §2 공통 화면
 * 상태; the fuller per-screen wording pass is separate follow-up work.
 */
export function LoadingState({ label }: Readonly<{ label: string }>) {
  return (
    <div role="status" className="flex flex-col items-center gap-2 rounded-lg border border-border bg-card p-6 text-center">
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-muted-foreground border-t-primary" aria-hidden />
      <p className="text-sm text-muted-foreground">{label}</p>
    </div>
  );
}

export function EmptyState({ label, action }: Readonly<{ label: string; action?: React.ReactNode }>) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border p-6 text-center">
      <p className="text-sm text-muted-foreground">{label}</p>
      {action}
    </div>
  );
}

export function ErrorState({ label, retryable }: Readonly<{ label: string; retryable?: boolean }>) {
  return (
    <div role="alert" className="flex flex-col gap-1 rounded-lg border border-destructive/40 bg-destructive/5 p-4">
      <p className="text-sm font-medium text-destructive">{label}</p>
      {retryable !== undefined && (
        <p className="text-xs text-muted-foreground">{retryable ? "다시 시도할 수 있어요." : "지금은 다시 시도할 수 없어요."}</p>
      )}
    </div>
  );
}

/**
 * 403 권한 없음 — 404(존재 자체를 숨김)와 다른 문구·스타일을 쓴다. 활성
 * 구성원이지만 관리 권한이 없을 때만 쓰고, 비구성원에는 쓰지 않는다.
 */
export function PermissionState({ label }: Readonly<{ label: string }>) {
  return (
    <div role="alert" className="flex flex-col gap-1 rounded-lg border border-amber-300 bg-amber-50 p-4">
      <p className="text-sm font-medium text-amber-800">권한이 필요해요</p>
      <p className="text-sm text-amber-900">{label}</p>
    </div>
  );
}

export function ScreenSection({
  title,
  className,
  children,
}: Readonly<{ title: string; className?: string; children: React.ReactNode }>) {
  return (
    <section className={cn("flex flex-col gap-3 rounded-lg border border-border bg-card p-4", className)}>
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      {children}
    </section>
  );
}
