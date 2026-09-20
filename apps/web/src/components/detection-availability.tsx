"use client";
import { useQuery } from "@tanstack/react-query";
import { MicOff, Info } from "lucide-react";
import { useApiClient } from "@/lib/api/real-client";
import { requireData } from "@/lib/api/errors";
import { useRealSession } from "@/lib/auth/real-session";
import { ActionButton } from "./seed-design/ui/action-button";
import { Callout } from "./seed-design/ui/callout";
import { ScreenSection, LoadingState } from "./screen-state";

/** Availability only. A validated browser detector and B-10 session API are prerequisites. */
export function DetectionAvailability() {
  const client = useApiClient();
  const real = useRealSession();
  const capabilities = useQuery({
    queryKey: ["capabilities", real.userId],
    enabled: client !== null && real.status === "signed-in",
    queryFn: async () => {
      if (!client) throw new Error("API unavailable");
      return requireData(await client.GET("/capabilities"));
    },
    retry: false,
  });
  return (
    <ScreenSection title="감지 → 자동 분석">
      {capabilities.isLoading && real.status === "signed-in" ? (
        <LoadingState label="감지 지원 여부를 확인하고 있어요" />
      ) : (
        <Callout
          prefixIcon={<MicOff />}
          title={
            capabilities.isError
              ? "감지 지원 여부를 확인하지 못했어요"
              : "자동 감지 준비 중"
          }
          description={
            capabilities.isError
              ? "연결을 확인하고 다시 시도해 주세요. 마이크는 켜지지 않았어요."
              : "현재는 검증된 자동 감지를 제공하지 않아요. 아래에서 직접 녹음하거나 파일을 선택할 수 있어요."
          }
        />
      )}
      {capabilities.isError && (
        <ActionButton
          variant="neutralWeak"
          disabled={capabilities.isFetching}
          onClick={() => void capabilities.refetch()}
        >
          지원 여부 다시 확인
        </ActionButton>
      )}
      <div className="status-note flex items-start gap-3">
        <Info size={20} className="shrink-0 mt-1" aria-hidden="true" />
        <p className="text-sm">
          마이크 권한을 허용하고 이 화면을 열어 둔 동안만 녹음할 수 있어요. 화면
          숨김·잠금·연결 중단 후에는 직접 다시 시작해 주세요.
        </p>
      </div>
    </ScreenSection>
  );
}
