# =============================================================================
# Age of Mythology Retold — Kivy GUI overlay (kvui)
# =============================================================================
#
# Augments Archipelago's standard `GameManager` window (kvui) with a 5-row
# status panel that pins to the top-right of the client view:
#   row 1: Atlantis Key / Final-section status
#   row 2: gem balance
#   row 3: shops open count
#   row 4: traps queued
#   row 5: next-trap name
#
# All updates flow through `update_atlantis_status`, `update_shop_status`,
# `update_trap_status`.  Callers (ApClient.py + GameClient.py) marshal onto
# the Kivy main thread via `Clock.schedule_once`.
#
# This file is purely cosmetic — disabling it does not break gameplay; the
# CLI client mode would still function.
# =============================================================================

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kvui import GameManager, LogtoUI


# RGB triples (0-1) per campaign for the Scenarios tab tiles.
# Matches the colors specified in the option spec.
_CAMPAIGN_TILE_COLORS: dict[str, tuple[float, float, float]] = {
    "FOTT_GREEK":    (0.3,   0.3,    1.0),
    "FOTT_EGYPTIAN": (1.0,   1.0,    0.0),
    "FOTT_NORSE":    (0.53,  0.31,   0.31),
    "FOTT_FINAL":    (1.0,   0.0,    0.0),
    "GOLDEN_GIFT":   (0.855, 0.65,   0.1255),
    "NEW_ATLANTIS":  (0.0,   1.0,    1.0),
}

# Max diagonal slash lines drawn per locked tile — sized for the widest possible span.
_SLASH_COUNT = 60

if TYPE_CHECKING:
    from .ApClient import AoMContext


