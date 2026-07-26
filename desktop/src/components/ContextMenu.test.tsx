import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import ContextMenu, { type ContextMenuEntry } from "./ContextMenu";

const entries = (onSelect = vi.fn()): ContextMenuEntry[] => [
  { kind: "label", text: "Actions" },
  {
    kind: "item",
    label: "Unavailable",
    disabled: true,
    onSelect,
  },
  { kind: "item", label: "Play next", onSelect },
  { kind: "separator" },
  {
    kind: "list",
    heading: "Playlists",
    items: [
      { key: "one", label: "Morning", onSelect },
      { key: "two", label: "Evening", onSelect },
    ],
  },
  { kind: "item", label: "Delete", onSelect },
];

function MenuHarness({
  menuEntries = entries(),
}: {
  menuEntries?: ContextMenuEntry[];
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open actions
      </button>
      <button type="button">After trigger</button>
      {open ? (
        <ContextMenu
          x={12}
          y={16}
          entries={menuEntries}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}

describe("ContextMenu keyboard behavior", () => {
  it("focuses the first enabled menuitem and gives every action menuitem semantics", async () => {
    const user = userEvent.setup();
    render(<MenuHarness />);

    await user.click(screen.getByRole("button", { name: "Open actions" }));

    const menuItems = screen.getAllByRole("menuitem");
    expect(menuItems).toHaveLength(5);
    expect(
      screen.getByRole("menuitem", { name: "Unavailable" }),
    ).toBeDisabled();
    await waitFor(() =>
      expect(
        screen.getByRole("menuitem", { name: "Play next" }),
      ).toHaveFocus(),
    );
  });

  it("navigates enabled menuitems with arrows, Home, and End", async () => {
    const user = userEvent.setup();
    render(<MenuHarness />);

    await user.click(screen.getByRole("button", { name: "Open actions" }));
    const playNext = screen.getByRole("menuitem", { name: "Play next" });
    const morning = screen.getByRole("menuitem", { name: "Morning" });
    const evening = screen.getByRole("menuitem", { name: "Evening" });
    const deleteItem = screen.getByRole("menuitem", { name: "Delete" });
    await waitFor(() => expect(playNext).toHaveFocus());

    await user.keyboard("{ArrowDown}");
    expect(morning).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(evening).toHaveFocus();
    await user.keyboard("{End}");
    expect(deleteItem).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(playNext).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(deleteItem).toHaveFocus();
    await user.keyboard("{Home}");
    expect(playNext).toHaveFocus();
  });

  it("closes on Escape and restores focus to the trigger", async () => {
    const user = userEvent.setup();
    render(<MenuHarness />);
    const trigger = screen.getByRole("button", { name: "Open actions" });

    await user.click(trigger);
    await waitFor(() =>
      expect(
        screen.getByRole("menuitem", { name: "Play next" }),
      ).toHaveFocus(),
    );
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("closes on Tab without trapping or restoring focus", async () => {
    const user = userEvent.setup();
    render(<MenuHarness />);
    const trigger = screen.getByRole("button", { name: "Open actions" });

    await user.click(trigger);
    await waitFor(() =>
      expect(
        screen.getByRole("menuitem", { name: "Play next" }),
      ).toHaveFocus(),
    );
    await user.tab();

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).not.toHaveFocus();
  });

  it("preserves outside-pointer dismissal", async () => {
    const user = userEvent.setup();
    render(<MenuHarness />);

    await user.click(screen.getByRole("button", { name: "Open actions" }));
    await waitFor(() =>
      expect(
        screen.getByRole("menuitem", { name: "Play next" }),
      ).toHaveFocus(),
    );
    await user.pointer({
      keys: "[MouseLeft]",
      target: screen.getByRole("button", { name: "After trigger" }),
    });

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
