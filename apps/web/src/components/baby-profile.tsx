"use client";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Baby as BabyIcon } from "lucide-react";
import { useApiClient } from "@/lib/api/real-client";
import { babiesKey, activeBabyKey, patchBaby } from "@/lib/api/babies";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError, requireData } from "@/lib/api/errors";
import { usePrivateScope } from "@/components/app-providers";
import { isCareEventOutcomeUnknown } from "@/lib/care-events/request-outcome";
import type { components } from "@/lib/api/generated";
import type { PrivateScopeSnapshot } from "@/lib/private-scope";
import { ScreenSection, ErrorState } from "./screen-state";
import { ActionButton } from "./seed-design/ui/action-button";
import { TextField, TextFieldInput } from "./seed-design/ui/text-field";
import { BottomSheetRoot, BottomSheetTrigger, BottomSheetContent, BottomSheetBody, BottomSheetFooter } from "./seed-design/ui/bottom-sheet";
import { List, ListItem } from "./seed-design/ui/list";

type Baby = components["schemas"]["Baby"];
type Patch = components["schemas"]["PatchBaby"];
const feedingLabel = { BREAST: "모유", FORMULA: "분유", MIXED: "혼합", UNSPECIFIED: "모름" };
export function BabyProfile({ baby, isOwner }: Readonly<{ baby: Baby; isOwner: boolean }>) {
  const client = useApiClient();
  const scope = usePrivateScope();
  const subscribe = useCallback((listener: () => void) => scope.subscribe(listener), [scope]);
  const getSnapshot = useCallback(() => scope.snapshot(), [scope]);
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Baby>(baby);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [uncertain, setUncertain] = useState<Patch | null>(null);
  const [latest, setLatest] = useState<Baby | null>(null);
  const lock = useRef(false);
  const active = useRef(true);
  const openedScope = useRef<PrivateScopeSnapshot | null>(null);
  useEffect(() => {
    active.current = true;
    const cleanup = () => { active.current = false; openedScope.current = null; lock.current = false; setBusy(false); setOpen(false); setUncertain(null); setLatest(null); setError(""); setSaved(false); };
    const unregister = scope.registerCleanup(cleanup);
    return () => { unregister(); active.current = false; };
  }, [scope, baby.baby_id, snapshot.generation]);
  function changeOpen(next: boolean) {
    if (busy) return;
    if (next) {
      openedScope.current = scope.snapshot();
      if (!uncertain) { setDraft(baby); setError(""); setLatest(null); }
      setSaved(false);
    }
    setOpen(next);
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (lock.current || !client || !isOwner || !openedScope.current) return;
    if (Array.from(draft.alias.trim()).length < 1 || Array.from(draft.alias.trim()).length > 40) { setError("아기 이름은 1~40자로 입력해 주세요."); return; }
    try { new Intl.DateTimeFormat("ko-KR", { timeZone: draft.timezone }); } catch { setError("올바른 시간대를 입력해 주세요. 예: Asia/Seoul"); return; }
    const captured = openedScope.current;
    const body = uncertain ?? { client_request_id: newClientRequestId(), version: draft.version, alias: draft.alias.trim(), birth_date: draft.birth_date, feeding_mode: draft.feeding_mode, timezone: draft.timezone };
    lock.current = true; setBusy(true); setError("");
    try {
      scope.assertCurrent(captured);
      await patchBaby(client, baby.baby_id, body);
      scope.assertCurrent(captured);
      if (!active.current) return;
      setUncertain(null); setOpen(false); setSaved(true);
      await Promise.all([queryClient.invalidateQueries({ queryKey: babiesKey(captured.userId) }), queryClient.invalidateQueries({ queryKey: activeBabyKey(captured.userId) })]);
    } catch (failure) {
      if (!active.current || scope.snapshot() !== captured) return;
      if (failure instanceof ContractApiError && failure.envelope.code === "VERSION_CONFLICT") {
        void queryClient.invalidateQueries({ queryKey: babiesKey(captured.userId) });
        setError("다른 보호자가 프로필을 변경했어요. 최신 값을 확인한 뒤 다시 수정해 주세요.");
        try {
          const refreshed = requireData(await client.GET("/babies", {})).items.find((item) => item.baby.baby_id === baby.baby_id);
          scope.assertCurrent(captured);
          if (active.current && refreshed) setLatest(refreshed.baby);
        } catch { if (active.current && scope.snapshot() === captured) setError("최신 정보를 불러오지 못했어요. 창을 닫고 다시 열어 확인해 주세요."); }
      } else if (isCareEventOutcomeUnknown(failure)) {
        setUncertain(body); setError("저장 응답을 받지 못했어요. 입력을 유지한 채 같은 요청으로 결과를 확인해 주세요.");
      } else setError(failure instanceof ContractApiError ? failure.envelope.message : "프로필을 저장하지 못했어요. 다시 시도해 주세요.");
    } finally { if (active.current && scope.snapshot() === captured) { lock.current = false; setBusy(false); } }
  }
  return <ScreenSection title="아기 프로필">
    <div className="flex items-center gap-3"><span className="brand-symbol"><BabyIcon aria-hidden="true" /></span><div><p className="text-xl font-bold">{baby.alias}</p><p className="text-sm text-muted-foreground">{isOwner ? "관리 보호자만 변경할 수 있어요" : "공동 보호자는 확인할 수 있어요"}</p></div></div>
    <List><ListItem title="생일" detail={baby.birth_date} /><ListItem title="수유 방식" detail={feedingLabel[baby.feeding_mode]} /><ListItem title="기록 시간대" detail={baby.timezone} /></List>
    {saved && <p role="status">프로필을 저장했어요.</p>}
    {isOwner && <BottomSheetRoot open={open} onOpenChange={changeOpen}>
      <BottomSheetTrigger asChild><ActionButton variant="neutralWeak"><Pencil size={18} aria-hidden="true" />프로필 수정</ActionButton></BottomSheetTrigger>
      <BottomSheetContent title="아기 프로필 수정" description="함께 돌보는 보호자에게도 변경된 정보가 보여요." showCloseButton={!busy}>
        <form onSubmit={save} className="flex min-h-0 flex-col">
          <BottomSheetBody>
            <fieldset disabled={busy || uncertain !== null} className="flex flex-col gap-5 py-2">
              <TextField label="아기 이름" value={draft.alias} onValueChange={({ value }) => setDraft({ ...draft, alias: value })}><TextFieldInput required autoComplete="off" /></TextField>
              <TextField label="생일"><TextFieldInput type="date" required value={draft.birth_date} onChange={(e) => setDraft({ ...draft, birth_date: e.target.value })} /></TextField>
              <label className="flex flex-col gap-2">수유 방식<select className="rounded-lg border px-3" value={draft.feeding_mode} onChange={(e) => setDraft({ ...draft, feeding_mode: e.target.value as Baby["feeding_mode"] })}>{Object.entries(feedingLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              <TextField label="기록 시간대" description="예: Asia/Seoul" value={draft.timezone} onValueChange={({ value }) => setDraft({ ...draft, timezone: value })}><TextFieldInput required /></TextField>
            </fieldset>
            {error && <div className="mt-4"><ErrorState label={error} /></div>}
            {latest && <div className="status-note mt-4"><p>최신 프로필: {latest.alias} · {latest.birth_date} · {feedingLabel[latest.feeding_mode]}</p><ActionButton type="button" variant="neutralWeak" onClick={() => { setDraft(latest); setLatest(null); setError(""); }}>최신 값으로 다시 작성</ActionButton></div>}
          </BottomSheetBody>
          <BottomSheetFooter><ActionButton type="submit" loading={busy} disabled={busy || latest !== null}>{uncertain ? "같은 요청으로 결과 확인" : "변경 저장"}</ActionButton></BottomSheetFooter>
        </form>
      </BottomSheetContent>
    </BottomSheetRoot>}
  </ScreenSection>;
}
