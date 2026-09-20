"use client";

// SC05 빠른 기록. 선택지 CareEvent 저장은 A-04 ①에서 실제 FastAPI에 연결한다.
// A-07 uses its own confirmation transaction; this page never creates a second CareEvent for it.
import { useEffect, useRef } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { CareEventCard } from "@/components/care-event-card";
import { CareEventForm } from "@/components/care-event-form";
import { useRealSession } from "@/lib/auth/real-session";
import { useDraft } from "@/lib/mock/draft";
import { isForBaby, mockCareEvent } from "@/lib/mock/fixtures";
import { ScreenSection } from "@/components/screen-state";

export default function QuickRecordPage() {
  const { babyId } = useParams<{ babyId: string }>();
  const draft = useDraft();
  const real = useRealSession();
  const liveText = draft.getLiveText(babyId);
  const loadedForBaby = useRef<string | null>(null);
  const example = mockCareEvent();

  // A personal draft saved before switching away comes back when this baby is active again.
  useEffect(() => {
    if (loadedForBaby.current === babyId) return;
    loadedForBaby.current = babyId;
    const savedDraft = draft.getSavedDraft(babyId);
    if (savedDraft && !draft.getLiveText(babyId)) draft.setLiveText(babyId, savedDraft);
  }, [babyId, draft]);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-foreground">빠른 기록</h1>
      <CareEventForm key={babyId} babyId={babyId} canSave={real.status === "signed-in"} />
      {real.status !== "signed-in" && isForBaby(babyId, example) && (
        <ScreenSection title="계약의 저장 예시 응답">
          <p className="text-xs text-muted-foreground">고정된 합성 자료이며 위 입력을 실제로 저장한 결과가 아니에요.</p>
          <CareEventCard event={example} babyId={babyId} />
        </ScreenSection>
      )}

      {real.status === "signed-in" ? <ScreenSection title="원문·선택지 확인 기록">
        <p className="text-sm text-muted-foreground">자연어·선택지·혼합 입력을 개인 초안으로 저장하고 내용을 확인해요. 이미 저장한 생활 기록은 다시 입력하지 않아도 돼요.</p>
        <Link href={`/babies/${babyId}/entries`} className="flex min-h-11 items-center justify-center rounded-md border border-border px-4 text-sm">개인 초안 작성·복구</Link>
      </ScreenSection> : <ScreenSection title="자연어로 기록">
        <textarea
          value={liveText}
          onChange={(event) => draft.setLiveText(babyId, event.target.value)}
          placeholder="예: 분유 80mL 먹였어요"
          className="min-h-20 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
        />
        <p className="text-xs text-muted-foreground">자연어 정규화·확인 저장은 A-07에서 연결해요. 지금 입력은 확정 기록이 아닙니다.</p>
      </ScreenSection>}

      {real.status !== "signed-in" && (
        <Link
          href={`/babies/${babyId}/entries/review`}
          className="flex min-h-11 items-center justify-center rounded-md border border-border px-4 text-sm font-medium text-foreground"
        >
          자연어 목 화면 미리보기
        </Link>
      )}
    </div>
  );
}
