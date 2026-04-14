// src/settings_window.rs — wry-based settings window for remap editing
// Last modified: 2026-04-12

use winit::dpi::{LogicalPosition, LogicalSize};
use winit::event_loop::{ActiveEventLoop, EventLoopProxy};
use winit::window::{Window, WindowId};
use wry::{Rect, WebView, WebViewBuilder};

use crate::window_state::{self, SettingsWindowState};
use crate::UserEvent;

const SETTINGS_HTML: &str = include_str!("../assets/settings.html");
const WINDOW_WIDTH: u32 = 1100;
const WINDOW_HEIGHT: u32 = 680;

pub struct SettingsWindow {
    // Field order matters for Drop: the webview must be dropped BEFORE the
    // window, otherwise WebView2 panics trying to clean up against a
    // destroyed parent HWND. Rust drops fields in declaration order.
    pub webview: WebView,
    pub window: Window,
}

impl SettingsWindow {
    /// Create the settings window + embedded webview. The webview's IPC
    /// handler forwards messages to the main event loop via `proxy`.
    pub fn new(
        event_loop: &ActiveEventLoop,
        proxy: EventLoopProxy<UserEvent>,
    ) -> Result<Self, String> {
        // Build the window title-bar icon from the shared embedded ICO
        let window_icon = crate::tray::window_icon_rgba()
            .and_then(|(rgba, w, h)| winit::window::Icon::from_rgba(rgba, w, h).ok());

        let mut attrs = Window::default_attributes()
            .with_title("Razer Joro Settings")
            .with_inner_size(LogicalSize::new(WINDOW_WIDTH, WINDOW_HEIGHT))
            .with_resizable(false)
            .with_window_icon(window_icon)
            .with_visible(true);

        // Restore saved position if we have one
        if let Some(saved) = window_state::load() {
            attrs = attrs.with_position(LogicalPosition::new(saved.x, saved.y));
        }

        let window = event_loop
            .create_window(attrs)
            .map_err(|e| format!("create_window: {e}"))?;

        let ipc_handler = move |req: wry::http::Request<String>| {
            let body = req.body().clone();
            let _ = proxy.send_event(UserEvent::SettingsIpc(body));
        };

        let webview = WebViewBuilder::new()
            .with_html(SETTINGS_HTML)
            .with_ipc_handler(ipc_handler)
            .build_as_child(&window)
            .map_err(|e| format!("webview build: {e}"))?;

        Ok(SettingsWindow { webview, window })
    }

    /// Grab the current window position and persist it to disk.
    /// Called from main.rs on WindowEvent::CloseRequested.
    pub fn save_position(&self) {
        if let Ok(pos) = self.window.outer_position() {
            let logical = pos.to_logical::<i32>(self.window.scale_factor());
            window_state::save(SettingsWindowState { x: logical.x, y: logical.y });
        }
    }

    pub fn id(&self) -> WindowId {
        self.window.id()
    }

    /// Keep the webview sized to the window content area.
    pub fn on_resized(&self, width: u32, height: u32) {
        let _ = self.webview.set_bounds(Rect {
            position: winit::dpi::LogicalPosition::new(0, 0).into(),
            size: LogicalSize::new(width, height).into(),
        });
    }

    /// Focus the existing window (called when user clicks "Settings" again
    /// and we already have a window open).
    pub fn focus(&self) {
        self.window.focus_window();
    }

    /// Evaluate JavaScript in the webview — used to push state from Rust.
    pub fn eval(&self, script: &str) -> Result<(), String> {
        self.webview
            .evaluate_script(script)
            .map_err(|e| format!("eval: {e}"))
    }
}
