import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Artist } from "../lib/api";

const apiMocks = vi.hoisted(() => ({
  fetchArtists: vi.fn(),
}));

vi.mock("../lib/api", () => apiMocks);

import ArtistsScreen from "./ArtistsScreen";

const artists: Artist[] = [
  { name: "Massive Attack", album_count: 3, track_count: 31 },
  { name: "Little Simz", album_count: 5, track_count: 62 },
];

describe("ArtistsScreen", () => {
  beforeEach(() => {
    apiMocks.fetchArtists.mockResolvedValue(artists);
  });

  it("filters case-insensitively and opens the selected artist", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<ArtistsScreen ready onOpen={onOpen} />);

    expect(
      await screen.findByRole("button", { name: "Open artist Massive Attack" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 artists")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "SIMZ");

    expect(screen.getByText("1 of 2 artists")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Open artist Massive Attack" }),
    ).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Open artist Little Simz" }),
    );
    expect(onOpen).toHaveBeenCalledWith(artists[1]);
  });

  it("does not load artists before setup is ready", () => {
    render(<ArtistsScreen ready={false} onOpen={vi.fn()} />);

    expect(apiMocks.fetchArtists).not.toHaveBeenCalled();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});
