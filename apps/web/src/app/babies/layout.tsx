import { DraftProvider } from "@/lib/mock/draft";

// Sits above [babyId] so a saved personal draft survives switching babies
// within the tab, per 개발계약 §3's "개인 초안 저장 후 전환" flow.
export default function BabiesLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <DraftProvider>{children}</DraftProvider>;
}
