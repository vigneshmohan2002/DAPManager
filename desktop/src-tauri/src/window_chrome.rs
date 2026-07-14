use tauri::{PhysicalSize, WebviewWindow};

// Mini-player threshold. Matches the size we set from the JS side
// (`enterMiniPlayer` in `lib/window.ts`); the small buffer absorbs
// rounding when the OS's logical size lands a pixel off after the
// scale-factor round-trip.
const MINI_PLAYER_SIZE: u32 = 220;

#[derive(Debug, PartialEq, Eq)]
enum ChromeAction {
    Shrink,
    Grow,
    None,
}

// Pure decision: should the chrome change, and which way? Split out
// from the side-effect wrapper below so the threshold + scale-factor
// rounding can be unit-tested without a real WebviewWindow.
fn decide_chrome_action(physical_size: (u32, u32), scale: f64, is_decorated: bool) -> ChromeAction {
    let scale = if scale > 0.0 { scale } else { 1.0 };
    let width = (physical_size.0 as f64 / scale).round() as u32;
    let height = (physical_size.1 as f64 / scale).round() as u32;
    let in_mini = width <= MINI_PLAYER_SIZE && height <= MINI_PLAYER_SIZE;
    match (in_mini, is_decorated) {
        (true, true) => ChromeAction::Shrink,
        (false, false) => ChromeAction::Grow,
        _ => ChromeAction::None,
    }
}

// When the main window shrinks to mini-player size, drop OS chrome
// and pin it always-on-top across spaces. Reverse on grow-back.
// Hooked to `WindowEvent::Resized` so a manual resize works too —
// not just the scripted one from `enterMiniPlayer`.
pub(crate) fn handle_mini_player_chrome(window: &WebviewWindow, size: &PhysicalSize<u32>) {
    let scale = window.scale_factor().unwrap_or(1.0);
    let is_decorated = window.is_decorated().unwrap_or(true);
    match decide_chrome_action((size.width, size.height), scale, is_decorated) {
        ChromeAction::Shrink => {
            let _ = window.set_decorations(false);
            let _ = window.set_always_on_top(true);
            let _ = window.set_visible_on_all_workspaces(true);
        }
        ChromeAction::Grow => {
            let _ = window.set_decorations(true);
            let _ = window.set_always_on_top(false);
            let _ = window.set_visible_on_all_workspaces(false);
        }
        ChromeAction::None => {}
    }
}

#[cfg(test)]
mod tests {
    use super::{decide_chrome_action, ChromeAction, MINI_PLAYER_SIZE};

    #[test]
    fn shrinks_at_threshold_when_decorated() {
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE, MINI_PLAYER_SIZE), 1.0, true),
            ChromeAction::Shrink,
        );
    }

    #[test]
    fn no_op_at_threshold_when_already_chromeless() {
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE, MINI_PLAYER_SIZE), 1.0, false),
            ChromeAction::None,
        );
    }

    #[test]
    fn grows_just_above_threshold_when_chromeless() {
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE + 1, MINI_PLAYER_SIZE + 1), 1.0, false,),
            ChromeAction::Grow,
        );
    }

    #[test]
    fn no_op_just_above_threshold_when_decorated() {
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE + 1, MINI_PLAYER_SIZE + 1), 1.0, true,),
            ChromeAction::None,
        );
    }

    #[test]
    fn does_not_shrink_when_only_one_dim_is_small() {
        // User narrowed the width but kept height tall — still a normal window.
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE, 800), 1.0, true),
            ChromeAction::None,
        );
    }

    #[test]
    fn hidpi_2x_threshold_in_physical_pixels() {
        // On a 2x display, 440 physical px == 220 logical px.
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE * 2, MINI_PLAYER_SIZE * 2), 2.0, true,),
            ChromeAction::Shrink,
        );
        // 442 physical / 2.0 = 221 logical → above threshold.
        assert_eq!(
            decide_chrome_action(
                (MINI_PLAYER_SIZE * 2 + 2, MINI_PLAYER_SIZE * 2 + 2),
                2.0,
                false,
            ),
            ChromeAction::Grow,
        );
    }

    #[test]
    fn fractional_scale_rounds_to_nearest_logical() {
        // 1.5x scale: 330 physical → 220 logical exactly.
        assert_eq!(
            decide_chrome_action((330, 330), 1.5, true),
            ChromeAction::Shrink,
        );
        // 332 / 1.5 ≈ 221.33 → rounds to 221 → above threshold.
        assert_eq!(
            decide_chrome_action((332, 332), 1.5, false),
            ChromeAction::Grow,
        );
    }

    #[test]
    fn zero_or_negative_scale_falls_back_to_one() {
        // Pathological scale factor — don't divide by zero, treat as 1x.
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE, MINI_PLAYER_SIZE), 0.0, true),
            ChromeAction::Shrink,
        );
    }
}
