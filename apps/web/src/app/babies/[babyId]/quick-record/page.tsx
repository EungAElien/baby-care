"use client";

// SC05 빠른 기록 — 선택지/자연어/상태/시각의 최소 필드 셸.
// 실제 저장은 A-04·A-07에서 연결하고, 여기서는 SC09 확인 화면으로 가는
// 목 이동 경로와 아기 전환 시 미저장 초안 처리(개발계약 §3)를 보인다.
import { useEffect, useRef } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useDraft } from "@/lib/mock/draft";
import { ScreenSection } from "@/components/screen-state";

export default function QuickRecordPage() {
  const { babyId } = useParams<{ babyId: string }>();
  const draft = useDraft();
  const liveText = draft.getLiveText(babyId);
  const loadedForBaby = useRef<string | null>(null);

  // A personal draft saved before switching away comes back when this baby is active again.
  useEffect(() => {
    if (loadedForBaby.current === babyId) return;
    loadedForBaby.current = babyId;
    const savedDraft = draft.getSavedDraft(babyId);
    if (savedDraft && !draft.getLiveText(babyId)) draft.setLiveText(babyId, savedDraft);
  }, [babyId, draft]);

  return (
    <div className="flex flex-col gap-4">
      <ScreenSection title="선택지로 기록">
        <div className="flex flex-wrap gap-2">
          {["수유", "수면", "기저귀", "달래기"].map((label) => (
            <button
              key={label}
              type="button"
              disabled
              className="min-h-11 rounded-md border border-border px-3 text-sm text-muted-foreground"
            >
              {label}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">시각·양 등 세부 항목은 펼쳐서 입력합니다(이후 작업에서 연결).</p>
      </ScreenSection>

      <ScreenSection title="자연어로 기록">
        <textarea
          value={liveText}
          onChange={(event) => draft.setLiveText(babyId, event.target.value)}
          placeholder="예: 분유 80mL 먹였어요"
          className="min-h-20 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground"
        />
        <p className="text-xs text-muted-foreground">
          확인 저장 전에는 정규화 초안일 뿐 확정 기록이 아닙니다. 다른 아기로 전환하면 계속 작성할지, 개인 초안으로
          저장할지 물어봅니다.
        </p>
      </ScreenSection>

      <Link
        href={`/babies/${babyId}/entries/review`}
        className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
      >
        내용 확인하러 가기
      </Link>
    </div>
  );
}
