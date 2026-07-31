import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WaveformSeeker from "./WaveformSeeker";

describe("WaveformSeeker", () => {
  let resizeObserverConstructions: number;

  beforeEach(() => {
    resizeObserverConstructions = 0;
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor() {
          resizeObserverConstructions += 1;
        }

        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("supports arrow, Home, and End seeking from the keyboard", () => {
    const onSeek = vi.fn();
    render(
      <WaveformSeeker
        peaks={new Float32Array([0.2, 0.6, 0.4])}
        position={30}
        duration={120}
        onSeek={onSeek}
      />,
    );
    const slider = screen.getByRole("slider", { name: "Seek" });

    fireEvent.keyDown(slider, { key: "ArrowRight" });
    fireEvent.keyDown(slider, { key: "ArrowLeft", shiftKey: true });
    fireEvent.keyDown(slider, { key: "Home" });
    fireEvent.keyDown(slider, { key: "End" });

    expect(onSeek.mock.calls.map(([seconds]) => seconds)).toEqual([
      35, 15, 0, 120,
    ]);
  });

  it("keeps one resize observer while playback position advances", () => {
    const peaks = new Float32Array([0.2, 0.6, 0.4]);
    const { rerender } = render(
      <WaveformSeeker
        peaks={peaks}
        position={30}
        duration={120}
        onSeek={vi.fn()}
      />,
    );

    rerender(
      <WaveformSeeker
        peaks={peaks}
        position={30.5}
        duration={120}
        onSeek={vi.fn()}
      />,
    );

    expect(resizeObserverConstructions).toBe(1);
  });
});
