import { notFound } from "next/navigation";
import { AudioMeasureScreen } from "./screen";

export default function AudioMeasurePage() {
  if (process.env.NODE_ENV !== "development") notFound();
  return <AudioMeasureScreen />;
}
