import { notFound } from "next/navigation";
import { isMockNavEnabled } from "@/lib/mock/config";
import { CharacterLab } from "./character-lab";

export default function CharacterMotionPage() {
  if (process.env.NODE_ENV !== "development" && !isMockNavEnabled()) notFound();
  return <CharacterLab />;
}
