"use client";

import { useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePrivateScope } from "./app-providers";
import { useBabiesQuery, findBabyAccess } from "@/lib/api/babies";
import { useApiClient } from "@/lib/api/real-client";
import { requireData } from "@/lib/api/errors";
import { ContractRequestError } from "@/lib/api/client";
import { SummaryOverview } from "./summary-overview";
import { ScreenSection, LoadingState, ErrorState } from "./screen-state";
import { ActionButton } from "./seed-design/ui/action-button";

export function dateInTimezone(timezone: string, now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const part = (type: string) => parts.find((item) => item.type === type)?.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export function RealSummary({ babyId }: Readonly<{ babyId: string }>) {
  const scope = usePrivateScope();
  const snapshot = useSyncExternalStore((listener) => scope.subscribe(listener), () => scope.snapshot(), () => scope.snapshot());
  const client = useApiClient();
  const babies = useBabiesQuery(!!snapshot.userId);
  const baby = findBabyAccess(babies.data, babyId)?.baby;
  const date = baby ? dateInTimezone(baby.timezone) : "";
  const enabled = !!client && !!baby && !!snapshot.userId && snapshot.babyId === babyId;
  const summary = useQuery({
    queryKey: ["private", snapshot.userId, babyId, "summary", date, baby?.timezone],
    enabled,
    queryFn: async ({ signal }) => {
      if (!client || !baby) throw new Error("아기 정보를 확인해 주세요.");
      const result = requireData(await client.GET("/babies/{baby_id}/summary", { signal, params: { path: { baby_id: babyId }, query: { date, timezone: baby.timezone } } }));
      scope.assertCurrent(snapshot);
      if (result.baby_id !== babyId || result.date !== date || result.timezone !== baby.timezone) throw new ContractRequestError("Summary is outside the requested scope.");
      return result;
    }, retry: false,
  });
  const patterns = useQuery({
    queryKey: ["private", snapshot.userId, babyId, "patterns"], enabled,
    queryFn: async ({ signal }) => {
      if (!client) throw new Error("서버 연결을 확인해 주세요.");
      const result = requireData(await client.GET("/babies/{baby_id}/patterns", { signal, params: { path: { baby_id: babyId }, query: { range: 7 } } }));
      scope.assertCurrent(snapshot);
      if (result.baby_id !== babyId) throw new ContractRequestError("Patterns are outside the requested scope.");
      return result;
    }, retry: false,
  });
  if (!snapshot.userId) return <ScreenSection title="로그인 후 확인할 수 있어요"><p>이 아기의 실제 기록을 바탕으로 하루를 살펴봐요.</p></ScreenSection>;
  if (babies.isLoading || summary.isLoading) return <LoadingState label="아기 시간대의 오늘 기록을 모으고 있어요" />;
  if (babies.isError || summary.isError) return <><ErrorState label="오늘 요약을 불러오지 못했어요. 기록이 없는 상태와는 달라요." /><ActionButton variant="neutralWeak" disabled={babies.isFetching || summary.isFetching} onClick={() => { void babies.refetch(); void summary.refetch(); }}>다시 조회</ActionButton></>;
  if (!baby || !summary.data) return <ErrorState label="이 아기의 요약을 확인할 수 없어요." />;
  return <>
    <SummaryOverview babyId={babyId} summary={summary.data} />
    <ScreenSection title="다음 돌봄을 준비해요">
      {patterns.isLoading && <LoadingState label="최근 7일의 기록을 살펴보고 있어요" />}
      {patterns.isError && <><ErrorState label="준비 패턴을 불러오지 못했어요." /><ActionButton variant="neutralWeak" disabled={patterns.isFetching} onClick={() => void patterns.refetch()}>패턴 다시 조회</ActionButton></>}
      {patterns.data?.items.map((pattern) => <div className="status-note" key={pattern.kind}>
        <h3 className="font-bold">{{ FEEDING: "수유", SLEEP_PREPARATION: "수면 준비", DIAPER: "기저귀" }[pattern.kind]}</h3>
        <p>{pattern.status === "ON_HOLD" ? ({ INSUFFICIENT_RECORDS: "기록이 더 필요해요", HIGH_VARIABILITY: "간격의 차이가 커서 판단을 보류해요", UNCONFIRMED_DAYS: "기록한 날의 확인이 필요해요", MANUAL_ONLY: "직접 확인하는 돌봄이에요" }[pattern.reason ?? "INSUFFICIENT_RECORDS"]) : pattern.estimated_due_at ? `${new Date(pattern.estimated_due_at).toLocaleString("ko-KR", { timeZone: baby.timezone })} 무렵을 참고해 주세요` : "준비 시점을 특정할 수 없어요"}</p>
        <p className="text-sm text-muted-foreground">확인된 {pattern.valid_days}일 · {pattern.interval_count}개 간격에 근거해요. 확정된 일정이 아니에요.</p>
      </div>)}
      {patterns.data?.items.length === 0 && <p>살펴볼 준비 패턴이 아직 없어요.</p>}
    </ScreenSection>
  </>;
}
