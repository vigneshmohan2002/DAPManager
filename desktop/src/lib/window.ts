// Mini-player window helpers.
//
// Resizing to MINI_SIZE triggers the Rust-side `handle_mini_player_chrome`
// (see `desktop/src-tauri/src/lib.rs`) which strips OS chrome and pins
// always-on-top across spaces.

import {
  currentMonitor,
  getCurrentWindow,
  LogicalSize,
  PhysicalPosition,
} from "@tauri-apps/api/window";

const MINI_SIZE = 210;
const MINI_PADDING = 16;
const RESTORE_SIZE = { width: 1200, height: 800 };

export async function enterMiniPlayer(): Promise<void> {
  const win = getCurrentWindow();
  await win.setSize(new LogicalSize(MINI_SIZE, MINI_SIZE));

  const monitor = await currentMonitor();
  if (!monitor) return;
  const inner = await win.innerSize();
  const x =
    monitor.position.x + monitor.size.width - inner.width - MINI_PADDING;
  const y =
    monitor.position.y + monitor.size.height - inner.height - MINI_PADDING;
  await win.setPosition(new PhysicalPosition(x, y));
}

export async function exitMiniPlayer(): Promise<void> {
  const win = getCurrentWindow();
  await win.setSize(new LogicalSize(RESTORE_SIZE.width, RESTORE_SIZE.height));
  await win.center();
}
