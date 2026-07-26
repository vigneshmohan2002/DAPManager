import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Album } from "../lib/api";
import AlbumCard from "./AlbumCard";

const album: Album = {
  id: "album-1",
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
