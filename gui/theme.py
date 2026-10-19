"""
Claude Code look for Foundry's GUI: warm neutral grays, Claude's clay-orange accent used only for
primary actions, Segoe UI Variable for text and Cascadia Mono for code.

Two layers:
1. apply_theme() rewrites customtkinter's default widget theme, so every standard widget (buttons,
   entries, textboxes, segmented buttons, scrollbars, progress bars...) picks up the palette.
2. The views were written with hardcoded legacy colors (cold grays, iOS blue, neon green). Instead of
   editing every literal in ~6,600 lines, LEGACY_REMAP translates those literals to the palette at the
   single point where customtkinter resolves a color. New code should use the constants below.
"""
import tkinter.font

import customtkinter as ctk
from customtkinter.windows.widgets.appearance_mode.appearance_mode_base_class import CTkAppearanceModeBaseClass

# ---------------------------------------------------------------- palette: (light, dark)
BG = ("#FAF9F5", "#262624")            # main canvas
SIDEBAR = ("#F3F1EA", "#1F1E1D")       # nav rail, status bar
SURFACE = ("#F0EEE6", "#30302E")       # cards, inputs
SURFACE_HOVER = ("#E8E6DC", "#393836")  # neutral buttons, hovered rows
SELECTED = ("#E3E0D4", "#3F3E3B")      # selected nav item
BORDER = ("#DEDCD1", "#3D3D3A")
CODE_BG = ("#F5F4EE", "#1C1B1A")       # terminal, editors, logs

TEXT = ("#1F1E1D", "#F5F4EF")
TEXT_MUTED = ("#5E5D59", "#A6A39A")
TEXT_FAINT = ("#8A8880", "#76746D")

ACCENT = "#D97757"                     # Claude orange: primary actions and live activity only
ACCENT_HOVER = "#C6613F"
ACCENT_TEXT = "#FFFFFF"
SUCCESS = ("#3F7D4E", "#8BBF8F")
ERROR = ("#B5403A", "#E5736A")
WARNING = ("#A8701B", "#E3A54A")

UI_FAMILY = "Segoe UI Variable Text"
MONO_FAMILY = "Consolas"               # upgraded to Cascadia Mono by init_fonts() when installed

# Claude Code's spinner glyphs, and a playful verb per step (the step line itself says what is happening)
SPINNER_FRAMES = ("·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢")
SPINNER_VERBS = ("Forging", "Smelting", "Hammering", "Tempering", "Casting", "Annealing", "Polishing", "Quenching")


# --------------------------------------------- legacy literal -> palette translation, per mode
_COMMON = {
    # iOS blue and purple (primary buttons, links, the "Claude Architect" label color) -> Claude orange
    "#007AFF": ACCENT, "#1E88E5": ACCENT, "#8E44AD": ACCENT,
    "#0062CC": ACCENT_HOVER, "#732D91": ACCENT_HOVER,
    # customtkinter's blue selection fill (selected list items) -> subtle neutral highlight, as in Claude Code
    "#3B8ED0": SELECTED, "#1F6AA5": SELECTED,
    # burnt-orange secondary buttons -> neutral, so orange stays reserved for primary actions
    "#D35400": SURFACE_HOVER, "#A04000": SELECTED,
    # oranges/ambers -> warning; reds -> error; greens -> success
    "#FF9500": WARNING, "#F39C12": WARNING, "#F5A623": WARNING, "#E67E22": WARNING, "#FF6F00": WARNING,
    "#E74C3C": ERROR, "#C0392B": ERROR,
    "#34C759": SUCCESS, "#10A37F": SUCCESS, "#2E9E5B": SUCCESS, "#257F49": SUCCESS,
}
_DARK_ONLY = {
    "#0D0E12": SIDEBAR, "gray11": SIDEBAR,
    "gray13": BG, "gray14": BG,
    "gray17": SURFACE, "gray18": SURFACE, "gray19": SURFACE,
    "gray24": SURFACE_HOVER, "gray25": SURFACE_HOVER, "gray30": SURFACE_HOVER,
    "gray20": SELECTED, "gray22": SELECTED,
    "#0C0D10": CODE_BG, "#1A1A1A": CODE_BG, "#0D1117": CODE_BG, "#0F1116": CODE_BG, "#121418": CODE_BG, "#12141A": CODE_BG,
    "gray60": TEXT_MUTED, "gray65": TEXT_MUTED, "gray70": TEXT_MUTED, "gray75": TEXT_MUTED,
    "gray80": TEXT, "gray85": TEXT, "gray90": TEXT, "#E6EDF3": TEXT, "#33FF66": TEXT,
}
_LIGHT_ONLY = {
    "gray98": BG, "gray96": BG, "gray95": BG,
    "gray92": SURFACE, "gray90": SURFACE, "gray88": SURFACE,
    "gray85": SURFACE_HOVER, "gray80": SURFACE_HOVER,
    "gray75": SELECTED, "gray70": SELECTED, "gray65": SELECTED,
    # Dark grays the views use as standalone button fills / hovers (fg_color="gray30", hover_color="gray20").
    "gray30": SURFACE_HOVER, "gray25": SURFACE_HOVER, "gray20": SELECTED,
    "gray10": TEXT, "#1A1A1A": TEXT,
    "gray60": TEXT_MUTED,
}


