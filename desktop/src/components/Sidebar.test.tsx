import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Playlist, SmartRuleset } from "../lib/api";
import Sidebar from "./Sidebar";

const apiMocks = vi.hoisted(() => ({
  createPlaylist: vi.fn(),
  deletePlaylist: vi.fn(),
  fetchPlaylists: vi.fn(),
  renamePlaylist: vi.fn(),
  updatePlaylistSmartRules: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({ show: vi.fn() }));

vi.mock("../lib/api", () => apiMocks);
vi.mock("./Toast", () => ({ useToast: () => toastMocks }));

const rules: SmartRuleset = {
  match: "all",
  rules: [{ field: "artist", op: "contains", value: "Artist" }],
};

const playlists: Playlist[] = [
  {
    playlist_id: "road-trip",
    name: "Road trip",
    track_count: 4,
    updated_at: "2026-07-14T12:00:00Z",
    smart_rules: rules,
  },
  {
    playlist_id: "liked_songs",
    name: "Liked Songs",
    track_count: 2,
    updated_at: "2026-07-14T12:00:00Z",
    smart_rules: rules,
  },
];

function renderSidebar(overrides: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  const props: React.ComponentProps<typeof Sidebar> = {
    activeId: "home",
    onSelect: vi.fn(),
    onOpenSearch: vi.fn(),
    ready: true,
    playlistsVersion: 0,
    onPlaylistsChanged: vi.fn(),
    onPlaylistCreated: vi.fn(),
    onPlaylistDeleted: vi.fn(),
    ...overrides,
  };
  return { ...render(<Sidebar {...props} />), props };
}

describe("Sidebar playlist controller", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchPlaylists.mockResolvedValue(playlists);
  });

  it("loads playlist rows, retains navigation IDs, and gates the system menu", async () => {
    const { props } = renderSidebar();
    const likedLabel = await screen.findByText("Liked Songs");
    const roadLabel = screen.getByText("Road trip");

    fireEvent.click(roadLabel.closest("button")!);
    expect(props.onSelect).toHaveBeenCalledWith("playlist:road-trip");

    fireEvent.contextMenu(likedLabel.closest("button")!, {
      clientX: 10,
      clientY: 20,
    });
    expect(screen.getByRole("menuitem", { name: "Edit rules…" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Rename…" })).toBeDisabled();
    expect(
      screen.getByRole("menuitem", { name: "Delete (soft)" }),
    ).toBeDisabled();
  });

  it("keeps management routes available through the compact tools menu", async () => {
    const user = userEvent.setup();
    const { props } = renderSidebar();

    await user.click(
      screen.getByRole("button", { name: "Open DAPManager tools" }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Downloads" }));

    expect(props.onSelect).toHaveBeenCalledWith("downloads");
  });

  it("keeps create payloads and parent notifications unchanged", async () => {
    const onPlaylistCreated = vi.fn();
    apiMocks.fetchPlaylists.mockResolvedValue([]);
    apiMocks.createPlaylist.mockResolvedValue({
      success: true,
      playlist_id: "new-list",
      name: "Road mix",
    });
    renderSidebar({ onPlaylistCreated });
    await screen.findByText("No playlists yet.");

    fireEvent.click(screen.getByTitle("New playlist"));
    fireEvent.change(screen.getByPlaceholderText("My playlist"), {
      target: { value: "Road mix" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(apiMocks.createPlaylist).toHaveBeenCalledWith("Road mix", null),
    );
    expect(onPlaylistCreated).toHaveBeenCalledWith("new-list");
    expect(toastMocks.show).toHaveBeenCalledWith(
      'Created playlist "Road mix".',
    );
  });
});
