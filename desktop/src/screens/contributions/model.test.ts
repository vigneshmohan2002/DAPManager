import { describe, expect, it } from "vitest";
import type { Contribution } from "../../lib/api";
import {
  audioQualityLabel,
  contributionCounts,
  contributionStatusMeta,
  parseDeviceContext,
} from "./model";

function contribution(status: string): Contribution {
  return {
    id: 1,
    device_id: "satellite-1",
    mbid: null,
    artist: "Artist",
    title: "Track",
    album: null,
    target_quality: null,
    acquired_quality: null,
    status,
    updated_at: null,
  };
}

describe("contribution view model", () => {
  it("normalizes device context while preserving explicit automatic settings", () => {
    expect(
      parseDeviceContext({
        device_role: "  master ",
        master_url: "  http://master:5001/ ",
        contribute_to_host: false,
      }),
    ).toEqual({
      role: "master",
      masterUrl: "http://master:5001/",
      automatic: false,
    });

    expect(parseDeviceContext({ master_url: "http://master" })).toEqual({
      role: "satellite",
      masterUrl: "http://master",
      automatic: true,
    });
  });

  it("keeps contribution status labels and terminal counts stable", () => {
    expect(contributionStatusMeta("have_better")).toMatchObject({
      label: "Already matched",
      terminal: true,
    });
    expect(contributionStatusMeta("custom_state")).toMatchObject({
      label: "custom_state",
      terminal: false,
    });
    expect(
      contributionCounts([
        contribution("attempting"),
        contribution("satisfied"),
        contribution("ingested"),
      ]),
    ).toEqual({ pending: 1, complete: 2 });
  });

  it("formats the existing quality fields without adding new assumptions", () => {
    expect(audioQualityLabel(null)).toBe("—");
    expect(
      audioQualityLabel({
        ext: "flac",
        lossless: true,
        bits_per_sample: 24,
        sample_rate: 44100,
        bitrate: 1_411_200,
      }),
    ).toBe("FLAC · Lossless · 24-bit · 44.1 kHz · 1411 kbps");
  });
});