def _resolve_table(index, table):
    return {key.upper() if key.startswith("#") else key: (value[index] if isinstance(value, tuple) else value)
            for key, value in table.items()}


LEGACY_REMAP = {
    "Light": _resolve_table(0, {**_COMMON, **_LIGHT_ONLY}),
    "Dark": _resolve_table(1, {**_COMMON, **_DARK_ONLY}),
}

_original_apply = CTkAppearanceModeBaseClass._apply_appearance_mode


def _themed_apply(self, color):
    resolved = _original_apply(self, color)
    if isinstance(resolved, str):
        key = resolved.upper() if resolved.startswith("#") else resolved
        return LEGACY_REMAP["Dark" if ctk.get_appearance_mode() == "Dark" else "Light"].get(key, resolved)
    return resolved


def _widget_theme():
    t = {
        "CTk": {"fg_color": BG},
        "CTkToplevel": {"fg_color": BG},
        "CTkFrame": {"corner_radius": 10, "border_width": 0, "fg_color": SURFACE, "top_fg_color": SURFACE_HOVER,
                     "border_color": BORDER},
        "CTkButton": {"corner_radius": 8, "border_width": 0, "fg_color": SURFACE_HOVER, "hover_color": SELECTED,
                      "border_color": BORDER, "text_color": TEXT, "text_color_disabled": TEXT_FAINT},
        "CTkLabel": {"corner_radius": 0, "fg_color": "transparent", "text_color": TEXT},
        "CTkEntry": {"corner_radius": 8, "border_width": 1, "fg_color": SURFACE, "border_color": BORDER,
                     "text_color": TEXT, "placeholder_text_color": TEXT_FAINT},
        "CTkCheckBox": {"corner_radius": 5, "border_width": 2, "fg_color": ACCENT, "border_color": TEXT_FAINT,
                        "hover_color": ACCENT_HOVER, "checkmark_color": ACCENT_TEXT, "text_color": TEXT,
                        "text_color_disabled": TEXT_FAINT},
        "CTkSwitch": {"corner_radius": 1000, "border_width": 3, "button_length": 0, "fg_color": SURFACE_HOVER,
                      "progress_color": ACCENT, "button_color": ("#FFFFFF", "#F5F4EF"),
                      "button_hover_color": ("#FFFFFF", "#FFFFFF"), "text_color": TEXT, "text_color_disabled": TEXT_FAINT},
        "CTkRadioButton": {"corner_radius": 1000, "border_width_checked": 6, "border_width_unchecked": 2,
                           "fg_color": ACCENT, "border_color": TEXT_FAINT, "hover_color": ACCENT_HOVER,
                           "text_color": TEXT, "text_color_disabled": TEXT_FAINT},
        "CTkProgressBar": {"corner_radius": 1000, "border_width": 0, "fg_color": SURFACE_HOVER,
                           "progress_color": ACCENT, "border_color": BORDER},
        "CTkSlider": {"corner_radius": 1000, "button_corner_radius": 1000, "border_width": 6, "button_length": 0,
                      "fg_color": SURFACE_HOVER, "progress_color": ACCENT, "button_color": ACCENT,
                      "button_hover_color": ACCENT_HOVER},
        "CTkOptionMenu": {"corner_radius": 8, "fg_color": SURFACE_HOVER, "button_color": SURFACE_HOVER,
                          "button_hover_color": SELECTED, "text_color": TEXT, "text_color_disabled": TEXT_FAINT},
        "CTkComboBox": {"corner_radius": 8, "border_width": 1, "fg_color": SURFACE, "border_color": BORDER,
                        "button_color": SURFACE_HOVER, "button_hover_color": SELECTED, "text_color": TEXT,
                        "text_color_disabled": TEXT_FAINT},
        "CTkScrollbar": {"corner_radius": 1000, "border_spacing": 4, "fg_color": "transparent",
                         "button_color": SURFACE_HOVER, "button_hover_color": SELECTED},
        # Also styles every CTkTabview's tab strip: selection is a subtle highlight, not an orange pill.
        "CTkSegmentedButton": {"corner_radius": 8, "border_width": 2, "fg_color": SURFACE, "selected_color": SELECTED,
                               "selected_hover_color": SELECTED, "unselected_color": SURFACE,
                               "unselected_hover_color": SURFACE_HOVER, "text_color": TEXT,
                               "text_color_disabled": TEXT_FAINT},
        "CTkTextbox": {"corner_radius": 8, "border_width": 1, "fg_color": SURFACE, "border_color": BORDER,
                       "text_color": TEXT, "scrollbar_button_color": SURFACE_HOVER,
                       "scrollbar_button_hover_color": SELECTED},
        "CTkScrollableFrame": {"label_fg_color": SURFACE},
        "DropdownMenu": {"fg_color": SURFACE, "hover_color": SURFACE_HOVER, "text_color": TEXT},
    }
    return {name: {k: (list(v) if isinstance(v, tuple) else v) for k, v in values.items()} for name, values in t.items()}


