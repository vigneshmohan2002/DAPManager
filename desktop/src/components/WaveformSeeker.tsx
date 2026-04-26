import { useEffect, useRef } from "react";

type Props = {
  peaks: Float32Array;
  position: number;
  duration: number;
  onSeek: (seconds: number) => void;
};

const UNFILLED = "#3a3a3c";

export default function WaveformSeeker({
  peaks,
  position,
  duration,
  onSeek,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    let raf = 0;
    const draw = () => {
      raf = 0;
      const dpr = window.devicePixelRatio || 1;
      const cssW = container.clientWidth;
      const cssH = container.clientHeight;
      const targetW = Math.max(1, Math.round(cssW * dpr));
      const targetH = Math.max(1, Math.round(cssH * dpr));
      if (canvas.width !== targetW) canvas.width = targetW;
      if (canvas.height !== targetH) canvas.height = targetH;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, cssH);

      const accent =
        getComputedStyle(canvas).getPropertyValue("--color-accent").trim() ||
        "#fff";
      const playedFrac =
        duration > 0 ? Math.min(1, Math.max(0, position / duration)) : 0;
      const playedX = playedFrac * cssW;

      const n = peaks.length;
      if (n === 0) return;
      const barW = cssW / n;
      const gap = barW > 2 ? 1 : 0;
      const drawW = Math.max(1, barW - gap);
      const mid = cssH / 2;

      for (let i = 0; i < n; i++) {
        const x = i * barW;
        const h = Math.max(1, peaks[i] * cssH);
        ctx.fillStyle = x + drawW <= playedX ? accent : UNFILLED;
        ctx.fillRect(x, mid - h / 2, drawW, h);
      }
    };

    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(draw);
    };

    schedule();
    const ro = new ResizeObserver(schedule);
    ro.observe(container);
    return () => {
      ro.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [peaks, position, duration]);

  const seekFromEvent = (clientX: number, rect: DOMRect) => {
    if (duration <= 0) return;
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    onSeek(frac * duration);
  };

  return (
    <div
      ref={containerRef}
      role="slider"
      aria-label="Seek"
      aria-valuemin={0}
      aria-valuemax={duration || 0}
      aria-valuenow={position}
      tabIndex={0}
      className="flex-1 h-7 cursor-pointer select-none"
      onPointerDown={(e) => {
        (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
        seekFromEvent(e.clientX, e.currentTarget.getBoundingClientRect());
      }}
      onPointerMove={(e) => {
        if (e.buttons !== 1) return;
        seekFromEvent(e.clientX, e.currentTarget.getBoundingClientRect());
      }}
    >
      <canvas ref={canvasRef} className="w-full h-full block" />
    </div>
  );
}
