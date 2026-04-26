import { useEffect, useState } from "react";
import { binPeaks } from "./waveform";

const DEFAULT_BINS = 200;

// Module-scoped so peaks survive PlayerBar re-mounts (e.g. when the window
// flips into mini-player mode and back).
const cache = new Map<string, Float32Array>();
const inflight = new Map<string, AbortController>();

// Fetches the stream URL, decodes via AudioContext, bins to a normalized
// Float32Array of length `bins`. Returns null while the decode is in flight
// or on failure — the caller is expected to fall back to a plain range
// slider when peaks aren't ready.
export function useWaveformPeaks(
  src: string | null,
  mbid: string | null,
  bins: number = DEFAULT_BINS,
): Float32Array | null {
  const [peaks, setPeaks] = useState<Float32Array | null>(() =>
    mbid ? cache.get(mbid) ?? null : null,
  );

  useEffect(() => {
    if (!src || !mbid) {
      setPeaks(null);
      return;
    }
    const cached = cache.get(mbid);
    if (cached) {
      setPeaks(cached);
      return;
    }
    setPeaks(null);

    inflight.get(mbid)?.abort();
    const ctrl = new AbortController();
    inflight.set(mbid, ctrl);

    let cancelled = false;
    (async () => {
      let audioCtx: AudioContext | null = null;
      try {
        const r = await fetch(src, { signal: ctrl.signal });
        if (!r.ok) throw new Error(`stream ${r.status}`);
        const buf = await r.arrayBuffer();
        if (cancelled) return;

        audioCtx = new AudioContext();
        const decoded = await audioCtx.decodeAudioData(buf);
        if (cancelled) return;

        const channel = decoded.getChannelData(0);
        const result = binPeaks(channel, bins);
        cache.set(mbid, result);
        if (!cancelled) setPeaks(result);
      } catch (err) {
        // Abort is normal on track-skip; everything else (network, decode)
        // means we just don't show a waveform — the range-slider fallback
        // keeps the UI usable.
        if ((err as Error)?.name !== "AbortError") {
          // eslint-disable-next-line no-console
          console.warn("waveform decode failed", err);
        }
      } finally {
        if (audioCtx) await audioCtx.close().catch(() => {});
        if (inflight.get(mbid) === ctrl) inflight.delete(mbid);
      }
    })();

    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [src, mbid, bins]);

  return peaks;
}
