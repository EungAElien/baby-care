import Image from "next/image";

const characterPosters = {
  holding: "/characters/2.0.0-review/action-holding-source.png",
  calm: "/characters/2.0.0-review/observation-calm.png",
  hungry: "/characters/2.0.0-review/ai-hungry.png",
} as const;

/** Existing v2 artwork; the adjacent text carries the state and its provenance. */
export function CharacterImage({
  scene,
  compact = false,
}: Readonly<{ scene: keyof typeof characterPosters; compact?: boolean }>) {
  return (
    <Image
      src={characterPosters[scene]}
      alt=""
      aria-hidden="true"
      width={400}
      height={400}
      unoptimized
      className={compact ? "character-poster character-poster-small" : "brand-scene character-poster"}
    />
  );
}

/** Brand illustration only: this is not proof that a care action occurred. */
export function BrandScene() {
  return <CharacterImage scene="holding" />;
}
