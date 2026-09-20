"use client";

// Per-baby personal draft state for SC05, kept only in tab memory (never
// localStorage/IndexedDB — contract §3 forbids persisting unsent input past a
// user/baby-scope change). Baby switching, sign-out, and switching mock
// accounts all discard it, matching "로그아웃·다른 계정 로그인·권한 해제 때는
// 해당 캐시와 미전송 내용을 지우고 자동 저장·재전송하지 않는다."
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { usePrivateScopeOptional } from "@/components/app-providers";
import { useMockSessionOptional } from "@/lib/mock/session";
import { useRealSession } from "@/lib/auth/real-session";

type DraftContextValue = Readonly<{
  getLiveText: (babyId: string) => string;
  setLiveText: (babyId: string, text: string) => void;
  hasLiveText: (babyId: string) => boolean;
  getSavedDraft: (babyId: string) => string | null;
  saveLiveAsDraft: (babyId: string) => void;
  discardLive: (babyId: string) => void;
  hasCareEventDirty: (babyId: string) => boolean;
  setCareEventDirty: (babyId: string, dirty: boolean) => void;
  clearBaby: (babyId: string) => void;
}>;

const DraftContext = createContext<DraftContextValue | null>(null);

export function DraftProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  // Optional: this provider sits above [babyId] for every visitor, real or
  // mock, so it must not assume MockSessionProvider is mounted.
  const mock = useMockSessionOptional();
  const real = useRealSession();
  const identityKey = real.status === "signed-in" && real.userId
    ? `real:${real.userId}`
    : real.status === "signed-out" && mock?.alias
      ? `mock:${mock.alias}`
      : "signed-out";

  // Remounting the store on an identity change discards both live and saved
  // text before the next account can render, including real → mock/logout.
  return <DraftStore key={identityKey}>{children}</DraftStore>;
}

function DraftStore({ children }: Readonly<{ children: React.ReactNode }>) {
  const scope = usePrivateScopeOptional();
  const [live, setLive] = useState<Readonly<Record<string, string>>>({});
  const [saved, setSaved] = useState<Readonly<Record<string, string>>>({});
  const [careEventDirty, setCareEventDirtyState] = useState<ReadonlySet<string>>(new Set());

  const getLiveText = useCallback((babyId: string) => live[babyId] ?? "", [live]);
  const hasLiveText = useCallback((babyId: string) => (live[babyId]?.trim().length ?? 0) > 0, [live]);
  const getSavedDraft = useCallback((babyId: string) => saved[babyId] ?? null, [saved]);
  const hasCareEventDirty = useCallback((babyId: string) => careEventDirty.has(babyId), [careEventDirty]);

  const setCareEventDirty = useCallback((babyId: string, dirty: boolean) => {
    setCareEventDirtyState((previous) => {
      if (previous.has(babyId) === dirty) return previous;
      const next = new Set(previous);
      if (dirty) next.add(babyId);
      else next.delete(babyId);
      return next;
    });
  }, []);

  const setLiveText = useCallback((babyId: string, text: string) => {
    setLive((prev) => ({ ...prev, [babyId]: text }));
  }, []);

  const saveLiveAsDraft = useCallback((babyId: string) => {
    setSaved((prev) => ({ ...prev, [babyId]: live[babyId] ?? "" }));
    setLive((prev) => {
      const next = { ...prev };
      delete next[babyId];
      return next;
    });
  }, [live]);

  const discardLive = useCallback((babyId: string) => {
    setLive((prev) => {
      const next = { ...prev };
      delete next[babyId];
      return next;
    });
  }, []);

  const clearBaby = useCallback((babyId: string) => {
    setLive((prev) => {
      const next = { ...prev };
      delete next[babyId];
      return next;
    });
    setSaved((prev) => {
      const next = { ...prev };
      delete next[babyId];
      return next;
    });
    setCareEventDirty(babyId, false);
  }, [setCareEventDirty]);

  useEffect(() => {
    if (!scope) return;
    let previous = scope.snapshot();
    return scope.subscribe(() => {
      const next = scope.snapshot();
      if (previous.babyId && (previous.babyId !== next.babyId || previous.userId !== next.userId)) clearBaby(previous.babyId);
      previous = next;
    });
  }, [scope, clearBaby]);

  const value = useMemo<DraftContextValue>(
    () => ({
      getLiveText, setLiveText, hasLiveText, getSavedDraft, saveLiveAsDraft, discardLive,
      hasCareEventDirty, setCareEventDirty, clearBaby,
    }),
    [getLiveText, setLiveText, hasLiveText, getSavedDraft, saveLiveAsDraft, discardLive,
      hasCareEventDirty, setCareEventDirty, clearBaby],
  );

  return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>;
}

export function useDraft(): DraftContextValue {
  const value = useContext(DraftContext);
  if (!value) throw new Error("useDraft must be called below DraftProvider.");
  return value;
}
