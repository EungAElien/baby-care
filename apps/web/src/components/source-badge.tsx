import { cn } from "@/lib/utils";

type InferenceMode = "REAL" | "STUB";
type DataOrigin = "USER" | "DEMO";

/**
 * Renders the contract's REAL/STUB × USER/DEMO source label (개발계약 11장).
 * Every screen showing analysis-derived content must show this — never
 * present a STUB or DEMO result as if it were a real analysis.
 */
export function SourceBadge({
  inferenceMode,
  dataOrigin,
  className,
}: Readonly<{ inferenceMode?: InferenceMode; dataOrigin: DataOrigin; className?: string }>) {
  const label = sourceLabel(inferenceMode, dataOrigin);
  const isReal = inferenceMode === "REAL" && dataOrigin === "USER";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium",
        isReal ? "border-primary/30 bg-accent text-primary" : "border-border bg-muted text-muted-foreground",
        className,
      )}
      data-inference-mode={inferenceMode}
      data-origin={dataOrigin}
    >
      {label}
    </span>
  );
}

function sourceLabel(inferenceMode: InferenceMode | undefined, dataOrigin: DataOrigin): string {
  if (inferenceMode === "REAL" && dataOrigin === "USER") return "실제 입력 분석";
  if (inferenceMode === "REAL" && dataOrigin === "DEMO") return "예시 음원 실제 분석";
  if (inferenceMode === "STUB") return "개발용 고정 응답";
  return dataOrigin === "DEMO" ? "예시 자료" : "실제 자료";
}
