/** Background time after which the app locks again. */
export const IDLE_RELOCK_MS = 5 * 60 * 1000;

/**
 * Tracks time in the background. A cold start always requires unlock (no tracker state
 * survives); this only decides whether returning to the foreground needs it too.
 */
export class IdleTracker {
  private backgroundedAt: number | null = null;

  constructor(private readonly relockAfterMs: number = IDLE_RELOCK_MS) {}

  onBackground(now: number): void {
    this.backgroundedAt ??= now;
  }

  /** True when the app must lock now. */
  onForeground(now: number): boolean {
    const since = this.backgroundedAt;
    this.backgroundedAt = null;
    return since !== null && now - since >= this.relockAfterMs;
  }
}
