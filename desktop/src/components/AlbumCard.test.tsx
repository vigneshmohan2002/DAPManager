import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Album } from "../lib/api";

const completionMocks = vi.hoisted(() => ({
  requestAlbumDownload: vi.fn(),
  postAction: vi.fn(),
}));
const toastMocks = vi.hoisted(() => ({ show: vi.fn() }));

vi.mock("../lib/api/downloads", () => ({
  requestAlbumDownload: completionMocks.requestAlbumDownload,
}));
vi.mock("../lib/api/sync", () => ({
  postAction: completionMocks.postAction,
}));
vi.mock("./Toast", () => ({ useToast: () => toastMocks }));

import AlbumCard from "./AlbumCard";

const album: Album = {
  id: "461eac33-7edd-481a-a7d1-089ec6fc01af",
  title: "Test Album",
  artist: "Test Artist",
  track_count: 12,
};

function renderCard(overrides: {
  onClick?: () => void;
  onDoubleClick?: () => void;
} = {}) {
  return render(
    <AlbumCard
      album={album}
      coverUrl="http://localhost/cover.jpg"
      {...overrides}
    />,
  );
}

describe("AlbumCard", () => {
  beforeEach(() => {
    completionMocks.requestAlbumDownload.mockResolvedValue({
      success: true,
      queued: true,
      message: "queued",
      request: {
        id: 7,
        release_mbid: album.id,
        title: album.title,
        artist: album.artist,
        track_count: album.track_count,
        stage: "queued",
        detail: "Waiting for the master download queue",
        completed_tracks: 1,
      },
    });
    completionMocks.postAction.mockResolvedValue({
      success: true,
      message: "Task started.",
    });
  });

  it("exposes a semantic, named album button", () => {
    renderCard();

    expect(
      screen.getByRole("button", { name: "Open Test Album by Test Artist" }),
    ).toHaveAttribute("type", "button");
  });

  it("presents the canonical primary artist instead of an arbitrary legacy credit", () => {
    render(
      <AlbumCard
        album={{
          ...album,
          artist: "2Pac featuring Big Syke",
          primary_artist: "2Pac",
          credited_artists: ["2Pac", "2Pac featuring Big Syke"],
        }}
        coverUrl="http://localhost/cover.jpg"
      />,
    );

    expect(
      screen.getByRole("button", { name: "Open Test Album by 2Pac" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2Pac")).toBeInTheDocument();
    expect(screen.queryByText("2Pac featuring Big Syke")).not.toBeInTheDocument();
  });

  it("falls back to the legacy artist when the primary credit is null", () => {
    render(
      <AlbumCard
        album={{ ...album, primary_artist: null }}
        coverUrl="http://localhost/cover.jpg"
      />,
    );

    expect(
      screen.getByRole("button", { name: "Open Test Album by Test Artist" }),
    ).toBeInTheDocument();
  });

  it("defers a single click until the double-click window closes", () => {
    vi.useFakeTimers();
    const onClick = vi.fn();
    renderCard({ onClick, onDoubleClick: vi.fn() });
    const card = screen.getByRole("button");

    fireEvent.click(card, { detail: 1 });
    expect(onClick).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(399));
    expect(onClick).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(1));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("plays on double-click without also opening the album", () => {
    vi.useFakeTimers();
    const onClick = vi.fn();
    const onDoubleClick = vi.fn();
    renderCard({ onClick, onDoubleClick });
    const card = screen.getByRole("button");

    // Match the browser event sequence: click, click, then dblclick.
    fireEvent.click(card, { detail: 1 });
    fireEvent.click(card, { detail: 2 });
    fireEvent.doubleClick(card, { detail: 2 });
    act(() => vi.runAllTimers());

    expect(onDoubleClick).toHaveBeenCalledOnce();
    expect(onClick).not.toHaveBeenCalled();
  });

  it.each([
    ["Enter", "{Enter}"],
    ["Space", " "],
  ])("opens with the %s key", async (_label, key) => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    renderCard({ onClick });
    const card = screen.getByRole("button");
    card.focus();

    await user.keyboard(key);

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("activates immediately when no double-click action exists", () => {
    const onClick = vi.fn();
    renderCard({ onClick });

    fireEvent.click(screen.getByRole("button"));

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("offers exact album completion from the card context menu", async () => {
    const user = userEvent.setup();
    renderCard();

    fireEvent.contextMenu(screen.getByRole("button"), {
      clientX: 24,
      clientY: 36,
    });
    await user.click(
      screen.getByRole("menuitem", { name: "Complete Album" }),
    );

    await waitFor(() =>
      expect(completionMocks.requestAlbumDownload).toHaveBeenCalledWith(
        album.id,
      ),
    );
    expect(completionMocks.postAction).toHaveBeenCalledWith("/api/download");
    expect(toastMocks.show).toHaveBeenCalledWith(
      "Completing Test Album — verified FLAC download started.",
      "ok",
    );
  });

  it("fails closed for an album without an exact release ID", async () => {
    const user = userEvent.setup();
    render(
      <AlbumCard
        album={{ ...album, id: "Test Album|Test Artist" }}
        coverUrl="http://localhost/cover.jpg"
      />,
    );

    fireEvent.contextMenu(screen.getByRole("button"));
    await user.click(
      screen.getByRole("menuitem", { name: "Complete Album" }),
    );

    expect(completionMocks.requestAlbumDownload).not.toHaveBeenCalled();
    expect(toastMocks.show).toHaveBeenCalledWith(
      "This album needs an exact MusicBrainz release ID before it can be completed safely.",
      "err",
    );
  });

  it("cancels a pending click when the card unmounts", () => {
    vi.useFakeTimers();
    const onClick = vi.fn();
    const view = renderCard({ onClick, onDoubleClick: vi.fn() });

    fireEvent.click(screen.getByRole("button"));
    view.unmount();
    act(() => vi.runAllTimers());

    expect(onClick).not.toHaveBeenCalled();
  });
});
