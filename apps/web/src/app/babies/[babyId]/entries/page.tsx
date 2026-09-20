"use client";

import { useParams } from "next/navigation";
import { CareEntryWorkspace } from "@/components/care-entry-workspace";

export default function EntriesPage() {
  const { babyId } = useParams<{ babyId: string }>();
  return <CareEntryWorkspace babyId={babyId} />;
}
