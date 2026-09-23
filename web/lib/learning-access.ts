export const LEARNING_SURFACES = [
  "chat",
  "reading",
  "mastery",
  "watching",
] as const;

export type LearningSurface = (typeof LEARNING_SURFACES)[number];

export const DEFAULT_LEARNER_SURFACES: readonly LearningSurface[] =
  LEARNING_SURFACES;

export type RoutedLearningSurface = LearningSurface;

const ROUTE_SURFACES: ReadonlyArray<{
  prefix: string;
  surface: RoutedLearningSurface;
}> = [
  { prefix: "/mastery", surface: "mastery" },
  { prefix: "/reading", surface: "reading" },
  { prefix: "/watching", surface: "watching" },
  { prefix: "/chat", surface: "chat" },
];

export function learningSurfaceForPath(
  pathname: string,
): RoutedLearningSurface | null {
  const match = ROUTE_SURFACES.find(
    ({ prefix }) =>
      pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
  return match?.surface ?? null;
}

export function learningSurfaceAllowed(
  _preset: string | null | undefined,
  allowedSurfaces: readonly string[] | null | undefined,
  surface: RoutedLearningSurface | null | undefined,
): boolean {
  if (!surface || !allowedSurfaces) return true;
  return new Set(allowedSurfaces).has(surface);
}
