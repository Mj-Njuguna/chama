import { IDLE_RELOCK_MS, IdleTracker } from '../idle';

describe('IdleTracker', () => {
  it('is 5 minutes', () => {
    expect(IDLE_RELOCK_MS).toBe(300_000);
  });

  it('locks at exactly 5 minutes in the background, not before', () => {
    const tracker = new IdleTracker();
    tracker.onBackground(0);
    expect(tracker.onForeground(IDLE_RELOCK_MS - 1)).toBe(false);
    tracker.onBackground(0);
    expect(tracker.onForeground(IDLE_RELOCK_MS)).toBe(true);
  });

  it('counts from the FIRST background event (inactive then background)', () => {
    const tracker = new IdleTracker();
    tracker.onBackground(0);
    tracker.onBackground(200_000);
    expect(tracker.onForeground(IDLE_RELOCK_MS)).toBe(true);
  });

  it('a foreground without a background does not lock', () => {
    expect(new IdleTracker().onForeground(10 * IDLE_RELOCK_MS)).toBe(false);
  });
});