_frame_init = ctk.CTkFrame.__init__


def _slim_frame_init(self, master, width=None, height=None, **kwargs):
    """A CTkFrame is 200x200 until it has children. Containers filled later (chips, button rows, lists) reserved that
    much empty space, so frames without an explicit size start at 1x1; their children still size them."""
    _frame_init(self, master, width=1 if width is None else width, height=1 if height is None else height, **kwargs)


def apply_theme(appearance_mode="Dark"):
    """Call once before the root window is created."""
    if ctk.CTkFrame.__init__ is not _slim_frame_init:
        ctk.CTkFrame.__init__ = _slim_frame_init
    ctk.set_appearance_mode(appearance_mode)
    ctk.set_default_color_theme("blue")  # base: guarantees every key customtkinter expects exists
    for widget, values in _widget_theme().items():
        ctk.ThemeManager.theme.setdefault(widget, {}).update(values)
    for platform in ("Windows", "macOS", "Linux"):
        ctk.ThemeManager.theme["CTkFont"][platform] = {"family": UI_FAMILY, "size": 13, "weight": "normal"}
    CTkAppearanceModeBaseClass._apply_appearance_mode = _themed_apply


def init_fonts():
    """Call once after the root window exists (font families can only be queried then)."""
    global UI_FAMILY, MONO_FAMILY
    families = set(tkinter.font.families())
    UI_FAMILY = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", "Helvetica") if f in families), UI_FAMILY)
    MONO_FAMILY = next((f for f in ("Cascadia Mono", "Cascadia Code", "SF Mono", "Menlo", "Consolas", "Courier New")
                        if f in families), MONO_FAMILY)
    for platform in ("Windows", "macOS", "Linux"):
        ctk.ThemeManager.theme["CTkFont"][platform]["family"] = UI_FAMILY


def mode_color(color):
    """Resolve a (light, dark) palette tuple for code that needs a single color string."""
    if isinstance(color, (tuple, list)):
        return color[1] if ctk.get_appearance_mode() == "Dark" else color[0]
    return color
