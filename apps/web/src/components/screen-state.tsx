import { AlertCircle, LockKeyhole, NotebookPen } from "lucide-react";
import { cn } from "@/lib/utils";
import { Callout } from "@/components/seed-design/ui/callout";
import { ResultSection } from "@/components/seed-design/ui/result-section";

export function LoadingState({ label }: Readonly<{ label: string }>) {
  return (
    <div role="status" className="screen-section items-center text-center">
      <span
        className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-primary"
        aria-hidden="true"
      />
      <p>{label}</p>
    </div>
  );
}
export function EmptyState({
  label,
  action,
}: Readonly<{ label: string; action?: React.ReactNode }>) {
  return (
    <div className="empty-result">
      <ResultSection
        size="medium"
        style={{ padding: 0 }}
        asset={<NotebookPen aria-hidden="true" size={28} className="mb-4" />}
        title={label}
      />
      {action && <div className="flex justify-center">{action}</div>}
    </div>
  );
}
export function ErrorState({
  label,
  retryable,
}: Readonly<{ label: string; retryable?: boolean }>) {
  return (
    <Callout
      role="alert"
      prefixIcon={<AlertCircle />}
      title="처리하지 못했어요"
      description={
        <>
          <span>{label}</span>
          {retryable !== undefined && (
            <p>
              {retryable
                ? "다시 시도할 수 있어요."
                : "지금은 다시 시도할 수 없어요."}
            </p>
          )}
        </>
      }
    />
  );
}
export function PermissionState({ label }: Readonly<{ label: string }>) {
  return (
    <Callout
      role="alert"
      prefixIcon={<LockKeyhole />}
      title="권한이 필요해요"
      description={label}
    />
  );
}
export function ScreenSection({
  title,
  className,
  children,
}: Readonly<{ title: string; className?: string; children: React.ReactNode }>) {
  return (
    <section className={cn("screen-section", className)}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}
