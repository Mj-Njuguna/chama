import { useCallback, useEffect, useState } from 'react';

/** Seconds left, ticking down once per second; `start(n)` restarts it. */
export function useCountdown(): { secondsLeft: number; start: (seconds: number) => void } {
  const [deadline, setDeadline] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (deadline === null || now >= deadline) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [deadline, now]);

  const start = useCallback((seconds: number) => {
    const at = Date.now();
    setNow(at);
    setDeadline(at + Math.max(0, seconds) * 1000);
  }, []);

  const secondsLeft = deadline === null ? 0 : Math.max(0, Math.ceil((deadline - now) / 1000));
  return { secondsLeft, start };
}