class AoMManager(GameManager):
    """Custom Archipelago GameManager subclass — adds the AoMR status panel
    to the standard kvui chrome.  Instantiated by `start_ap_ui` (called from
    ApClient.py once the AoMContext is ready)."""
    base_title = "Archipelago Age of Mythology: Retold Client"
    icon = str(Path(__file__).parent.parent / "aom_icon.ico")

    def build(self):
        """Kivy entry — construct the widget tree.  Wraps the base kvui
        layout in a FloatLayout so we can pin a status `BoxLayout` to the
        top-right above the chat tabs.  Returns the root widget."""
        main_content = super().build()

        root = FloatLayout()
        root.add_widget(main_content)

        # Single container — 5 rows stacked vertically, same as original.
        # Two canvas rectangles are drawn: a wide one behind row 1 (Atlantis Key)
        # and a narrower one behind rows 2-5 (Gems / Shops / Traps).
        container = BoxLayout(
            orientation="vertical",
            size_hint=(None, None),
            size=(dp(260), dp(100)),
            padding=(dp(6), 0),
        )

        with container.canvas.before:
            Color(0, 0, 0, 1)
            # Wide rectangle behind the Atlantis Key row (full width)
            self._bg_top = Rectangle()
            # Narrower rectangle behind the remaining rows
            self._bg_bot = Rectangle()

        def _update_rects(*args):
            row_h  = dp(20)
            top_w  = container.width * 0.9     # 10% narrower than container
            bot_w  = dp(142)                   # bottom rect width
            cx, cy = container.x, container.y
            ch     = container.height
            # Top rect: rightmost top_w pixels, top row only
            self._bg_top.pos  = (cx + container.width - top_w, cy + ch - row_h)
            self._bg_top.size = (top_w, row_h)
            # Bot rect: rightmost bot_w pixels, rows 2-5
            self._bg_bot.pos  = (cx + container.width - bot_w, cy)
            self._bg_bot.size = (bot_w, ch - row_h)

        container.bind(pos=_update_rects, size=_update_rects)

        def _make_label():
            lbl = Label(
                text="", markup=True, halign="right", valign="middle",
                font_size=dp(12), size_hint_y=None, height=dp(20),
            )
            lbl.bind(size=lbl.setter("text_size"))
            return lbl

        self._atlantis_label    = _make_label()
        self._gems_label        = _make_label()
        self._shops_label       = _make_label()
        self._trap_count_label  = _make_label()
        self._trap_next_label   = _make_label()

        container.add_widget(self._atlantis_label)
        container.add_widget(self._gems_label)
        container.add_widget(self._shops_label)
        container.add_widget(self._trap_count_label)
        container.add_widget(self._trap_next_label)
        root.add_widget(container)

        def _reposition(*args):
            container.right = root.right
            container.top   = self.tabs.y
        self.tabs.bind(pos=_reposition, size=_reposition)
        root.bind(size=_reposition)
        Clock.schedule_once(lambda dt: _reposition())

        return root

    def build_scenarios_tab(self) -> None:
        """Lazy-build 'Scenarios' tab. kvui modern API: `add_client_tab(title, content)`.
        Called from on_start() so the tab always appears regardless of option settings."""
        if getattr(self, "_scenarios_tab", None) is not None:
            return
        scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False, do_scroll_y=True)
        # Top padding pushes the first row of tiles below the status panel (dp(100) tall).
        outer = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8), padding=(dp(6), dp(100), dp(6), dp(6)))
        outer.bind(minimum_height=outer.setter("height"))
        scroll.add_widget(outer)
        try:
            self.add_client_tab("Scenarios", scroll)
        except Exception as ex:
            logging.getLogger(__name__).warning(f"Could not add Scenarios tab: {ex}")
            return
        self._scenarios_tab = scroll
        self._scenarios_outer = outer
        self._scenario_tile_widgets: dict = {}

    def update_scenarios_view(
        self,
        unlock_sets_of_scenarios: int,
        scenario_to_key_id: dict,
        bundle_display_names: dict,
        held_keys: set,
        campaign_unlocked_by_id: dict,
        disabled_campaign_ids: set,
        scenario_to_god: dict = None,
        scenario_check_counts: dict = None,
    ) -> None:
        """Refresh the Scenarios tab.  Safe to call repeatedly; on first call,
        builds the grid; on subsequent calls, just recolors tiles.

        Args:
            unlock_sets_of_scenarios: option value (0 means option is off; tab
                still renders but tile state collapses to campaign-only).
            scenario_to_key_id:       scenario global_number → AP item id.
            bundle_display_names:     AP item id → friendly bundle name.
            held_keys:                set of AP item ids the player has.
            campaign_unlocked_by_id:  campaign.id → bool.
            disabled_campaign_ids:    campaign.id ints to omit entirely.
            scenario_to_god:          scenario global_number → major god name (optional).
            scenario_check_counts:    scenario global_number → (found, total) tuple (optional).
        """
        if scenario_to_god is None:
            scenario_to_god = {}
        if scenario_check_counts is None:
            scenario_check_counts = {}

        def _update(dt):
            from ..locations.Scenarios import aomScenarioData
            from ..locations.Campaigns  import aomCampaignData

            self.build_scenarios_tab()

            if not getattr(self, "_scenario_tile_widgets", None):
                # First-time grid build.
                self._scenarios_outer.clear_widgets()
                self._scenario_tile_widgets = {}
                # Group by campaign in enum order.
                by_campaign: dict = {}
                for s in aomScenarioData:
                    by_campaign.setdefault(s.campaign, []).append(s)

                for campaign, scenarios in by_campaign.items():
                    if campaign.id in disabled_campaign_ids:
                        continue
                    section = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
                    section.bind(minimum_height=section.setter("height"))

                    header = Label(
                        text=f"[b]{campaign.campaign_name}[/b]",
                        markup=True, halign="left", valign="middle",
                        size_hint_y=None, height=dp(36), font_size=dp(26),
                    )
                    header.bind(size=header.setter("text_size"))
                    section.add_widget(header)

                    # 5 columns; GridLayout auto-sizes tile widths.
                    # At ~800 px window the natural width per tile is ~160 px, giving
                    # approximately a 2:1 width:height ratio with the dp(80) height below.
                    grid = GridLayout(cols=5, size_hint_y=None, spacing=dp(4))
                    grid.bind(minimum_height=grid.setter("height"))

                    for s in scenarios:
                        tile = Label(
                            text=s.display_name, markup=True,
                            halign="center", valign="middle",
                            size_hint_y=None, height=dp(80), font_size=dp(22),
                        )
                        tile.bind(size=tile.setter("text_size"))

                        with tile.canvas.before:
                            bg_color   = Color(0.2, 0.2, 0.2, 1.0)
                            tile_rect  = Rectangle(pos=tile.pos, size=tile.size)
                            # Stencil-clip slashes to this tile's exact bounds so they
                            # never bleed into adjacent tiles.
                            from kivy.graphics import StencilPush, StencilUse, StencilUnUse, StencilPop
                            StencilPush()
                            clip_rect  = Rectangle(pos=tile.pos, size=tile.size)
                            StencilUse()
                            slash_col  = Color(0.5, 0.5, 0.5, 0.0)
                            slash_lines = [
                                Line(points=[0, 0, 0, 0], width=dp(3))
                                for _ in range(_SLASH_COUNT)
                            ]
                            StencilUnUse()
                            clip_rect2 = Rectangle(pos=tile.pos, size=tile.size)
                            StencilPop()

                        def _bind_tile(label=tile, rect=tile_rect, crect=clip_rect, crect2=clip_rect2, slines=slash_lines):
                            def _sync(*_a):
                                lx, ly = label.x, label.y
                                lw, lh = label.width, label.height
                                rect.pos   = (lx, ly)
                                rect.size  = (lw, lh)
                                crect.pos  = (lx, ly)
                                crect.size = (lw, lh)
                                crect2.pos  = (lx, ly)
                                crect2.size = (lw, lh)
                                # Recompute 45° diagonal slash positions.
                                # Start far enough left that a full-height diagonal
                                # beginning off-screen still enters from the left edge,
                                # covering the full width of even a very wide tile.
                                spacing = dp(22)
                                i = 0
                                x_start = -lh
                                while x_start < lw and i < len(slines):
                                    slines[i].points = [
                                        lx + x_start,      ly,
                                        lx + x_start + lh, ly + lh,
                                    ]
                                    i += 1
                                    x_start += spacing
                                # Extra passes for wide tiles: keep going past lw so the
                                # stencil clip catches every remaining slash.
                                while i < len(slines):
                                    slines[i].points = [0, 0, 0, 0]
                                    i += 1
                            label.bind(pos=_sync, size=_sync)
                        _bind_tile()

                        # Store bg_color, slash_col, and campaign name for the recolor loop.
                        self._scenario_tile_widgets[s.global_number] = (
                            tile, bg_color, slash_col, s.campaign.name
                        )
                        grid.add_widget(tile)

                    section.add_widget(grid)
                    self._scenarios_outer.add_widget(section)

            # Recolor every tile based on current state.
            for sid, (tile, color, slash_col, camp_name) in self._scenario_tile_widgets.items():
                base = _CAMPAIGN_TILE_COLORS.get(camp_name, (0.5, 0.5, 0.5))
                kid = scenario_to_key_id.get(sid)
                key_held = (kid is not None and kid in held_keys)
                # Without the scenario-key option, every scenario is treated as key-held.
                if unlock_sets_of_scenarios <= 0:
                    key_held = True
                from ..locations.Scenarios import aomScenarioData as _SD
                scen_obj = next((s for s in _SD if s.global_number == sid), None)
                campaign_open = bool(campaign_unlocked_by_id.get(scen_obj.campaign.id, False)) if scen_obj else False

                # Four states: key × campaign
                if key_held and campaign_open:
                    r, g, b = base; a = 1.0
                elif key_held:
                    r, g, b = base; a = 0.55
                elif campaign_open:
                    r = base[0] * 0.35; g = base[1] * 0.35; b = base[2] * 0.35; a = 1.0
                else:
                    r = g = b = 0.18; a = 1.0
                color.rgba = (r, g, b, a)

                # Slashes visible on locked tiles only.
                fully_unlocked = key_held and campaign_open
                slash_col.a = 0.0 if fully_unlocked else 0.4

                # Build tile text: scenario name (truncated to first line), god, check count.
                name = scen_obj.display_name if scen_obj else str(sid)
                # Keep adding words until the running total exceeds 14 chars so the name
                # fits on a single line without wrapping and pushing god/checks off the tile.
                words = name.split()
                kept, total_chars = [], 0
                for w in words:
                    candidate = total_chars + len(w) + (1 if kept else 0)
                    if kept and candidate > 14:
                        break
                    kept.append(w)
                    total_chars = candidate
                name_line = " ".join(kept)
                # Text color: black on fully-unlocked NEW_ATLANTIS tiles (teal background
                # makes white text hard to read); white everywhere else.
                if fully_unlocked and camp_name == "NEW_ATLANTIS":
                    name_line_markup = f"[b][color=000000]{name_line}[/color][/b]"
                    god_markup = lambda g: f"[i][color=000000]{g}[/color][/i]"
                    checks_markup = lambda s: f"[color=000000]{s}[/color]"
                else:
                    name_line_markup = f"[b]{name_line}[/b]"
                    god_markup = lambda g: f"[i]{g}[/i]"
                    checks_markup = lambda s: s

                lines = [name_line_markup]

                god_name = scenario_to_god.get(sid)
                if god_name:
                    lines.append(god_markup(god_name))

                check_info = scenario_check_counts.get(sid)
                if check_info:
                    found, total = check_info
                    lines.append(checks_markup(f"{found}/{total} checks"))

                tile.text = "\n".join(lines)

        Clock.schedule_once(_update)

    def update_atlantis_status(self, text: str, green: bool = False) -> None:
        """Update the row-1 status label.

        Args:
            text:  The status string (or "" to hide).
            green: If True, render in bright green (unlocked / open state).
                    Otherwise gold (in-progress / neutral).

        Marshals onto the Kivy main thread via Clock.schedule_once so async
        AP code can call this safely.

        Caller: `_update_atlantis_ui` in ApClient.py.
        """
        def _update(dt):
            if not text:
                self._atlantis_label.text = ""
                return
            color = "44FF44" if green else "FFD700"
            self._atlantis_label.text = f"[b][color={color}]{text}[/b]"
        Clock.schedule_once(_update)

    def update_shop_status(self, gems, shops_open) -> None:
        """Update gems / shops-open rows.

        Args:
            gems:       Current gem balance, or None to hide both rows
                        (used when gem_shop is disabled).
            shops_open: Number of shop tiers currently unlocked (1-4).
        """
        def _update(dt):
            if gems is None:
                self._gems_label.text  = ""
                self._shops_label.text = ""
            else:
                self._gems_label.text  = f"[b][color=44FF44]Gems: {gems}[/color][/b]"
                self._shops_label.text = f"[b][color=AAAAFF]Shops open: {shops_open}[/color][/b]"
        Clock.schedule_once(_update)

    def update_trap_status(self, queue_size: int, next_trap_name: str) -> None:
        """Update trap-queue display.  Hides both rows when queue is empty.

        Args:
            queue_size:    Number of pending traps in `ctx.trap_queue`.
            next_trap_name: Display name of the next trap to fire.

        Caller: `_update_atlantis_ui` plus the trap-queue update path in
        `_resolve_shop_signal` / `read_new_checks`.
        """
        def _update(dt):
            if queue_size <= 0:
                self._trap_count_label.text = ""
                self._trap_next_label.text  = ""
            else:
                c = "C9695F"
                self._trap_count_label.text = f"[b][color={c}]Traps in Queue: {queue_size}[/color][/b]"
                self._trap_next_label.text  = f"[b][color={c}]Next Trap: {next_trap_name}[/color][/b]"
        Clock.schedule_once(_update)

    def on_start(self) -> None:
        logging.getLogger(__name__).addHandler(LogtoUI(self.log_panels["All"].on_log))
        logger = logging.getLogger("Client")
        logger.info("Age of Mythology: Retold client commands:")
        logger.info("  /status              - show connection info and Atlantis Key progress")
        logger.info("  /scenarios (/progress) - list beaten, in-progress, and untouched scenarios")

    @staticmethod
    def start_ap_ui(ctx: "AoMContext") -> None:
        """Bootstrap the Kivy UI alongside the running asyncio loop.
        Stores both the manager and its task on `ctx` so the client can
        cancel them on shutdown.

        Caller: `main()` in ApClient.py.
        """
        ctx.ui = AoMManager(ctx)
        ctx.ui_task = asyncio.create_task(ctx.ui.async_run(), name="UI")
