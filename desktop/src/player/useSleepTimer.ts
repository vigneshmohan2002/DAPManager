import { useCallback, useEffect, useState } from "react";

type SleepTimerState = {
  sleepTimerExpiresAt: number | null;
  setSleepTimer: (durationMs: number | null) => void;
};

export function useSleepTimer(
  audio: HTMLAudioElement | null,
): SleepTimerState {
  const [sleepTimerExpiresAt, setSleepTimerExpiresAt] = useState<
    number | null
  >(null);

  useEffect(() => {
    if (sleepTimerExpiresAt === null) return;
    const fire = () => {
      audio?.pause();
      setSleepTimerExpiresAt(null);
    };
    const remaining = sleepTimerExpiresAt - Date.now();
    if (remaining <= 0) {
      fire();
      return;
    }
    const timerId = window.setTimeout(fire, remaining);
    return () => window.clearTimeout(timerId);
  }, [audio, sleepTimerExpiresAt]);

  const setSleepTimer = useCallback((durationMs: number | null) => {
    setSleepTimerExpiresAt(
      durationMs === null || durationMs <= 0
        ? null
        : Date.now() + durationMs,
    );
  }, []);

  return { sleepTimerExpiresAt, setSleepTimer };
}
