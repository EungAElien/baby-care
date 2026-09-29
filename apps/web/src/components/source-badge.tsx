import { cn } from "@/lib/utils";
import { Badge } from "@seed-design/react";

type InferenceMode = "REAL" | "STUB";
type DataOrigin = "USER" | "DEMO";

/**
 * Renders the contract's REAL/STUB × USER/DEMO source label (개발계약 11장).
 * Every screen showing analysis-derived content must show this — never
 * present a STUB or DEMO result as if it were a real analysis.
 */
export function SourceBadge({
  inferenceMode,
  inferenceExecuted,
  dataOrigin,
  className,
}: Readonly<{
  inferenceMode?: InferenceMode;
  inferenceExecuted?: boolean;
  dataOrigin: DataOrigin;
  className?: string;
}>) {
  const label = sourceLabel(inferenceMode, dataOrigin, inferenceExecuted);
  return (
    <Badge
      tone="neutral"
      variant="weak"
      size="medium"
      className={cn("source-badge", className)}
      data-inference-mode={inferenceMode}
      data-origin={dataOrigin}
      data-inference-executed={inferenceExecuted}
    >
      {label}
    </Badge>
  );
}

function sourceLabel(
  inferenceMode: InferenceMode | undefined,
  dataOrigin: DataOrigin,
  executed?: boolean,
): string {
  if (inferenceMode === "REAL")
    return `${dataOrigin === "DEMO" ? "예시 음원 · " : ""}${executed === true ? "실제 모델 실행" : executed === false ? "모델 미실행" : "모델 실행 여부 미확인"}`;
  if (inferenceMode === "STUB")
    return `${dataOrigin === "DEMO" ? "예시 자료 · " : ""}개발용 고정 응답`;
  return dataOrigin === "DEMO" ? "예시 자료" : "실제 자료";
}
