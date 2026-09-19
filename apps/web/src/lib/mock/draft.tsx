"use client";

// Per-baby personal draft state for SC05, kept only in tab memory (never
// localStorage/IndexedDB — contract §3 forbids persisting unsent input past a
// user/baby-scope change). Baby switching, sign-out, and switching mock
// accounts all discard it, matching "로그아웃·다른 계정 로그인·권한 해제 때는
// 해당 캐시와 미전송 내용을 지우고 자동 저장·재전송하지 않는다."
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useMockSession } from "@/lib/mock/session";

type DraftContextValue = Readonly<{
  getLiveText: (babyId: string) => string;
  setLiveText: (babyId: string, text: string) => void;
  hasLiveText: (babyId: string) => boolean;
  getSavedDraft: (babyId: string) => string | null;
  saveLiveAsDraft: (babyId: string) => void;
  discardLive: (babyId: string) => void;
}>;

const DraftContext = createContext<DraftContextValue | null>(null);

export function DraftProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  const session = useMockSession();
  const [live, setLive] = useState<Readonly<Record<string, string>>>({});
  const [saved, setSaved] = useState<Readonly<Record<string, string>>>({});
  const lastAlias = useRef(session.alias);

  // A different login (or logging out) must not let one account's draft leak into another's.
  useEffect(() => {
    if (lastAlias.current === session.alias) return;
    lastAlias.current = session.alias;
    setLive({});
    setSaved({});
  }, [session.alias]);

  const getLiveText = useCallback((babyId: string) => live[babyId] ?? "", [live]);
  const hasLiveText = useCallback((babyId: string) => (live[babyId]?.trim().length ?? 0) > 0, [live]);
  const getSavedDraft = useCallback((babyId: string) => saved[babyId] ?? null, [saved]);

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

  const value = useMemo<DraftContextValue>(
    () => ({ getLiveText, setLiveText, hasLiveText, getSavedDraft, saveLiveAsDraft, discardLive }),
    [getLiveText, setLiveText, hasLiveText, getSavedDraft, saveLiveAsDraft, discardLive],
  );

  return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>;
}

export function useDraft(): DraftContextValue {
  const value = useContext(DraftContext);
  if (!value) throw new Error("useDraft must be called below DraftProvider.");
  return value;
}
