"use client";

import { useWatch } from "react-hook-form";
import type { UseFormReturn } from "react-hook-form";
import type { CareEventFormValues } from "@/lib/care-events/form";

export function CareEventFields({ form, disabled = false }: Readonly<{
  form: UseFormReturn<CareEventFormValues>;
  disabled?: boolean;
}>) {
  const { register, formState } = form;
  const type = useWatch({ control: form.control, name: "type" });
  const timeUnknown = useWatch({ control: form.control, name: "timeUnknown" });

  return (
    <fieldset disabled={disabled} className="flex flex-col gap-4 disabled:opacity-60">
      <label className="flex flex-col gap-1 text-sm text-foreground">
        기록 종류
        <select {...register("type")} className="min-h-11 rounded-md border border-border bg-background px-3">
          <option value="FEEDING">수유</option>
          <option value="SLEEP">수면</option>
          <option value="DIAPER">기저귀</option>
          <option value="SOOTHE">달래기·기타 돌봄</option>
        </select>
      </label>

      {type !== "SLEEP" && (
        <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
          <input type="checkbox" {...register("timeUnknown")} />
          실제 시각을 몰라요
        </label>
      )}
      {(type === "SLEEP" || !timeUnknown) && (
        <label className="flex flex-col gap-1 text-sm text-foreground">
          {type === "SLEEP" ? "수면 시작 시각" : "실제 발생 시각"}
          <input
            type="datetime-local"
            step="any"
            {...register("occurredAt")}
            aria-invalid={Boolean(formState.errors.occurredAt)}
            className="min-h-11 rounded-md border border-border bg-background px-3"
          />
          {formState.errors.occurredAt && <span role="alert" className="text-xs text-destructive">{formState.errors.occurredAt.message}</span>}
        </label>
      )}

      {type === "FEEDING" && (
        <>
          <label className="flex flex-col gap-1 text-sm text-foreground">
            수유 방식
            <select {...register("feedingMode")} className="min-h-11 rounded-md border border-border bg-background px-3">
              <option value="UNSPECIFIED">모름</option>
              <option value="BREAST">모유</option>
              <option value="FORMULA">분유</option>
              <option value="MIXED">혼합</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm text-foreground">
            양 (mL, 모르면 비워두기)
            <input type="number" min="0" step="any" {...register("amountMl")} aria-invalid={Boolean(formState.errors.amountMl)} className="min-h-11 rounded-md border border-border bg-background px-3" />
            {formState.errors.amountMl && <span role="alert" className="text-xs text-destructive">{formState.errors.amountMl.message}</span>}
          </label>
          <label className="flex flex-col gap-1 text-sm text-foreground">
            걸린 시간 (분, 모르면 비워두기)
            <input type="number" min="0" step="any" {...register("durationMinutes")} aria-invalid={Boolean(formState.errors.durationMinutes)} className="min-h-11 rounded-md border border-border bg-background px-3" />
            {formState.errors.durationMinutes && <span role="alert" className="text-xs text-destructive">{formState.errors.durationMinutes.message}</span>}
          </label>
        </>
      )}

      {type === "SLEEP" && (
        <label className="flex flex-col gap-1 text-sm text-foreground">
          수면 종료 시각 (진행 중이면 비워두기)
          <input type="datetime-local" step="any" {...register("endedAt")} aria-invalid={Boolean(formState.errors.endedAt)} className="min-h-11 rounded-md border border-border bg-background px-3" />
          {formState.errors.endedAt && <span role="alert" className="text-xs text-destructive">{formState.errors.endedAt.message}</span>}
        </label>
      )}

      {type === "DIAPER" && (
        <>
          <label className="flex flex-col gap-1 text-sm text-foreground">
            한 일
            <select {...register("diaperOperation")} className="min-h-11 rounded-md border border-border bg-background px-3">
              <option value="CHECK">확인</option>
              <option value="CHANGE">교체</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm text-foreground">
            확인한 상태
            <select {...register("diaperCondition")} className="min-h-11 rounded-md border border-border bg-background px-3">
              <option value="UNKNOWN">모름</option>
              <option value="WET">소변</option>
              <option value="STOOL">대변</option>
              <option value="BOTH">소변·대변</option>
              <option value="CLEAN">깨끗함</option>
            </select>
          </label>
        </>
      )}

      {type === "SOOTHE" && (
        <label className="flex flex-col gap-1 text-sm text-foreground">
          한 일
          <select {...register("sootheAction")} className="min-h-11 rounded-md border border-border bg-background px-3">
            <option value="OTHER">기타 돌봄</option>
            <option value="HOLDING">안기</option>
            <option value="BURPING">트림 돕기</option>
            <option value="SLEEP_PREPARATION">재우기</option>
            <option value="ENVIRONMENT_ADJUSTMENT">환경 조절</option>
          </select>
        </label>
      )}
    </fieldset>
  );
}
