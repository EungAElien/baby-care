"use client";
import { useEffect, useState } from "react";
import { observationDisplay, stateLabels } from "@/lib/care-entries/content";
import { Eye } from "lucide-react";
import { CharacterImage } from "./brand-scene";
import { useRealSession } from "@/lib/auth/real-session";
import { useTimelineQuery } from "@/lib/api/care-events";
import type { components } from "@/lib/api/generated";
import { isForBaby, mockStateObservation } from "@/lib/mock/fixtures";
import { isMockNavEnabled } from "@/lib/mock/config";
import {
  ScreenSection,
  EmptyState,
  ErrorState,
  LoadingState,
} from "./screen-state";
import { SourceBadge } from "./source-badge";
import { ActionButton } from "./seed-design/ui/action-button";

export const observationLabel: Record<string, string> = {
  CRYING: "울고 있어요",
  FUSSING: "칭얼거려요",
  CALM: "차분해요",
  SLEEPY_APPEARING: "졸려 보여요",
  ASLEEP: "잠들었어요",
  AWAKE: "깨어 있어요",
  CHEERFUL_APPEARING: "기분이 좋아 보여요",
  NEUTRAL: "확인된 상태 없음",
};
type Observation = components["schemas"]["StateObservation"];
function ObservationView({
  observation,
}: Readonly<{ observation: Observation | null }>) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  const display = observation ? observationDisplay(observation, now) : null;
  return observation ? (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {display?.visual === "CALM" && display.minutes !== null && !display.stale ? (
          <CharacterImage scene="calm" compact />
        ) : (
          <Eye size={20} aria-hidden="true" />
        )}
        <p className="text-xl font-bold">
          {display?.stale
            ? "현재 상태를 알 수 없어요"
            : observationLabel[display?.visual ?? "NEUTRAL"]}
        </p>
        <SourceBadge dataOrigin={observation.data_origin} />
      </div>
      <p className="text-sm">
        최근 관찰:{" "}
        {observation.state_codes.map((code) => stateLabels[code]).join(" · ")}
        {display?.stale && " · 오래된 관찰"}
      </p>
      <p className="text-sm text-muted-foreground">
        {observation.observed_at ? (
          <>
            <time dateTime={observation.observed_at}>
              {new Date(observation.observed_at).toLocaleString("ko-KR")}
            </time>{" "}
            관찰
          </>
        ) : (
          "관찰 시각 모름"
        )}
      </p>
      <p className="text-sm text-muted-foreground">
        보호자가 확인한 모습이에요. 현재 상태나 자동 추정한 기분을 뜻하지
        않아요.
      </p>
    </div>
  ) : (
    <EmptyState label="최근 조회 범위에 확인된 상태가 없어요" />
  );
}
function RealObservation({ babyId }: Readonly<{ babyId: string }>) {
  const timeline = useTimelineQuery(babyId, true);
  const item = timeline.data?.pages
    .flatMap((page) => page.items)
    .find(
      (entry) =>
        entry.kind === "STATE_OBSERVATION" &&
        "state_observation_id" in entry.resource,
    );
  const observation =
    item && "state_observation_id" in item.resource ? item.resource : null;
  if (timeline.isLoading)
    return <LoadingState label="확인된 상태를 불러오고 있어요" />;
  if (timeline.isError)
    return (
      <>
        <ErrorState label="확인된 상태를 불러오지 못했어요." />
        <ActionButton
          variant="neutralWeak"
          onClick={() => void timeline.refetch()}
        >
          다시 조회
        </ActionButton>
      </>
    );
  return (
    <>
      <ObservationView observation={observation} />
      {!observation && timeline.hasNextPage && (
        <p className="text-sm text-muted-foreground">
          최근 조회 범위에서 찾지 못했어요. 이전 기록은 타임라인에서 확인해
          주세요.
        </p>
      )}
    </>
  );
}
export function HomeObservation({ babyId }: Readonly<{ babyId: string }>) {
  const real = useRealSession();
  const example =
    isMockNavEnabled() && real.status === "signed-out"
      ? mockStateObservation()
      : null;
  return (
    <ScreenSection title="최근 확인한 모습">
      {real.status === "signed-in" ? (
        <RealObservation babyId={babyId} />
      ) : (
        <ObservationView
          observation={example && isForBaby(babyId, example) ? example : null}
        />
      )}
    </ScreenSection>
  );
}
