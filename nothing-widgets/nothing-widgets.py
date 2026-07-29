#!/usr/bin/env python3
"""Nothing OS — interactive, movable desktop widgets (GTK3).
Drag a widget by its body to move it (position persists); buttons stay clickable."""
import gi, os, json, subprocess, math, time, random, urllib.request, glob, threading, shlex
from collections import deque
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa

# ---------- themes (live-swappable via the PALETTE card) ----------
# disp = big numerals, label = small caps headings, mono = data/meters.
NOTHING_FONTS = ('Ndot 57', 'NType 82', 'Lettera Mono LL')
JB            = ('JetBrainsMono NF Light', 'JetBrainsMono NF Medium', 'JetBrainsMono NF')
THEMES = {
    "MONO RED":  dict(accent="#E5484D", text="#F2F2F2", dim="#9A9A9A", faint="#5E5E5E",
                      base="rgba(9,9,10,0.88)", tile="#0A0A0A", hot="#160607",
                      fonts=JB),
    "NOTHING":   dict(accent="#D71921", text="#EDEDED", dim="#9A9A9A", faint="#5A5A5A",
                      base="rgba(11,11,13,0.86)", tile="#0A0A0A", hot="#160607",
                      fonts=NOTHING_FONTS),
    "AMBER":     dict(accent="#F5A524", text="#EDEAE3", dim="#9A9488", faint="#5E5A52",
                      base="rgba(12,11,9,0.88)", tile="#0B0A08", hot="#171006",
                      fonts=JB),
    "CYAN":      dict(accent="#22D3EE", text="#E8EDEF", dim="#8A9497", faint="#55605F",
                      base="rgba(9,11,13,0.88)", tile="#080A0C", hot="#04141A",
                      fonts=JB),
    "PHOSPHOR":  dict(accent="#4ADE80", text="#E6EDE6", dim="#7F8C7F", faint="#4F5A4F",
                      base="rgba(8,10,8,0.88)", tile="#070907", hot="#06160C",
                      fonts=JB),
    "MONOCHROME":dict(accent="#FFFFFF", text="#D8D8D8", dim="#7A7A7A", faint="#4E4E4E",
                      base="rgba(10,10,10,0.88)", tile="#0A0A0A", hot="#1A1A1A",
                      fonts=JB),
}
THEME_NAMES = list(THEMES)
THEME_FILE  = os.path.expanduser("~/.config/nothing-widgets/theme.json")

def load_theme_name():
    try:
        n = json.load(open(THEME_FILE)).get("theme")
        if n in THEMES: return n
    except Exception: pass
    return "MONO RED"

THEME_NAME = load_theme_name()
T = THEMES[THEME_NAME]
ACCENT = T["accent"]

def rgb(hexcol):
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))

CONF_DIR = os.path.expanduser("~/.config/nothing-widgets")
POS_FILE = os.path.join(CONF_DIR, "positions.json")
UI_FILE  = os.path.join(CONF_DIR, "ui-state.json")     # collapsed/expanded per widget
os.makedirs(CONF_DIR, exist_ok=True)
GLib.set_prgname("nothing-widget")   # stable WM_CLASS so the dock can skip our own windows

# ---------- shell helpers ----------
def sh(cmd):
    subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)

def out(cmd, timeout=4):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""

def dots(pct, n=12):
    try: pct = float(pct)
    except Exception: pct = 0
    f = max(0, min(n, round(pct / 100 * n)))
    return "●" * f + "·" * (n - f)

SPARK = "▁▂▃▄▅▆▇█"
def spark(vals, hi=100):
    # unicode block sparkline; hi=0 -> auto-scale to the window's own max
    vals = list(vals)
    if not vals: return ""
    top = hi if hi else (max(vals) or 1)
    out = []
    for v in vals:
        try: v = float(v)
        except Exception: v = 0.0
        v = max(0.0, min(top, v))
        out.append(SPARK[int(v / top * (len(SPARK) - 1))])
    return "".join(out)

def two_tone(pct, n, on=ACCENT, off="#3a3a3a"):
    # dotted progress with a lit (accent) run and a faint remainder — Pango markup
    try: pct = float(pct)
    except Exception: pct = 0.0
    f = max(0, min(n, round(pct / 100 * n)))
    return ("<span foreground='%s'>%s</span><span foreground='%s'>%s</span>"
            % (on, "●" * f, off, "·" * (n - f)))

HDOTS = []   # header accent dots — pulsed together by start_pulse()
def start_pulse():
    def beat():
        a = 0.40 + 0.60 * (0.5 + 0.5 * math.sin(time.time() * 2.2))
        for d in HDOTS:
            try: d.set_opacity(a)
            except Exception: pass
        return True
    GLib.timeout_add(100, beat)

def running(name):
    return bool(out("pgrep -f %s" % name))   # single spawn (was two)

# ---------- positions ----------
def load_pos():
    try:
        with open(POS_FILE) as fh: return json.load(fh)
    except Exception: return {}

def save_pos(name, x, y):
    p = load_pos(); p[name] = [x, y]
    with open(POS_FILE, "w") as fh: json.dump(p, fh)

# ---------- the single vertical pane ----------
# Every card in PANE_ORDER is auto-stacked down one column: each sits directly
# under the previous one, so collapsing a card pulls everything below it up and
# no hole is ever left behind. Only the dock and the orb live outside the pane.
PANE_X, PANE_TOP, PANE_GAP, PANE_W = 74, 16, 4, 452
POPOUT_W = 460          # every rail pop-out shares this width
NOTES_W  = 560          # Notes gets extra room to actually read/write
PANE_ORDER = ["clock", "system", "status", "net", "now", "sessions"]
# Everything else is reachable from the RAIL: a slim icon strip on the left edge.
RAIL_ORDER = ["controls", "pomo", "calendar", "notes", "arcade"]
PANE = {}          # wname -> Widget, filled as they are constructed

def relayout_pane():
    y = PANE_TOP
    for name in PANE_ORDER:
        w = PANE.get(name)
        if w is None or not w.get_visible(): continue
        cx, cy = w.get_position()
        if (cx, cy) != (PANE_X, y):        # only move drifted cards → no flicker
            w.move(PANE_X, y)
        # Stack by the CONTENT height (natural preferred), not get_size(): the
        # window's allocation can lag behind its real rendered height during
        # startup, which used to place the next card too high and overlap it.
        h = max(w.get_preferred_height()[1], w.get_size()[1])
        y += h + PANE_GAP
    return False

def pane_keeper():
    # Muffin re-maps sticky windows when you switch workspaces and can nudge them
    # out of place — re-assert the stack a couple of times per switch, cheaply.
    relayout_pane()
    return True

def schedule_relayout():
    # after GTK has settled the new sizes, not during the resize itself
    GLib.timeout_add(30, relayout_pane)

# ---------- collapsed/expanded state ----------
# Widgets that start life as a title bar; expand with the ▸ caret (state sticks).
START_COLLAPSED = set()

def load_ui():
    try:
        with open(UI_FILE) as fh: return json.load(fh)
    except Exception: return {}

def save_ui(name, collapsed):
    s = load_ui(); s[name] = bool(collapsed)
    try:
        with open(UI_FILE, "w") as fh: json.dump(s, fh)
    except Exception: pass

# ---------- styling ----------
CSS_T = """
.card { background-color: %(base)s; border: 1px solid rgba(255,255,255,0.13);
        border-radius: 8px; padding: 11px 14px; margin: 3px;
        transition: border-color 180ms ease, box-shadow 180ms ease; }
.card:hover, .card.hov {
        border-color: %(a)s;
        background-color: %(hot)s;
        box-shadow: inset 0 0 34px %(a25)s, 0 0 24px 3px %(a30)s; }
.sbar   { font-family:"%(mono)s"; font-size:14px; letter-spacing:1px; }
.calmonth { color:%(text)s; font-family:"%(disp)s"; font-size:17px; letter-spacing:1px; }
.calwd  { color:%(faint)s; font-family:"%(label)s"; font-size:11px; }
.cal    { color:%(dim)s; font-family:"%(mono)s"; font-size:13px; }
.calcell { background-color:transparent; border:1px solid transparent; border-radius:5px;
           color:%(dim)s; font-family:"%(mono)s"; font-size:13px; padding:0; }
.calcell:hover { border-color:%(a)s; color:#ffffff; background-color:%(hot)s; }
.calcell.today { color:#0a0a0a; background-color:%(a)s; border-color:%(a)s; }
.calinfo { color:%(faint)s; font-family:"%(mono)s"; font-size:11px; }
.title  { color:%(dim)s; font-family:"%(label)s"; font-size:12px; letter-spacing:2px; }
.hdot   { color:%(a)s; font-family:"%(mono)s"; font-size:13px; }
.rule   { background-color: rgba(255,255,255,0.07); min-height:1px; }
.clock  { color:%(text)s; font-family:"%(disp)s"; font-size:52px; }
.csec   { color:%(a)s; font-family:"%(disp)s"; font-size:19px; }
.date   { color:%(dim)s; font-family:"%(disp)s"; font-size:15px; letter-spacing:1px; }
.k      { color:%(dim)s; font-family:"%(label)s"; font-size:12px; letter-spacing:1px; }
.v      { color:%(text)s; font-family:"%(mono)s"; font-size:13px; }
.meter  { font-family:"%(mono)s"; font-size:13px; color:%(text)s; }
.meter.hot { color:%(a)s; }
.big    { color:%(text)s; font-family:"%(disp)s"; font-size:17px; }
.dim    { color:%(dim)s; font-family:"%(mono)s"; font-size:12px; }
.faint  { color:%(faint)s; font-family:"%(mono)s"; font-size:12px; }
.dotlit { color:%(a)s; font-family:"%(mono)s"; font-size:12px; }
.dotidle{ color:#3a3a3a; font-family:"%(mono)s"; font-size:12px; }
.tile   { background-color:%(tile)s; border:1px solid rgba(255,255,255,0.08); border-radius:5px;
          color:%(dim)s; font-family:"%(label)s"; font-size:12px; letter-spacing:1px; padding:7px 6px; }
.tile:hover { border-color:%(a)s; color:%(text)s; box-shadow: 0 0 12px 0 %(a25)s; }
.tile.on { background-color:%(hot)s; border-color:%(a)s; color:%(text)s; }
.app    { background-color:%(tile)s; border:1px solid rgba(255,255,255,0.08); border-radius:8px;
          padding:10px 8px 7px;
          transition: background-color 160ms ease, border-color 160ms ease, box-shadow 160ms ease; }
.app:hover { border-color:%(a)s; background-color:%(hot)s; box-shadow: 0 0 16px 1px %(a30)s; }
.applabel { color:%(dim)s; font-family:"%(label)s"; font-size:12px; letter-spacing:1px; }
.app:hover .applabel { color:#ffffff; }
.rundot   { color:%(a)s; font-family:"%(mono)s"; font-size:11px; }
.rundotoff{ color:#2c2c2c; font-family:"%(mono)s"; font-size:11px; }
.dockbar  { background-color: rgba(17,17,20,0.80); border:1px solid rgba(255,255,255,0.12);
            border-radius: 22px; padding: 7px 12px; }
.dapp     { background-color:transparent; border:0; border-radius:14px; padding:2px 5px;
            transition: background-color 140ms ease; }
.dapp:hover { background-color: rgba(255,255,255,0.09); }
.viz      { font-family:"%(mono)s"; font-size:22px; color:%(a)s; letter-spacing:1px; }
.vizoff   { font-family:"%(mono)s"; font-size:22px; color:#333333; letter-spacing:1px; }
.clock2   { color:%(text)s; font-family:"%(disp)s"; font-size:34px; }
.notes, .notes text { background-color:transparent; color:%(text)s;
            font-family:"%(mono)s"; font-size:13px; caret-color:%(a)s; }
.chat, .chat text { background-color:transparent; color:%(dim)s;
            font-family:"%(mono)s"; font-size:15px; }
.chatin, .chatin text { background-color:%(tile)s; color:%(text)s; caret-color:%(a)s;
            font-family:"%(mono)s"; font-size:15px; border:1px solid rgba(255,255,255,0.12);
            border-radius:6px; padding:7px 10px; }
.chatin:focus { border-color:%(a)s; }
.sendbtn { background-color:%(hot)s; border:1px solid %(a)s; border-radius:6px;
            color:%(text)s; font-family:"%(label)s"; font-size:14px; letter-spacing:2px; padding:7px 16px; }
.sendbtn:hover { background-color:%(a)s; }
.chatq { color:%(a)s; font-family:"%(label)s"; font-size:13px; letter-spacing:1px; }
.chata { color:%(dim)s; font-family:"%(mono)s"; font-size:15px; }
.chatsys { color:%(faint)s; font-family:"%(mono)s"; font-size:13px; }
.media  { background-color:transparent; border:0; color:%(dim)s;
          font-family:"%(mono)s"; font-size:18px; padding:1px 11px; }
.media:hover { color:%(a)s; }
.caret  { background-color:transparent; border:0; color:%(dim)s; padding:0 6px 0 0;
          font-family:"%(mono)s"; font-size:14px; }
.caret:hover { color:%(a)s; }
/* --- SESSIONS timeline --- */
.srow   { background-color:transparent; border:0; border-radius:5px; padding:3px 6px;
          transition: background-color 140ms ease; }
.srow:hover { background-color:%(a15)s; }
.srow.child { padding:1px 6px 1px 14px; }
.scount { background-color:transparent; border:1px solid rgba(255,255,255,0.14);
          border-radius:9px; color:%(dim)s; padding:0 6px; margin-left:4px;
          font-family:"%(mono)s"; font-size:11px; }
.scount:hover { border-color:%(a)s; color:%(text)s; }
.stime  { color:%(dim)s; font-family:"%(mono)s"; font-size:12px; }
.sdir   { color:%(text)s; font-family:"%(label)s"; font-size:13px; letter-spacing:1px; }
.stask  { color:%(dim)s; font-family:"%(mono)s"; font-size:12px; }
.srail  { color:#3a3a3a; font-family:"%(mono)s"; font-size:14px; }
.srail.live { color:%(a)s; }
/* --- floating Jarvis orb --- */
.orb    { background-color:%(base)s; border:1px solid %(a)s; border-radius:26px; padding:0; }
.orb:hover { background-color:%(hot)s; box-shadow: 0 0 20px 2px %(a30)s; }
.orbicon { color:%(a)s; font-family:"%(mono)s"; font-size:17px; }
/* --- RAIL (slim launcher strip) --- */
.railbar { background-color:%(base)s; border:1px solid rgba(255,255,255,0.11);
           border-radius:20px; padding:8px 5px; }
.railbtn { background-color:transparent; border:1px solid transparent; border-radius:12px;
           color:%(dim)s; font-family:"%(mono)s"; font-size:16px; padding:6px 7px;
           transition: background-color 140ms ease, color 140ms ease, border-color 140ms ease; }
.railbtn:hover { background-color:rgba(255,255,255,0.08); color:%(text)s; }
.railbtn.on { color:%(a)s; border-color:%(a)s; background-color:%(hot)s; }
.railtag { color:%(faint)s; font-family:"%(mono)s"; font-size:10px; }
/* --- PALETTE picker --- */
.swatch { border:1px solid rgba(255,255,255,0.18); border-radius:6px; padding:0;
          transition: border-color 140ms ease, box-shadow 140ms ease; }
.swatch:hover { border-color:#ffffff; }
.swatch.on { border-color:%(a)s; box-shadow: 0 0 10px 1px %(a30)s; }
.swname { color:%(dim)s; font-family:"%(label)s"; font-size:11px; letter-spacing:1px; }
"""

def _alpha(hexcol, a):
    r, g, b = [int(round(v * 255)) for v in rgb(hexcol)]
    return "rgba(%d,%d,%d,%.2f)" % (r, g, b, a)

def build_css(t):
    disp, label, mono = t["fonts"]
    return (CSS_T % {"a": t["accent"], "text": t["text"], "dim": t["dim"],
                     "faint": t["faint"], "base": t["base"], "tile": t["tile"],
                     "hot": t["hot"], "disp": disp, "label": label, "mono": mono,
                     "a15": _alpha(t["accent"], 0.15), "a25": _alpha(t["accent"], 0.25),
                     "a30": _alpha(t["accent"], 0.34)}).encode()

_PROVIDER = None
def apply_css():
    global _PROVIDER
    scr = Gdk.Screen.get_default()
    if _PROVIDER is not None:
        Gtk.StyleContext.remove_provider_for_screen(scr, _PROVIDER)
    _PROVIDER = Gtk.CssProvider(); _PROVIDER.load_from_data(build_css(T))
    Gtk.StyleContext.add_provider_for_screen(scr, _PROVIDER, Gtk.STYLE_PROVIDER_PRIORITY_USER)

def set_theme(name):
    """Swap the palette live: restyle every widget, no restart."""
    global THEME_NAME, T, ACCENT
    if name not in THEMES: return
    THEME_NAME = name; T = THEMES[name]; ACCENT = T["accent"]
    try: json.dump({"theme": name}, open(THEME_FILE, "w"))
    except Exception: pass
    apply_css()
    for fn in THEME_HOOKS:
        try: fn()
        except Exception: pass
    # retint the wallpaper + conky animation to match (runs in the background)
    script = os.path.expanduser("~/.config/conky/nothing/theme-apply.sh")
    if os.path.exists(script):
        sh("%s %s" % (shlex.quote(script), shlex.quote(T["accent"])))

THEME_HOOKS = []   # widgets that paint themselves (Arcade, chat tags) re-read ACCENT

# ---------- base widget window ----------
class Widget(Gtk.Window):
    def __init__(self, name, x, y, w=None, focusable=False):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.wname = name
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(focusable)   # editable widgets (Notes) opt in to focus
        self.set_focus_on_map(False)
        self.set_keep_below(True)
        self.stick()
        # UTILITY: draggable (the WM honours begin_move_drag) and kept out of the
        # taskbar. accept_focus=False already stops clicks stealing focus — DOCK
        # would block dragging entirely (WMs treat docks as fixed panel furniture).
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        # ...but a UTILITY window with no WM_TRANSIENT_FOR is subject to Muffin's
        # "promotion due to group": it inherits the highest layer of ANY window in
        # its WM_HINTS group (mutter stack.c: compute_layer ->
        # get_maximum_layer_in_group). GTK gives every window of a process the same
        # group leader, so ONE keep_above window — a rail pop-out, the assistant,
        # the REPOS dialog — used to drag the entire desktop into META_LAYER_TOP,
        # covering the user's real windows and making them unclickable.
        # Making each window its own group leader isolates them, so a widget can
        # never be promoted by one of its siblings.
        self.connect("realize", self._isolate_wm_group)
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis: self.set_visual(vis)
        self.in_pane = name in PANE_ORDER
        if self.in_pane:
            PANE[name] = self
            self.set_size_request(PANE_W, -1)
        elif w:
            self.set_size_request(w, -1)
        self._last_press = 0
        self._cardbox = None
        self._armed = False; self._moved = False
        self._px = self._py = 0; self._save_scheduled = False
        if self.in_pane:
            self.move(PANE_X, y)      # real slot assigned by relayout_pane()
        else:
            pos = load_pos().get(name, [x, y])
            self.move(pos[0], pos[1])
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK
                        | Gdk.EventMask.BUTTON1_MOTION_MASK
                        | Gdk.EventMask.ENTER_NOTIFY_MASK
                        | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("button-press-event", self._press)
        self.connect("button-release-event", self._release)
        self.connect("motion-notify-event", self._motion)
        self.connect("configure-event", self._configure)
        self.connect("enter-notify-event", self._enter)
        self.connect("leave-notify-event", self._leave)

    def _isolate_wm_group(self, *_):
        # own group leader == no shared group == no cross-widget layer promotion
        gw = self.get_window()
        if gw is not None:
            try: gw.set_group(gw)
            except Exception: pass

    action = None   # double-click a card to launch this

    def add(self, widget):        # remember the card box so we can toggle its glow
        self._cardbox = widget
        super().add(widget)

    def _enter(self, w, e):
        if self._cardbox: self._cardbox.get_style_context().add_class("hov")
        return False

    def _leave(self, w, e):
        # keep the glow while the pointer is over a child (INFERIOR crossing)
        if self._cardbox and e.detail != Gdk.NotifyType.INFERIOR:
            self._cardbox.get_style_context().remove_class("hov")
        return False

    def first(self, fn):
        # run the first data fetch AFTER the window is mapped/painted, so the
        # desktop appears instantly instead of waiting on blocking shell calls.
        def once(): fn(); return False
        GLib.idle_add(once)

    def _press(self, w, e):
        if e.button != 1:
            return False
        now = e.time
        if self._last_press and (now - self._last_press) < 350:
            # second click of a double-click → arm: drag if the pointer moves,
            # otherwise open the app on release.
            self._last_press = 0
            self._armed = True; self._moved = False
            self._px, self._py = e.x_root, e.y_root
        else:
            # a single click does nothing on its own — widgets never move/open
            # by accident; you must double-click (and optionally drag).
            self._last_press = now
            self._armed = False
        return False

    def _motion(self, w, e):
        if self._armed and (e.state & Gdk.ModifierType.BUTTON1_MASK):
            if (e.x_root - self._px) ** 2 + (e.y_root - self._py) ** 2 > 36:  # >6px = drag
                self._armed = False; self._moved = True
                self.begin_move_drag(1, int(e.x_root), int(e.y_root), e.time)
        return False

    def _release(self, w, e):
        # double-click released without dragging → open the app
        if self._armed and not self._moved and self.action:
            sh(self.action)
        self._armed = False
        return False

    def _configure(self, w, e):
        # pane cards are placed by relayout_pane(); their position isn't theirs to keep
        if self.in_pane: return False
        # persist position after a move, throttled to one write per move
        if not self._save_scheduled:
            self._save_scheduled = True
            GLib.timeout_add(500, self._flush_pos)
        return False

    def _flush_pos(self):
        self._save_scheduled = False
        x, y = self.get_position(); save_pos(self.wname, x, y)
        return False

    def header(self, text, collapsible=False, body=None, subtitle=None):
        box = Gtk.Box(spacing=8)
        if collapsible:
            self._caret = Gtk.Button(label="▾")
            self._caret.get_style_context().add_class("caret")
            self._caret.set_relief(Gtk.ReliefStyle.NONE)
            self._caret.connect("clicked", lambda *_: self.toggle_collapse())
            box.pack_start(self._caret, False, False, 0)
        lbl = L(text, "title"); lbl.set_hexpand(True); lbl.set_xalign(0)
        dot = L("●", "hdot"); dot.set_valign(Gtk.Align.CENTER)
        HDOTS.append(dot)   # pulsed together by start_pulse()
        box.pack_start(lbl, True, True, 0)
        if subtitle is not None:
            self._subtitle = subtitle; box.pack_end(subtitle, False, False, 0)
        box.pack_end(dot, False, False, 0)
        if collapsible:
            self._body = body
            self._titlelbl = lbl
        return box

    # ---- collapse / expand -------------------------------------------------
    # The body Box is hidden and the window shrinks to its title bar. Position
    # is untouched, so a card expands back into the same slot it came from.
    _body = None; _caret = None; _collapsed = False

    def setup_collapse(self, body, default=None):
        """Call after add(); `body` is hidden when collapsed."""
        self._body = body
        want = load_ui().get(self.wname,
                             self.wname in START_COLLAPSED if default is None else default)
        if want: GLib.idle_add(self._apply_collapse, True, False)

    def toggle_collapse(self, *_):
        self._apply_collapse(not self._collapsed, True)

    def _apply_collapse(self, collapsed, persist):
        if self._body is None: return False
        self._collapsed = collapsed
        self._body.set_visible(not collapsed)
        self._body.set_no_show_all(collapsed)
        if self._caret: self._caret.set_label("▸" if collapsed else "▾")
        self.resize(1, 1)                      # let the window shrink to the header
        if persist: save_ui(self.wname, collapsed)
        self.on_collapse(collapsed)            # subclasses pause work while hidden
        schedule_relayout()                    # close the gap this just opened
        return False

    def on_collapse(self, collapsed):
        pass

def L(text, cls, xalign=0):
    lbl = Gtk.Label(label=text, xalign=xalign)
    for c in cls.split(): lbl.get_style_context().add_class(c)
    lbl.set_use_markup(False)
    return lbl

def icon_img(cands, size=34, file=None):
    if file and os.path.exists(file):
        try:
            from gi.repository import GdkPixbuf
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(file, size, size)
            return Gtk.Image.new_from_pixbuf(pb)
        except Exception: pass
    it = Gtk.IconTheme.get_default()
    name = next((n for n in cands if it.has_icon(n)), "application-x-executable")
    img = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.DIALOG)
    img.set_pixel_size(size)
    return img

def rule():
    r = Gtk.Box(); r.get_style_context().add_class("rule")
    r.set_size_request(-1, 1); r.set_margin_top(2); r.set_margin_bottom(2)
    return r

def vbox(spacing=6, m=0):
    # styled "card" box: background + hairline border + radius come from CSS padding
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    b.get_style_context().add_class("card")
    return b

# ---------- CLOCK ----------
class Clock(Widget):
    def __init__(self):
        super().__init__("clock", 50, 40, 600)
        self.action = "gnome-calendar"
        b = vbox(7, m=24)
        top = Gtk.Box(spacing=10); top.set_valign(Gtk.Align.END)
        self.time = L("--:--", "clock")
        self.sec  = L("00", "csec"); self.sec.set_valign(Gtk.Align.END)
        self.sec.set_margin_bottom(14)
        top.pack_start(self.time, False, False, 0)
        top.pack_start(self.sec, False, False, 0)
        b.pack_start(top, False, False, 0)
        self.secbar = Gtk.Label(); self.secbar.set_xalign(0)
        self.secbar.get_style_context().add_class("sbar")
        self.secbar.set_margin_top(4); self.secbar.set_margin_bottom(2)
        b.pack_start(self.secbar, False, False, 0)
        self.date = L("", "date")
        b.pack_start(self.date, False, False, 0)
        self.add(b); self.first(self.tick); GLib.timeout_add(1000, self.tick)
    def tick(self):
        t = time.localtime()
        self.time.set_text(time.strftime("%H:%M", t))
        self.sec.set_text(time.strftime("%S", t))
        self.secbar.set_markup(two_tone(t.tm_sec / 60 * 100, 30))
        hh = t.tm_hour
        greet = ("GOOD MORNING" if hh < 12 else "GOOD AFTERNOON" if hh < 17
                 else "GOOD EVENING" if hh < 21 else "GOOD NIGHT")
        self.date.set_text("%s · %s" % (greet, time.strftime("%a %d %b %Y", t).upper()))
        return True

# ---------- QUICK CONTROLS ----------
class Controls(Widget):
    def __init__(self):
        super().__init__("controls", 1130, 40, 710)
        b = vbox(11, m=22); b.pack_start(self.header("QUICK CONTROLS"), False, False, 0)
        grid = Gtk.Grid(row_spacing=5, column_spacing=5)
        grid.set_column_homogeneous(True); grid.set_row_homogeneous(True)
        self.tiles = {}
        defs = [("wifi","WI-FI",0,0),("bt","BLUETOOTH",0,1),
                ("air","AIRPLANE",1,0),("dnd","DO NOT DISTURB",1,1)]
        for key,label,r,c in defs:
            btn = Gtk.Button(label=label); btn.get_style_context().add_class("tile")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_hexpand(True); btn.set_vexpand(True)
            btn.set_size_request(-1, 52)
            btn.connect("clicked", self.toggle, key)
            self.tiles[key] = btn; grid.attach(btn, c, r, 1, 1)
        b.pack_start(grid, True, True, 0)
        self.add(b); self.first(self.refresh); GLib.timeout_add(4000, self.refresh)
    def states(self):
        wifi = out("nmcli -t -f WIFI radio") == "enabled"
        bt   = "yes" in out("bluetoothctl show | grep -i Powered:")
        air  = (not wifi) and (not bt)
        dnd  = out("gsettings get org.cinnamon.desktop.notifications display-notifications") == "false"
        return {"wifi":wifi,"bt":bt,"air":air,"dnd":dnd}
    def refresh(self):
        s = self.states()
        for k,btn in self.tiles.items():
            ctx = btn.get_style_context()
            (ctx.add_class if s[k] else ctx.remove_class)("on")
        return True
    def toggle(self, btn, key):
        s = self.states()
        if key == "wifi":
            sh("nmcli radio wifi %s" % ("off" if s["wifi"] else "on"))
        elif key == "bt":
            if s["bt"]: sh("bluetoothctl power off")
            else: sh("rfkill unblock bluetooth; bluetoothctl power on")
        elif key == "air":
            if s["air"]: sh("nmcli radio wifi on; rfkill unblock bluetooth; bluetoothctl power on")
            else: sh("nmcli radio wifi off; bluetoothctl power off")
        elif key == "dnd":
            nv = "true" if s["dnd"] else "false"
            sh("gsettings set org.cinnamon.desktop.notifications display-notifications %s" % nv)
        GLib.timeout_add(700, self.refresh)

# ---------- SYSTEM ----------
class System(Widget):
    def __init__(self):
        super().__init__("system", 50, 356, 600)
        self.action = "gnome-system-monitor"
        b = vbox(8, m=22); b.pack_start(self.header("SYSTEM · i5-1135G7"), False, False, 0)
        self.rows = {}
        self.hist = {"CPU": deque(maxlen=26), "RAM": deque(maxlen=26)}
        for k in ("CPU","RAM","TMP","BAT"):
            row = Gtk.Box(spacing=18)
            key = L(k, "k"); key.set_size_request(32,-1)
            meter = L("", "meter"); meter.set_xalign(0)
            val = L("", "dim"); val.set_xalign(1); val.set_size_request(54,-1)
            row.pack_start(key, False, False, 0)
            row.pack_start(meter, False, False, 0)
            row.pack_end(val, False, False, 0)
            self.rows[k] = (meter,val); b.pack_start(row, False, False, 0)
        self._pcpu = None
        self.add(b); self.first(self.tick); GLib.timeout_add(2000, self.tick)
    # --- all reads below hit /proc or /sys directly: zero subprocess spawns ---
    def _cpu(self):
        try:
            v = list(map(int, open("/proc/stat").readline().split()[1:]))
            idle = v[3] + (v[4] if len(v) > 4 else 0); total = sum(v)
            if self._pcpu is None:
                self._pcpu = (total, idle); return 0
            pt, pi = self._pcpu; self._pcpu = (total, idle)
            dt = total - pt
            return int((1 - (idle - pi) / dt) * 100) if dt > 0 else 0
        except Exception: return 0
    def _mem(self):
        try:
            mi = {}
            for ln in open("/proc/meminfo"):
                k, _, rest = ln.partition(":"); mi[k] = int(rest.split()[0])
            return int((1 - mi.get("MemAvailable", mi["MemFree"]) / mi["MemTotal"]) * 100)
        except Exception: return 0
    def _temp(self):
        best = 0
        for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            try: best = max(best, int(open(p).read()))
            except Exception: pass
        return best // 1000
    def _bat(self):
        try: return open("/sys/class/power_supply/BAT0/capacity").read().strip() or "0"
        except Exception: return "0"
    def _chg(self):
        for p in glob.glob("/sys/class/power_supply/A*/online"):
            try:
                if open(p).read().strip() == "1": return True
            except Exception: pass
        return False
    def tick(self):
        data = {"CPU": str(self._cpu()), "RAM": str(self._mem()),
                "TMP": str(self._temp()), "BAT": self._bat()}
        chg = self._chg()
        unit = {"CPU":"%","RAM":"%","TMP":"°","BAT":"%"}
        def hot(k,v):
            try: v=int(v)
            except: return False
            return (k=="TMP" and v>=80) or (k=="BAT" and v<=20 and not chg) or (k in("CPU","RAM") and v>=90)
        for k,(meter,val) in self.rows.items():
            if k in self.hist:   # CPU / RAM → scrolling sparkline history
                self.hist[k].append(int(data[k]) if data[k].isdigit() else 0)
                meter.set_text(spark(self.hist[k], 100))
            else:                # TMP / BAT → steady dotted meter
                meter.set_text(dots(data[k], 22))
            txt = data[k]+unit[k]
            if k=="BAT" and chg: txt += " +"
            val.set_text(txt)
            ctx = meter.get_style_context()
            (ctx.add_class if hot(k,data[k]) else ctx.remove_class)("hot")
        return True

# ---------- NETWORK ----------
class Network(Widget):
    def __init__(self):
        super().__init__("net", 1130, 344, 710)
        self.action = "cinnamon-settings network"
        b = vbox(8, m=22); b.pack_start(self.header("NETWORK"), False, False, 0)
        self.iface = out("ip route 2>/dev/null | awk '/default/{print $5; exit}'") or "wlp0s20f3"
        self.ssid = self._kv(b, "SSID")
        self.ip   = self._kv(b, "LOCAL IP")
        sp = Gtk.Box(spacing=22)
        self.down = L("↓ 0", "dim"); self.up = L("↑ 0", "dim")
        sp.pack_start(self.down, False, False, 0)
        sp.pack_start(self.up, False, False, 0)
        sp.set_margin_top(2); b.pack_start(sp, False, False, 0)
        self.nhist = deque(maxlen=26)
        self.nspark = L("", "meter"); self.nspark.set_xalign(0); self.nspark.set_margin_top(2)
        b.pack_start(self.nspark, False, False, 0)
        self.add(b); self._rx=self._tx=None; self.first(self.tick); GLib.timeout_add(2000, self.tick)
    def _kv(self, b, k):
        row = Gtk.Box(spacing=14)
        key = L(k, "k"); key.set_size_request(66,-1)
        val = L("—", "v"); val.set_hexpand(True); val.set_xalign(1)
        row.pack_start(key, False, False, 0); row.pack_end(val, True, True, 0)
        b.pack_start(row, False, False, 0); return val
    def _bytes(self, d):
        try: return int(open("/sys/class/net/%s/statistics/%s_bytes" % (self.iface, d)).read())
        except Exception: return 0
    def tick(self):
        self.ssid.set_text(out("iwgetid -r 2>/dev/null || /usr/sbin/iwgetid -r 2>/dev/null") or "—")
        self.ip.set_text(out("ip -4 addr show %s 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1" % self.iface) or "—")
        rx, tx = self._bytes("rx"), self._bytes("tx")
        if self._rx is not None:
            dr = (rx-self._rx)/2
            self.down.set_text("↓ %s/s" % human(dr))
            self.up.set_text("↑ %s/s" % human((tx-self._tx)/2))
            self.nhist.append(dr)
            self.nspark.set_text(spark(self.nhist, 0))   # auto-scale to recent peak
        self._rx, self._tx = rx, tx
        return True

def human(n):
    n = max(0, n)
    for u in ("B","K","M","G"):
        if n < 1024: return "%d%s" % (n, u)
        n /= 1024
    return "%dG" % n

# ---------- NOW PLAYING ----------
class NowPlaying(Widget):
    def __init__(self):
        super().__init__("now", 1130, 560, 710)
        b = vbox(9, m=22); b.pack_start(self.header("NOW PLAYING"), False, False, 0)
        self.track = L("—", "dim"); self.track.set_line_wrap(True); self.track.set_max_width_chars(34)
        self.track.set_xalign(0)
        b.pack_start(self.track, False, False, 0)
        self.viz = L("", "vizoff"); self.viz.set_xalign(0); self.viz.set_margin_top(4)
        b.pack_start(self.viz, False, False, 0)
        ctrl = Gtk.Box(spacing=8)
        for sym,cmd in [("⏮","previous"),("⏯","play-pause"),("⏭","next")]:
            btn = Gtk.Button(label=sym); btn.get_style_context().add_class("media")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.connect("clicked", lambda w,c=cmd: sh("playerctl "+c))
            ctrl.pack_start(btn, False, False, 0)
        b.pack_start(ctrl, False, False, 0)
        self._bars = [0.05] * 22; self._playing = False
        self.add(b); self.first(self.tick); GLib.timeout_add(2000, self.tick)
        GLib.timeout_add(90, self._animate)
    def _animate(self):
        if not self._playing and max(self._bars) < 0.06:
            return True    # idle & settled → skip the redraw (saves battery)
        t = time.time()
        for i in range(len(self._bars)):
            if self._playing:
                w = abs(math.sin(t * 5.5 + i * 0.5) * math.sin(t * 2.1 + i * 0.9))
                tgt = max(0.06, min(1.0, w * 0.8 + random.random() * 0.3))
                self._bars[i] += (tgt - self._bars[i]) * 0.5
            else:
                self._bars[i] += (0.05 - self._bars[i]) * 0.15
        self.viz.set_text("".join(SPARK[min(7, int(b * 8))] for b in self._bars))
        self.viz.get_style_context().remove_class("vizoff" if self._playing else "viz")
        self.viz.get_style_context().add_class("viz" if self._playing else "vizoff")
        return True
    def tick(self):
        self._playing = (out("playerctl status 2>/dev/null") == "Playing")
        t = out("playerctl metadata --format '{{artist}} — {{title}}' 2>/dev/null")
        self.track.set_text((t[:60] if t else "— nothing playing —"))
        return True

# ---------- SYSTEM STATUS (agentic) ----------
class Status(Widget):
    def __init__(self):
        super().__init__("status", 50, 636, 600)
        self.action = "gnome-system-monitor"
        b = vbox(8, m=22); b.pack_start(self.header("SYSTEM :// STATUS"), False, False, 0)
        self.line = L("", "faint"); self.line.set_xalign(0)
        b.pack_start(self.line, False, False, 0)
        self.svc = Gtk.Box(spacing=18); self.dots = {}
        for name in ("VICINAE","WHISPR","BT","BG"):
            cell = Gtk.Box(spacing=6)
            d = L("●", "dotidle"); nm = L(name, "faint")
            cell.pack_start(d, False, False, 0); cell.pack_start(nm, False, False, 0)
            self.dots[name] = d; self.svc.pack_start(cell, False, False, 0)
        self.svc.set_margin_top(2); b.pack_start(self.svc, False, False, 0)
        self.add(b); self.first(self.tick); GLib.timeout_add(3000, self.tick)
    def tick(self):
        up = out("uptime -p 2>/dev/null | sed 's/^up //' | sed 's/ hours\\?/h/;s/ minutes\\?/m/;s/,//g'") or "—"
        try: load = open("/proc/loadavg").read().split()[0]
        except Exception: load = "—"
        procs = str(sum(1 for p in os.listdir("/proc") if p.isdigit()))
        self.line.set_text("up %s   load %s   %sp" % (up, load, procs))
        state = {"VICINAE":running("vicinae"), "WHISPR":running("open-whispr"),
                 "BT":running("bluetoothd"), "BG":running("conky")}
        for name,d in self.dots.items():
            ctx = d.get_style_context()
            if state[name]:
                ctx.add_class("dotlit"); ctx.remove_class("dotidle")
            else:
                ctx.add_class("dotidle"); ctx.remove_class("dotlit")
        return True

# ---------- LIVE DOCK (macOS-style: open apps + pinned, magnify on hover) ----------
class Launcher(Widget):
    # (WM_CLASS match substrings, icon candidates, label, launch cmd, icon-file)
    PINS = [
        (["brave"],                     ["brave-browser","brave","com.brave.Browser"], "BRAVE",    "brave-browser", None),
        (["code"],                      ["visual-studio-code","code"],                 "CODE",     "code", "/usr/share/pixmaps/vscode.png"),
        (["gnome-terminal","terminal"], ["org.gnome.Terminal","utilities-terminal"],   "TERMINAL", "gnome-terminal", None),
        (["nemo"],                      ["nemo","system-file-manager"],                "FILES",    "nemo", None),
    ]
    SKIP = ("nothing", "conky", "cinnamon", "nemo-desktop", "vicinae", "python", "plank")
    BASE, MAG = 40, 56   # icon px at rest / magnified

    def __init__(self):
        super().__init__("dock", 660, 980)   # no fixed width — sizes to icons, auto-centred
        self._items = []; self._anim = None; self._sig = None
        self.row = Gtk.Box(spacing=8); self.row.get_style_context().add_class("dockbar")
        self.row.set_halign(Gtk.Align.CENTER)
        self.add(self.row)
        self.first(self.refresh); GLib.timeout_add(1500, self.refresh)

    def _wins(self):
        res = []
        for line in out("wmctrl -lx 2>/dev/null").splitlines():
            p = line.split(None, 4)
            if len(p) < 4: continue
            wid, desk, wc = p[0], p[1], p[2]
            title = p[4] if len(p) > 4 else ""
            if desk == "-1" or any(s in wc.lower() for s in self.SKIP): continue
            res.append((wid, wc, title))
        return res

    def refresh(self):
        wins = self._wins()
        sig = tuple(sorted(w[0] + w[1] for w in wins))
        if sig == self._sig: return True    # unchanged → don't rebuild (no flicker)
        self._sig = sig
        for c in self.row.get_children(): self.row.remove(c)
        self._items = []; used = set()
        for matches, icons, label, cmd, ifile in self.PINS:   # pinned first
            wid = next((w for w, wc, ti in wins if any(m in wc.lower() for m in matches)), None)
            if wid: used.add(wid)
            self.row.pack_start(self._item(icons, label, cmd, ifile, wid), False, False, 0)
        for w, wc, ti in wins:                                 # then other open apps
            if w in used: continue
            inst, cls = wc.split(".")[0], wc.split(".")[-1]
            self.row.pack_start(self._item([inst, inst.lower(), cls.lower(), cls],
                                           (cls or inst)[:14], None, None, w),
                                False, False, 0)
        self.row.show_all()
        GLib.idle_add(self._recenter)     # keep the pill centred as it grows/shrinks
        return True

    def _recenter(self):
        nat = self.get_preferred_width()[1]
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        geo = mon.get_geometry()
        y = load_pos().get("dock", [0, 980])[1]
        self.move(geo.x + max(0, (geo.width - nat) // 2), y)
        return False

    def _item(self, icons, label, cmd, ifile, wid):
        btn = Gtk.Button(); btn.get_style_context().add_class("dapp")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        slot = Gtk.Box(); slot.set_size_request(self.MAG + 4, self.MAG)  # reserved so magnify never reflows
        img = icon_img(icons, self.BASE, ifile)
        img.set_valign(Gtk.Align.END); img.set_halign(Gtk.Align.CENTER)
        slot.pack_start(img, True, True, 0)
        cell.pack_start(slot, False, False, 0)
        cell.pack_start(L("●" if wid else " ", "rundot" if wid else "rundotoff", 0.5), False, False, 0)
        btn.add(cell)
        btn._img = img; btn._size = float(self.BASE); btn._target = self.BASE
        btn.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        btn.connect("enter-notify-event", self._hover, True)
        btn.connect("leave-notify-event", self._hover, False)
        btn.connect("clicked", self._activate, wid, cmd)
        btn.connect("button-press-event", self._menu, wid, cmd, label)
        self._items.append(btn)
        return btn

    def _activate(self, btn, wid, cmd):
        if wid: sh("wmctrl -i -a %s" % wid)   # focus/raise the open window
        elif cmd: sh(cmd)                     # launch a pinned app that isn't running

    def _menu(self, btn, e, wid, cmd, name):
        # right-click → taskbar-style context menu
        if e.button != 3: return False
        m = Gtk.Menu()
        hdr = Gtk.MenuItem(label=name or "App"); hdr.set_sensitive(False)
        m.append(hdr); m.append(Gtk.SeparatorMenuItem())
        def item(text, fn):
            mi = Gtk.MenuItem(label=text); mi.connect("activate", lambda *_: fn()); m.append(mi)
        if wid:
            item("Focus",  lambda: sh("wmctrl -i -a %s" % wid))
            item("Close",  lambda: sh("wmctrl -ic %s" % wid))   # graceful _NET_CLOSE_WINDOW
        if cmd:
            item("Open new window" if wid else "Open", lambda: sh(cmd))
        m.show_all(); m.popup_at_pointer(e)
        return True

    def _hover(self, btn, e, enter):
        btn._target = self.MAG if enter else self.BASE
        if self._anim is None:
            self._anim = GLib.timeout_add(16, self._animate)   # ~60fps ease
        return False

    def _animate(self):
        busy = False
        for btn in self._items:
            d = btn._target - btn._size
            if abs(d) > 0.6:
                btn._size += d * 0.30; busy = True
            elif btn._size != btn._target:
                btn._size = float(btn._target)
            btn._img.set_pixel_size(int(round(btn._size)))
        if not busy: self._anim = None; return False
        return True

# ---------- CALENDAR (centrepiece) ----------
class Calendar(Widget):
    def __init__(self):
        super().__init__("calendar", 705, 330, 410)
        self.action = "gnome-calendar"
        import calendar, datetime
        today = datetime.date.today()
        # collapsed, the header keeps today's date visible so the card still informs
        sub = L(today.strftime("%a %d %b").upper(), "calinfo")
        b = vbox(7, m=22)
        b.pack_start(self.header("CALENDAR", collapsible=True, subtitle=sub), False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.pack_start(L(today.strftime("%B %Y").upper(), "calmonth"), False, False, 0)
        grid = Gtk.Grid(row_spacing=2, column_spacing=2); grid.set_column_homogeneous(True)
        for c, w in enumerate(("MO","TU","WE","TH","FR","SA","SU")):
            grid.attach(L(w, "calwd", 0.5), c, 0, 1, 1)
        for r, week in enumerate(calendar.monthcalendar(today.year, today.month), start=1):
            for c, day in enumerate(week):
                if day == 0:
                    cell = L("", "cal", 0.5)
                else:
                    cell = Gtk.Button(label="%d" % day)
                    cell.set_relief(Gtk.ReliefStyle.NONE)
                    ctx = cell.get_style_context(); ctx.add_class("calcell")
                    if day == today.day: ctx.add_class("today")
                    cell.connect("clicked", lambda *a: sh("gnome-calendar"))
                cell.set_size_request(52, 34)
                grid.attach(cell, c, r, 1, 1)
        body.pack_start(grid, False, False, 0)
        # offline "agenda": derived facts, no external data needed
        wk = today.isocalendar()[1]; doy = today.timetuple().tm_yday
        yr_days = 366 if calendar.isleap(today.year) else 365
        left = calendar.monthrange(today.year, today.month)[1] - today.day
        info = L("WEEK %02d · DAY %d/%d · %d LEFT IN MONTH" % (wk, doy, yr_days, left), "calinfo")
        info.set_margin_top(4); body.pack_start(info, False, False, 0)
        b.pack_start(body, False, False, 0)
        self.add(b)
        self.setup_collapse(body)

# ---------- LOCAL MODEL (Ollama + iGPU) ----------
class LLMStatus(Widget):
    def __init__(self):
        super().__init__("llm", 690, 300, 420)   # fills the empty centre
        b = vbox(8, m=22); b.pack_start(self.header("LOCAL MODEL"), False, False, 0)
        self.model = L("— idle —", "big"); self.model.set_xalign(0)
        self.model.set_ellipsize(Pango.EllipsizeMode.END); self.model.set_max_width_chars(22)
        b.pack_start(self.model, False, False, 0)
        self.rows = {}
        for k in ("PARAMS", "SIZE", "PROC"):
            row = Gtk.Box(spacing=14)
            key = L(k, "k"); key.set_size_request(78, -1)
            val = L("—", "v"); val.set_xalign(1); val.set_hexpand(True)
            row.pack_start(key, False, False, 0); row.pack_end(val, True, True, 0)
            self.rows[k] = val; b.pack_start(row, False, False, 0)
        grow = Gtk.Box(spacing=14)
        gk = L("iGPU", "k"); gk.set_size_request(78, -1)
        self.gmeter = L("", "meter"); self.gmeter.set_xalign(0)
        self.gval = L("—", "dim"); self.gval.set_xalign(1); self.gval.set_size_request(82, -1)
        grow.pack_start(gk, False, False, 0); grow.pack_start(self.gmeter, False, False, 0)
        grow.pack_end(self.gval, False, False, 0)
        b.pack_start(grow, False, False, 0)
        self.add(b); self.first(self.tick); GLib.timeout_add(2500, self.tick)
    def _api(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=1.5) as r:
                return json.load(r)
        except Exception:
            return None
    def tick(self):
        models = ((self._api() or {}).get("models")) or []
        if models:
            m = models[0]; det = m.get("details", {}) or {}
            self.model.set_text(m.get("name", "?"))
            self.rows["PARAMS"].set_text(("%s · %s" % (det.get("parameter_size", "?"),
                                          det.get("quantization_level", ""))).strip(" ·"))
            size = m.get("size", 0); vram = m.get("size_vram", 0)
            self.rows["SIZE"].set_text("%.1fG" % (size / 1073741824) if size else "—")
            if size and vram:
                g = int(round(vram / size * 100))
                self.rows["PROC"].set_text("GPU %d%% · CPU %d%%" % (g, 100 - g))
            else:
                self.rows["PROC"].set_text("CPU 100%" if size else "—")
        else:
            self.model.set_text("— idle —")
            for v in self.rows.values(): v.set_text("—")
        act = out("cat /sys/class/drm/card1/gt_act_freq_mhz 2>/dev/null")
        mx = (out("cat /sys/class/drm/card1/gt_RP0_freq_mhz 2>/dev/null")
              or out("cat /sys/class/drm/card1/gt_max_freq_mhz 2>/dev/null"))
        if act.isdigit() and mx.isdigit() and int(mx) > 0:
            self.gmeter.set_text(dots(int(act) / int(mx) * 100, 20))
            self.gval.set_text("%s MHz" % act)
        else:
            self.gmeter.set_text(dots(0, 20)); self.gval.set_text("—")
        return True

# ---------- FOCUS TIMER (Pomodoro) ----------
class Pomodoro(Widget):
    WORK = 25 * 60
    def __init__(self):
        super().__init__("pomo", 735, 40, 350)
        self.remaining = self.WORK; self.running = False; self._timer = None
        b = vbox(7, m=22); b.pack_start(self.header("FOCUS"), False, False, 0)
        self.disp = L("25:00", "clock2"); self.disp.set_xalign(0)
        b.pack_start(self.disp, False, False, 0)
        self.bar = Gtk.Label(); self.bar.set_xalign(0)
        self.bar.get_style_context().add_class("sbar")
        b.pack_start(self.bar, False, False, 0)
        ctrl = Gtk.Box(spacing=8)
        self.startbtn = Gtk.Button(label="START")
        self.startbtn.get_style_context().add_class("tile")
        self.startbtn.set_relief(Gtk.ReliefStyle.NONE)
        self.startbtn.set_size_request(-1, 38); self.startbtn.set_hexpand(True)
        self.startbtn.connect("clicked", self._toggle)
        rb = Gtk.Button(label="RESET"); rb.get_style_context().add_class("tile")
        rb.set_relief(Gtk.ReliefStyle.NONE); rb.set_size_request(-1, 38); rb.set_hexpand(True)
        rb.connect("clicked", self._reset)
        ctrl.pack_start(self.startbtn, True, True, 0); ctrl.pack_start(rb, True, True, 0)
        b.pack_start(ctrl, False, False, 0)
        self.add(b); self._render()
        # start counting down on its own once the desktop is up
        GLib.idle_add(self._autostart)

    def _autostart(self):
        if not self.running: self._toggle()
        return False
    def _toggle(self, *_):
        self.running = not self.running
        self.startbtn.set_label("PAUSE" if self.running else "START")
        if self.running and self._timer is None:
            self._timer = GLib.timeout_add(1000, self._sec)
    def _sec(self):
        if not self.running:
            self._timer = None; return False
        if self.remaining > 0: self.remaining -= 1
        if self.remaining <= 0:
            self.running = False; self.startbtn.set_label("START")
            sh("notify-send 'Focus complete' 'Time for a break' 2>/dev/null || true")
        self._render()
        if not self.running: self._timer = None; return False
        return True
    def _reset(self, *_):
        self.running = False; self.remaining = self.WORK
        self.startbtn.set_label("START"); self._render()
    def _render(self):
        m, s = divmod(self.remaining, 60)
        self.disp.set_text("%02d:%02d" % (m, s))
        self.bar.set_markup(two_tone((self.WORK - self.remaining) / self.WORK * 100, 28))

# ---------- CLAUDE SESSIONS (timeline; click to resume) ----------
class Sessions(Widget):
    """Recent `claude` sessions as a timeline. Click a row to reopen that exact
    conversation: a terminal in the session's cwd running `claude --resume <id>`."""
    ROOT     = os.path.expanduser("~/.claude/projects")
    CACHE    = os.path.join(CONF_DIR, "sessions-cache.json")
    ROWS     = 60       # keep them all — the list scrolls, so nothing is dropped
    HEAD_MAX = 400      # lines to scan per file — cwd + first prompt are near the top
    VIEW_H   = 158      # scroll viewport height (Sessions is last in the pane; this
                        # is the room left down to the screen edge — scroll for more)

    def __init__(self):
        super().__init__("sessions", 690, 300, 440)
        self._sig = None; self._open = {}; self._last_items = []
        self._cache = self._load_cache()
        b = vbox(6, m=18)
        b.pack_start(self.header("SESSIONS"), False, False, 0)
        b.pack_start(rule(), False, False, 0)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        # scrollable viewport: every project/run is reachable by scrolling down,
        # while the card itself stays a fixed height so it never shoves the pane.
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_min_content_height(self.VIEW_H)
        self.scroll.set_max_content_height(self.VIEW_H)
        self.scroll.set_propagate_natural_height(False)
        self.scroll.get_style_context().add_class("chat")   # transparent bg
        self.scroll.add(self.list)
        b.pack_start(self.scroll, True, True, 0)
        self.foot = L("— scanning —", "faint"); self.foot.set_xalign(0)
        self.foot.set_margin_top(4)
        b.pack_start(self.foot, False, False, 0)
        self.add(b)
        self.first(self.refresh); GLib.timeout_add(20000, self.refresh)

    # ---- cache: parsing a 20MB transcript on every tick would stall the UI ----
    def _load_cache(self):
        try:
            with open(self.CACHE) as fh: return json.load(fh)
        except Exception: return {}

    def _save_cache(self):
        try:
            with open(self.CACHE, "w") as fh: json.dump(self._cache, fh)
        except Exception: pass

    def _parse(self, path):
        """cwd + first real user prompt. Only the head of the file is read."""
        cwd = None; task = None
        try:
            with open(path, errors="replace") as fh:
                for i, ln in enumerate(fh):
                    if i > self.HEAD_MAX or (cwd and task): break
                    try: o = json.loads(ln)
                    except Exception: continue
                    cwd = cwd or o.get("cwd")
                    if task is None and o.get("type") == "user":
                        c = (o.get("message") or {}).get("content")
                        if isinstance(c, list):
                            c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
                        if isinstance(c, str):
                            c = c.strip()
                            # skip hook/system injections and slash commands
                            if c and not c.startswith(("<", "/")):
                                task = " ".join(c.split())[:120]
        except Exception: pass
        return cwd, task

    def _scan(self):
        items = []
        for path in glob.glob(os.path.join(self.ROOT, "*", "*.jsonl")):
            try: mt = os.path.getmtime(path)
            except OSError: continue
            ent = self._cache.get(path)
            if not ent or ent.get("mt") != mt:
                cwd, task = self._parse(path)
                ent = {"mt": mt, "cwd": cwd, "task": task,
                       "sid": os.path.basename(path)[:-6]}
                self._cache[path] = ent
            items.append(ent)
        items.sort(key=lambda e: e["mt"], reverse=True)
        # drop cache entries for transcripts that no longer exist
        for k in [k for k in self._cache if not os.path.exists(k)]:
            self._cache.pop(k, None)
        self._save_cache()
        # One row per project: many runs of local-jarvis shouldn't crowd out
        # everything else. Newest run represents the group; the rest fold under it.
        groups = {}
        for e in items:
            cwd = e.get("cwd") or os.path.expanduser("~")
            groups.setdefault(cwd, []).append(e)
        out = []
        for cwd, runs in groups.items():
            runs.sort(key=lambda e: e["mt"], reverse=True)
            out.append({"cwd": cwd, "mt": runs[0]["mt"], "runs": runs})
        out.sort(key=lambda g: g["mt"], reverse=True)
        return out[:self.ROWS]

    @staticmethod
    def _label(cwd):
        # Name each row by WHERE claude ran, readably: "HOME" instead of a bare
        # "~", and the last two path parts (parent/dir) so nested projects don't
        # collapse to an ambiguous single word.
        home = os.path.expanduser("~")
        cwd = cwd.rstrip("/")
        if cwd == home: return "HOME"
        rel = cwd[len(home) + 1:] if cwd.startswith(home + "/") else cwd.lstrip("/")
        parts = [p for p in rel.split("/") if p]
        if not parts: return "HOME"
        return "/".join(parts[-2:])

    def refresh(self):
        threading.Thread(target=self._scan_bg, daemon=True).start()
        return True

    def _scan_bg(self):
        try: items = self._scan()
        except Exception: items = []
        GLib.idle_add(self._render, items)

    def _render(self, items):
        sig = (tuple((g["cwd"], g["mt"], len(g["runs"])) for g in items),
               tuple(sorted(k for k, v in self._open.items() if v)))
        if sig == self._sig: return False      # nothing changed → no rebuild, no flicker
        self._sig = sig; self._last_items = items
        for c in self.list.get_children(): self.list.remove(c)
        if not items:
            self.foot.set_text("no sessions yet"); self.list.show_all()
            schedule_relayout(); return False
        now = time.time()
        total = sum(len(g["runs"]) for g in items)
        for i, g in enumerate(items):
            self.list.pack_start(self._group_row(g, i == 0, now), False, False, 0)
            if self._open.get(g["cwd"]):       # unfolded → older runs underneath
                for e in g["runs"][1:]:
                    self.list.pack_start(self._child_row(e, now), False, False, 0)
        self.foot.set_text("%d projects · %d runs · latest %s"
                           % (len(items), total, self._ago(now - items[0]["mt"])))
        self.list.show_all()
        schedule_relayout()                    # height changed → restack the pane
        return False

    @staticmethod
    def _ago(sec):
        sec = max(0, int(sec))
        if sec < 60:    return "just now"
        if sec < 3600:  return "%dm ago" % (sec // 60)
        if sec < 86400: return "%dh ago" % (sec // 3600)
        return "%dd ago" % (sec // 86400)

    def _group_row(self, g, first, now):
        """One project. Clicking resumes its newest run; the count unfolds the rest."""
        cwd, runs = g["cwd"], g["runs"]
        e = runs[0]; name = self._label(cwd)
        row = Gtk.Box(spacing=0)

        btn = Gtk.Button(); btn.get_style_context().add_class("srow")
        btn.set_relief(Gtk.ReliefStyle.NONE); btn.set_hexpand(True)
        line = Gtk.Box(spacing=8)
        rail = L("●" if first else "│", "srail live" if first else "srail")
        rail.set_valign(Gtk.Align.START)
        line.pack_start(rail, False, False, 0)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        top = Gtk.Box(spacing=8)
        d = L(name.upper()[:26], "sdir"); d.set_xalign(0); d.set_hexpand(True)
        d.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        ago = L(self._ago(now - e["mt"]), "stime"); ago.set_xalign(1)
        top.pack_start(d, True, True, 0); top.pack_end(ago, False, False, 0)
        col.pack_start(top, False, False, 0)
        tl = L(e.get("task") or "—", "stask"); tl.set_xalign(0)
        tl.set_ellipsize(Pango.EllipsizeMode.END); tl.set_max_width_chars(40)
        col.pack_start(tl, False, False, 0)
        line.pack_start(col, True, True, 0)
        btn.add(line)
        btn.set_tooltip_text("%s\n%s\nclaude --resume %s" % (cwd, e.get("task") or "", e["sid"]))
        btn.connect("clicked", lambda *_: self._resume(cwd, e["sid"]))
        btn.connect("button-press-event", self._menu, cwd, e["sid"], name)
        row.pack_start(btn, True, True, 0)

        if len(runs) > 1:
            opened = self._open.get(cwd, False)
            tog = Gtk.Button(label="%d %s" % (len(runs), "⌄" if opened else "›"))
            tog.get_style_context().add_class("scount")
            tog.set_relief(Gtk.ReliefStyle.NONE); tog.set_valign(Gtk.Align.CENTER)
            tog.set_tooltip_text("%d runs in %s" % (len(runs), name))
            tog.connect("clicked", self._toggle_group, cwd)
            row.pack_end(tog, False, False, 0)
        return row

    def _child_row(self, e, now):
        """An older run inside an unfolded project."""
        cwd = e.get("cwd") or os.path.expanduser("~")
        btn = Gtk.Button(); btn.get_style_context().add_class("srow child")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        line = Gtk.Box(spacing=8)
        line.pack_start(L("╰", "srail"), False, False, 0)
        t = L(time.strftime("%d %b %H:%M", time.localtime(e["mt"])), "stime")
        t.set_xalign(0); t.set_size_request(78, -1)
        line.pack_start(t, False, False, 0)
        tl = L(e.get("task") or "—", "stask"); tl.set_xalign(0); tl.set_hexpand(True)
        tl.set_ellipsize(Pango.EllipsizeMode.END); tl.set_max_width_chars(30)
        line.pack_start(tl, True, True, 0)
        btn.add(line)
        btn.set_tooltip_text("%s\n%s\nclaude --resume %s" % (cwd, e.get("task") or "", e["sid"]))
        btn.connect("clicked", lambda *_: self._resume(cwd, e["sid"]))
        btn.connect("button-press-event", self._menu, cwd, e["sid"], self._label(cwd))
        return btn

    def _toggle_group(self, btn, cwd):
        self._open[cwd] = not self._open.get(cwd, False)
        self._sig = None                 # force a rebuild
        self._render(self._last_items)
        return True

    def _resume(self, cwd, sid):
        sh("gnome-terminal --working-directory=%s -- claude --resume %s"
           % (shlex.quote(cwd), shlex.quote(sid)))

    def _menu(self, btn, ev, cwd, sid, name):
        if ev.button != 3: return False
        m = Gtk.Menu()
        hdr = Gtk.MenuItem(label=name); hdr.set_sensitive(False)
        m.append(hdr); m.append(Gtk.SeparatorMenuItem())
        q = shlex.quote(cwd)
        for text, cmd in (
                ("Resume this session", "gnome-terminal --working-directory=%s -- claude --resume %s" % (q, shlex.quote(sid))),
                ("Continue latest here", "gnome-terminal --working-directory=%s -- claude -c" % q),
                ("Open terminal here",   "gnome-terminal --working-directory=%s" % q),
                ("Open in Files",        "nemo %s" % q)):
            mi = Gtk.MenuItem(label=text)
            mi.connect("activate", lambda w, c=cmd: sh(c)); m.append(mi)
        m.show_all(); m.popup_at_pointer(ev)
        return True

# ---------- NOTES (editable, autosaved) ----------
class Notes(Widget):
    FILE = os.path.join(CONF_DIR, "notes.txt")
    def __init__(self):
        super().__init__("notes", 1130, 762, 710, focusable=True)
        b = vbox(7, m=22); b.pack_start(self.header("NOTES", collapsible=True), False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.buf = Gtk.TextBuffer()
        try: self.buf.set_text(open(self.FILE).read())
        except Exception: pass
        tv = Gtk.TextView.new_with_buffer(self.buf)
        tv.get_style_context().add_class("notes")
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)  # vertical scroll
        sc.set_size_request(-1, 230); sc.get_style_context().add_class("notes")
        sc.add(tv)
        body.pack_start(sc, True, True, 0)
        b.pack_start(body, True, True, 0)
        self.add(b)
        self.buf.connect("changed", self._save)
        self.setup_collapse(body)
    def _save(self, *_):
        s, e = self.buf.get_bounds()
        try: open(self.FILE, "w").write(self.buf.get_text(s, e, True))
        except Exception: pass

# ---------- AI ASSISTANT (local Ollama, streaming) ----------
class Assistant(Widget):
    HOST = "http://127.0.0.1:11434"
    # fast + capable general model; system facts are injected so it "knows" this machine.
    MODELS = ["qwen2.5-coder:3b", "qwen2.5-coder:1.5b", "qwen3:4b", "jarvis-coder:latest"]
    SYS = ("You are Jarvis, a concise assistant living on Arjun's Linux Mint desktop. "
           "Answer briefly and practically — short paragraphs, code blocks when useful. "
           "A LIVE SYSTEM snapshot of this exact computer is provided below; when asked "
           "about the machine (battery, cpu, ram, disk, network, time, uptime), answer "
           "from it directly and confidently. Don't say you lack access — you have the snapshot.")

    def __init__(self):
        super().__init__("assistant", 690, 300, 440, focusable=True)
        self.busy = False; self.history = []; self.model = self.MODELS[0]
        b = vbox(7, m=18)
        head = Gtk.Box(spacing=6)
        self.titlelbl = L("JARVIS", "title"); self.titlelbl.set_hexpand(True); self.titlelbl.set_xalign(0)
        self.mbtn = Gtk.Button(label="qwen2.5-coder:3b"); self.mbtn.get_style_context().add_class("chatq")
        self.mbtn.set_relief(Gtk.ReliefStyle.NONE); self.mbtn.connect("clicked", self._cycle_model)
        clr = Gtk.Button(label="CLEAR"); clr.get_style_context().add_class("media")
        clr.set_relief(Gtk.ReliefStyle.NONE); clr.connect("clicked", self._clear)
        shut = Gtk.Button(label="✕"); shut.get_style_context().add_class("media")
        shut.set_relief(Gtk.ReliefStyle.NONE)
        shut.connect("clicked", lambda *_: self.hide_popup())
        dot = L("●", "hdot"); HDOTS.append(dot)
        head.pack_start(self.titlelbl, True, True, 0)
        head.pack_end(dot, False, False, 0); head.pack_end(shut, False, False, 0)
        head.pack_end(clr, False, False, 0)
        head.pack_end(self.mbtn, False, False, 0)
        b.pack_start(head, False, False, 0)
        # compact live model-status line (folded in from the old Local Model card)
        self.status = L("— checking model —", "chatsys"); self.status.set_xalign(0)
        b.pack_start(self.status, False, False, 0)
        b.pack_start(rule(), False, False, 0)
        self.first(self._poll_status); GLib.timeout_add(3000, self._poll_status)
        # transcript
        self.buf = Gtk.TextBuffer()
        self.tag_q  = self.buf.create_tag("q", foreground=ACCENT, weight=700)
        self.tag_a  = self.buf.create_tag("a", foreground="#cfcfcf")
        self.tag_sys= self.buf.create_tag("sys", foreground="#6a6a6a", style=1)
        self.tv = Gtk.TextView.new_with_buffer(self.buf)
        self.tv.get_style_context().add_class("chat")
        self.tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR); self.tv.set_editable(False)
        self.tv.set_cursor_visible(False)
        self.sc = Gtk.ScrolledWindow(); self.sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sc.set_size_request(-1, 230); self.sc.add(self.tv)
        b.pack_start(self.sc, True, True, 0)
        self._append("sys", "Ask me anything — runs on your local model, fully offline.\n")
        # input row
        row = Gtk.Box(spacing=8)
        self.entry = Gtk.Entry(); self.entry.get_style_context().add_class("chatin")
        self.entry.set_placeholder_text("Message Jarvis…"); self.entry.set_hexpand(True)
        self.entry.connect("activate", self._send)
        send = Gtk.Button(label="SEND"); send.get_style_context().add_class("sendbtn")
        send.set_relief(Gtk.ReliefStyle.NONE); send.connect("clicked", self._send)
        row.pack_start(self.entry, True, True, 0); row.pack_end(send, False, False, 0)
        b.pack_start(row, False, False, 0)
        self.add(b)

    # ---- popup control (driven by the floating orb) ----
    def toggle_popup(self, orb):
        if self.get_visible(): self.hide_popup()
        else: self.show_popup(orb)

    def show_popup(self, orb):
        # sit just above the orb, clamped to the screen
        ox, oy = orb.get_position()
        h = self.get_preferred_height()[1] or 380
        geo = (Gdk.Display.get_default().get_primary_monitor()
               or Gdk.Display.get_default().get_monitor(0)).get_geometry()
        self.move(ox, max(geo.y + 20, oy - h - 12))
        self.show_all()
        # Float over the other CARDS, not over the user's windows: stay in the
        # keep_below layer and just restack within it. keep_above would put the
        # chat in META_LAYER_TOP, covering whatever the user is working in.
        # present() only raises/activates — it does not change the layer.
        self.present()
        GLib.idle_add(self.entry.grab_focus)

    def hide_popup(self):
        self.hide()

    def _append(self, tag, text):
        end = self.buf.get_end_iter()
        self.buf.insert_with_tags(end, text, {"q": self.tag_q, "a": self.tag_a, "sys": self.tag_sys}[tag])
        GLib.idle_add(self._scroll_bottom)

    def _scroll_bottom(self):
        adj = self.sc.get_vadjustment()
        if adj: adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def _clear(self, *_):
        self.buf.set_text(""); self.history = []
        self._append("sys", "Cleared.\n")

    def _send(self, *_):
        if self.busy: return
        q = self.entry.get_text().strip()
        if not q: return
        self.entry.set_text(""); self.busy = True
        self._append("q", "\n› " + q + "\n"); self._append("a", "")
        threading.Thread(target=self._run, args=(q,), daemon=True).start()

    def _run(self, q):
        # build a chat-style prompt: persona + live system snapshot + short history
        msgs = [{"role": "system", "content": self.SYS + "\n\n" + self._system_context()}]
        for u, a in self.history[-4:]:
            msgs.append({"role": "user", "content": u}); msgs.append({"role": "assistant", "content": a})
        msgs.append({"role": "user", "content": q})
        payload = json.dumps({"model": self.model, "messages": msgs, "stream": True,
                              "think": False}).encode()
        req = urllib.request.Request(self.HOST + "/api/chat", data=payload,
                                     headers={"Content-Type": "application/json"})
        acc = []
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                for line in r:
                    line = line.strip()
                    if not line: continue
                    try: obj = json.loads(line)
                    except Exception: continue
                    chunk = (obj.get("message") or {}).get("content", "")
                    if chunk:
                        acc.append(chunk)
                        GLib.idle_add(self._append, "a", chunk)
                    if obj.get("done"): break
        except Exception as e:
            GLib.idle_add(self._append, "sys", "\n[offline or model busy: %s]\n" % str(e)[:60])
        ans = "".join(acc).strip()
        if ans: self.history.append((q, ans))
        GLib.idle_add(self._append, "a", "\n")
        GLib.idle_add(self._done)

    def _done(self):
        self.busy = False; return False

    def _poll_status(self):
        try:
            with urllib.request.urlopen(self.HOST + "/api/ps", timeout=1.5) as r:
                models = (json.load(r) or {}).get("models") or []
        except Exception:
            self.status.set_text("⚠ ollama offline"); return True
        if models:
            m = models[0]; det = m.get("details", {}) or {}
            size = m.get("size", 0); vram = m.get("size_vram", 0)
            proc = "GPU" if (size and vram and vram >= size) else ("GPU/CPU" if vram else "CPU")
            self.status.set_text("● %s · %s · %s · %.1fG"
                % (m.get("name", "?"), det.get("parameter_size", "?"), proc, size / 1073741824))
        else:
            self.status.set_text("○ idle · model unloaded")
        return True

    def _cycle_model(self, *_):
        i = (self.MODELS.index(self.model) + 1) % len(self.MODELS)
        self.model = self.MODELS[i]; self.mbtn.set_label(self.model)
        self._append("sys", "\n[model → %s]\n" % self.model)

    def _system_context(self):
        # live snapshot of THIS machine, injected so the model can answer factually
        def rd(p, d=""):
            try: return open(p).read().strip()
            except Exception: return d
        # cpu %
        try:
            v1 = list(map(int, open("/proc/stat").readline().split()[1:]))
            time.sleep(0.05)
            v2 = list(map(int, open("/proc/stat").readline().split()[1:]))
            dt = sum(v2) - sum(v1); di = (v2[3] + v2[4]) - (v1[3] + v1[4])
            cpu = int((1 - di / dt) * 100) if dt > 0 else 0
        except Exception: cpu = 0
        mi = {}
        for ln in open("/proc/meminfo"):
            k, _, r = ln.partition(":");
            if r: mi[k] = int(r.split()[0])
        ram_used = (mi.get("MemTotal", 0) - mi.get("MemAvailable", 0)) // 1024
        ram_tot = mi.get("MemTotal", 0) // 1024
        bat = rd("/sys/class/power_supply/BAT0/capacity", "?")
        chg = "charging" if any(rd(p) == "1" for p in glob.glob("/sys/class/power_supply/A*/online")) else "on battery"
        temp = max([int(rd(p, "0")) for p in glob.glob("/sys/class/thermal/thermal_zone*/temp")] or [0]) // 1000
        up = out("uptime -p | sed 's/^up //'") or "?"
        load = rd("/proc/loadavg", "?").split(" ")[0]
        disk = out("df -h / | awk 'NR==2{print $4\" free of \"$2}'")
        ssid = out("iwgetid -r 2>/dev/null || /usr/sbin/iwgetid -r 2>/dev/null") or "not connected"
        ip = out("hostname -I 2>/dev/null | awk '{print $1}'") or "?"
        host = rd("/proc/sys/kernel/hostname", "?")
        distro = out(". /etc/os-release 2>/dev/null; echo $PRETTY_NAME") or "Linux"
        now = time.strftime("%A %d %B %Y, %H:%M")
        return ("LIVE SYSTEM SNAPSHOT (this computer, right now):\n"
                "- Host: %s · %s (Cinnamon/X11)\n- CPU: Intel i5-1135G7, load now ~%d%%, 1min load %s, temp %d°C\n"
                "- RAM: %d MB used of %d MB\n- Battery: %s%% (%s)\n- Disk /: %s\n"
                "- Uptime: %s\n- Network: Wi-Fi '%s', local IP %s\n- Date/time: %s"
                % (host, distro, cpu, load, temp, ram_used, ram_tot, bat, chg, disk, up, ssid, ip, now))

# ---------- PALETTE (live theme switcher) ----------
class Palette(Widget):
    def __init__(self):
        super().__init__("palette", 74, 300, 300)
        b = vbox(7, m=18); b.pack_start(self.header("PALETTE"), False, False, 0)
        self.name = L(THEME_NAME, "swname"); self.name.set_xalign(0)
        b.pack_start(self.name, False, False, 0)
        grid = Gtk.Box(spacing=6)
        self.sw = {}
        for n in THEME_NAMES:
            t = THEMES[n]
            btn = Gtk.Button(); btn.get_style_context().add_class("swatch")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_size_request(46, 34); btn.set_tooltip_text(n)
            da = Gtk.DrawingArea()
            da.connect("draw", self._paint, t)
            btn.add(da)
            btn.connect("clicked", lambda w, nn=n: self._pick(nn))
            self.sw[n] = btn; grid.pack_start(btn, True, True, 0)
        b.pack_start(grid, False, False, 0)
        self.add(b)
        self._mark()

    def _paint(self, da, cr, t):
        w = da.get_allocated_width(); h = da.get_allocated_height()
        cr.set_source_rgb(*rgb(t["tile"])); cr.rectangle(0, 0, w, h); cr.fill()
        cr.set_source_rgb(*rgb(t["accent"])); cr.rectangle(0, 0, w, h * 0.42); cr.fill()
        cr.set_source_rgb(*rgb(t["text"]))
        cr.rectangle(3, h * 0.60, w * 0.55, 2); cr.fill()
        cr.set_source_rgb(*rgb(t["dim"]))
        cr.rectangle(3, h * 0.76, w * 0.34, 2); cr.fill()
        return False

    def _pick(self, name):
        set_theme(name); self.name.set_text(name); self._mark()

    def _mark(self):
        for n, btn in self.sw.items():
            ctx = btn.get_style_context()
            (ctx.add_class if n == THEME_NAME else ctx.remove_class)("on")

# ---------- RAIL (slim strip; expands the cards that aren't in the pane) ----------
class Rail(Widget):
    """A thin vertical launcher on the left edge. Each icon shows/hides its card
    with a short slide+fade, so the extra widgets stay one click away."""
    ICONS = {"controls": "◉", "pomo": "◔", "calendar": "▦",
             "notes": "✎", "arcade": "◈", "palette": "◐"}
    LABELS = {"controls": "CTRL", "pomo": "FOCUS", "calendar": "CAL",
              "notes": "NOTE", "arcade": "PLAY", "palette": "SKIN"}

    def __init__(self, targets):
        super().__init__("rail", 8, 300)
        self.targets = targets            # wname -> Widget (hidden pop-outs)
        self.btns = {}; self.tags = {}
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        col.get_style_context().add_class("railbar")
        for n in list(self.ICONS):
            if n not in targets: continue
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            btn = Gtk.Button(label=self.ICONS[n])
            btn.get_style_context().add_class("railbtn")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_tooltip_text(self.LABELS[n])
            btn.connect("clicked", lambda w, nn=n: self.toggle(nn))
            cell.pack_start(btn, False, False, 0)
            tag = L(self.LABELS[n], "railtag", 0.5)
            cell.pack_start(tag, False, False, 0)
            self.btns[n] = btn
            self.tags[n] = tag
            col.pack_start(cell, False, False, 0)
        self.add(col)
        GLib.idle_add(self._centre)
        GLib.timeout_add(1000, self._tick)

    def _tick(self):
        # the Focus timer auto-starts at login and lives behind an icon, so surface
        # its countdown on the rail instead of making you open it to check.
        p = self.targets.get("pomo")
        tag = self.tags.get("pomo")
        if p is not None and tag is not None:
            if getattr(p, "running", False):
                m, s = divmod(p.remaining, 60)
                tag.set_text("%d:%02d" % (m, s))
                self.btns["pomo"].get_style_context().add_class("on")
            else:
                tag.set_text(self.LABELS["pomo"])
                if not p.get_visible():
                    self.btns["pomo"].get_style_context().remove_class("on")
        return True

    def add(self, widget):     # the rail draws its own pill, not a .card
        self._cardbox = widget
        Gtk.Window.add(self, widget)

    def _centre(self):
        geo = (Gdk.Display.get_default().get_primary_monitor()
               or Gdk.Display.get_default().get_monitor(0)).get_geometry()
        h = self.get_preferred_height()[1]
        self.move(8, geo.y + max(0, (geo.height - h) // 2))
        return False

    def toggle(self, name):
        w = self.targets.get(name)
        if w is None: return
        if w.get_visible():
            self._slide_out(w, name); return
        # only one pop-out at a time — close whatever else is open first
        for other, ow in self.targets.items():
            if other != name and ow.get_visible():
                self._slide_out(ow, other)
        self._slide_in(w, name)

    def _anchor(self, w):
        """Open in the empty space to the RIGHT of the pane: never covers a pane
        card, and it balances a desktop that otherwise sits entirely on the left."""
        h = w.get_preferred_height()[1] or 200
        geo = (Gdk.Display.get_default().get_primary_monitor()
               or Gdk.Display.get_default().get_monitor(0)).get_geometry()
        x = PANE_X + PANE_W + 26
        y = geo.y + max(12, (geo.height - h) // 2)      # vertically centred
        return x, y

    def _raise(self, w):
        # A pop-out must clear the pane cards — but only them. Restack INSIDE the
        # keep_below layer: raising is honoured within a layer, so the pop-out
        # lands on top of the other widgets while the user's windows stay on top
        # of everything. keep_above here used to lift the pop-out into
        # META_LAYER_TOP and (via group promotion) the whole desktop with it.
        gw = w.get_window()
        if gw is not None: gw.raise_()

    def _slide_in(self, w, name):
        x, y = self._anchor(w)
        w.set_opacity(0.0)
        w.move(x - 22, y)
        w.show_all()
        self._raise(w)
        self.btns[name].get_style_context().add_class("on")
        self._animate(w, x - 22, x, y, 0.0, 1.0)

    def _slide_out(self, w, name):
        x, y = w.get_position()
        self.btns[name].get_style_context().remove_class("on")
        self._animate(w, x, x - 22, y, 1.0, 0.0, hide=w)

    def _animate(self, w, x0, x1, y, o0, o1, hide=None, steps=10):
        state = {"i": 0}
        def step():
            state["i"] += 1
            f = state["i"] / steps
            e = 1 - (1 - f) ** 3          # ease-out
            w.move(int(x0 + (x1 - x0) * e), y)
            w.set_opacity(o0 + (o1 - o0) * e)
            if state["i"] >= steps:
                if hide is not None: hide.hide(); hide.set_opacity(1.0)
                return False
            return True
        GLib.timeout_add(16, step)

# ---------- JARVIS ORB (floating launcher for the Assistant) ----------
class AssistantOrb(Widget):
    """Small breathing puck in the bottom-left corner. Click to pop the chat
    open above it; click again (or CLOSE in the chat) to tuck it away."""
    SIZE = 52

    def __init__(self, assistant):
        super().__init__("orb", 50, 1000)
        self.assistant = assistant
        self._t0 = time.time()
        btn = Gtk.Button(); btn.get_style_context().add_class("orb")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_size_request(self.SIZE, self.SIZE)
        self.icon = L("◕", "orbicon", 0.5)
        btn.add(self.icon)
        btn.connect("clicked", lambda *_: self.assistant.toggle_popup(self))
        self.btn = btn
        self.add(btn)
        GLib.timeout_add(70, self._breathe)

    def add(self, widget):   # no .card class here — the orb styles itself
        self._cardbox = widget
        Gtk.Window.add(self, widget)

    def _breathe(self):
        # idle: slow pulse. thinking: faster, with a spinning glyph.
        busy = getattr(self.assistant, "busy", False)
        speed = 6.0 if busy else 1.8
        a = 0.55 + 0.45 * (0.5 + 0.5 * math.sin((time.time() - self._t0) * speed))
        self.icon.set_opacity(a)
        if busy:
            self.icon.set_text("◐◓◑◒"[int((time.time() - self._t0) * 7) % 4])
        else:
            self.icon.set_text("◕" if self.assistant.get_visible() else "◔")
        return True

# ---------- ARCADE (switchable mini-games) ----------
class Arcade(Widget):
    GAMES = ["DASH", "HOOPS", "SNAKE", "REFLEX", "RAIN"]
    HINTS = {"DASH":"space / click to jump (double-jump!)", "HOOPS":"drag from the ball → release to shoot",
             "SNAKE":"click, then arrows / WASD",
             "REFLEX":"click when it turns red", "RAIN":"ambient · dot rain"}
    HOOPS_K = 9.0              # drag → launch-velocity gain
    HOOPS_G = 1300.0           # gravity px/s²
    W, H = 404, 236
    COLS, ROWS, CELL = 25, 14, 16
    @property
    def R(self): return rgb(ACCENT)   # accent, follows the live theme
    SNAKE_STEP = 0.11           # seconds per snake cell (frame-rate independent)
    FPS_MS = 22                 # ~45 fps timer; physics is dt-scaled so speed is constant

    def __init__(self):
        super().__init__("arcade", 690, 610, 440, focusable=True)
        self.gi = 0; self._last_t = time.monotonic(); self.sn_acc = 0.0
        b = vbox(7, m=18)
        head = Gtk.Box(spacing=6)
        self._caret = Gtk.Button(label="▾"); self._caret.get_style_context().add_class("caret")
        self._caret.set_relief(Gtk.ReliefStyle.NONE)
        self._caret.connect("clicked", lambda *_: self.toggle_collapse())
        head.pack_start(self._caret, False, False, 0)
        self.titlelbl = L("ARCADE · DASH", "title"); self.titlelbl.set_hexpand(True); self.titlelbl.set_xalign(0)
        prev = Gtk.Button(label="‹"); nxt = Gtk.Button(label="›")
        for btn, d in ((prev, -1), (nxt, 1)):
            btn.get_style_context().add_class("media"); btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.connect("clicked", lambda w, dd=d: self.switch(dd))
        dot = L("●", "hdot"); HDOTS.append(dot)
        head.pack_start(self.titlelbl, True, True, 0)
        head.pack_end(dot, False, False, 0); head.pack_end(nxt, False, False, 0); head.pack_end(prev, False, False, 0)
        b.pack_start(head, False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.da = Gtk.DrawingArea(); self.da.set_size_request(self.W, self.H); self.da.set_can_focus(True)
        self.da.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK
                           | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.BUTTON1_MOTION_MASK)
        self.da.connect("draw", self._draw)
        self.da.connect("button-press-event", self._click)
        self.da.connect("button-release-event", self._da_release)
        self.da.connect("motion-notify-event", self._da_motion)
        self.da.connect("key-press-event", self._key)
        body.pack_start(self.da, False, False, 0)
        self.hint = L(self.HINTS["DASH"], "faint"); self.hint.set_xalign(0)
        body.pack_start(self.hint, False, False, 0)
        b.pack_start(body, False, False, 0)
        self.add(b)
        for n in self.GAMES: self._reset_game(n)
        GLib.timeout_add(self.FPS_MS, self._loop)
        self.setup_collapse(body)

    def on_collapse(self, collapsed):
        # a hidden game must not keep burning ~45 redraws/second
        if not collapsed: self._last_t = time.monotonic()

    def switch(self, d):
        self.gi = (self.gi + d) % len(self.GAMES)
        name = self.GAMES[self.gi]
        self.titlelbl.set_text("ARCADE · " + name); self.hint.set_text(self.HINTS[name])
        self._reset_game(name); self._last_t = time.monotonic()
        self.da.grab_focus(); self.da.queue_draw()

    def _reset_game(self, name):
        if name == "DASH":
            # per-second units: spd px/s, gravity px/s², jump px/s, gap px until next obstacle
            self.dash = dict(py=self.H - 24, vy=0, onground=True, obs=[],
                             spd=165.0, dist=0.0, alive=True, gap=140.0, jumps=0)
        elif name == "SNAKE":
            self.sn = dict(body=[(5, 8), (4, 8), (3, 8)], dir=(1, 0), ndir=(1, 0), alive=True, food=(12, 8), score=0)
            self.sn_acc = 0.0; self._snake_food()
        elif name == "REFLEX":
            self.rf = dict(state="idle", t0=0.0, best=None, last=None, timer=None)
        elif name == "RAIN":
            # each drop: [y, speed px/s]
            self.rain = [[random.uniform(-self.H, 0), 120 + (i % 3) * 55] for i in range(self.W // 12)]
        elif name == "HOOPS":
            self.hoops = dict(bx=80.0, by=150.0, vx=0.0, vy=0.0, r=11,
                              aiming=False, ax=0.0, ay=0.0, flying=False, scored=False,
                              score=0, streak=0, hx=self.W - 68.0, hy=74.0, hw=44)

    # ---- input ----
    def _click(self, da, e):
        da.grab_focus(); name = self.GAMES[self.gi]
        if name == "DASH": self._dash_jump()
        elif name == "REFLEX": self._reflex_click()
        elif name == "HOOPS": self._hoops_press(e)
        return True

    def _da_motion(self, da, e):
        if self.GAMES[self.gi] == "HOOPS": self._hoops_motion(e)
        return False

    def _da_release(self, da, e):
        if self.GAMES[self.gi] == "HOOPS": self._hoops_release(e); return True
        return False

    def _key(self, da, e):
        name = self.GAMES[self.gi]; k = e.keyval
        if name == "DASH" and k in (Gdk.KEY_space, Gdk.KEY_Up, Gdk.KEY_w):
            self._dash_jump(); return True
        if name == "SNAKE":
            nd = {Gdk.KEY_Up:(0,-1), Gdk.KEY_w:(0,-1), Gdk.KEY_Down:(0,1), Gdk.KEY_s:(0,1),
                  Gdk.KEY_Left:(-1,0), Gdk.KEY_a:(-1,0), Gdk.KEY_Right:(1,0), Gdk.KEY_d:(1,0)}.get(k)
            if nd:
                dx, dy = self.sn["dir"]
                if nd != (-dx, -dy): self.sn["ndir"] = nd
                return True
        return False

    def _loop(self):
        if self._collapsed or not self.get_visible():
            self._last_t = time.monotonic()   # don't bank up dt while hidden/folded
            return True
        now = time.monotonic()
        dt = now - self._last_t; self._last_t = now
        if dt > 0.1: dt = 0.1          # clamp after stalls so nothing teleports
        try:
            name = self.GAMES[self.gi]; draw = False
            if name == "DASH":
                if self.dash["alive"]: self._dash_tick(dt); draw = True
            elif name == "SNAKE":
                if self.sn["alive"]:
                    self.sn_acc += dt
                    while self.sn_acc >= self.SNAKE_STEP and self.sn["alive"]:
                        self.sn_acc -= self.SNAKE_STEP; self._snake_tick()
                    draw = True
            elif name == "RAIN":
                self._rain_tick(dt); draw = True
            elif name == "HOOPS":
                if self.hoops["flying"]: self._hoops_tick(dt); draw = True
            # REFLEX is static → it redraws only on state change, not every frame
            if draw: self.da.queue_draw()
        except Exception: pass
        return True

    # ---- DASH ----
    def _dash_jump(self):
        d = self.dash
        if not d["alive"]:
            self._reset_game("DASH"); self._last_t = time.monotonic(); self.da.queue_draw(); return
        if d["jumps"] < 2:            # ground jump + one air (double) jump
            d["vy"] = -560.0; d["onground"] = False; d["jumps"] += 1
    def _dash_tick(self, dt):
        d = self.dash; g = self.H - 24
        d["spd"] += 7.0 * dt                       # gradual speed ramp
        d["dist"] += d["spd"] * dt
        d["vy"] += 1600.0 * dt                      # gravity
        d["py"] += d["vy"] * dt
        if d["py"] >= g: d["py"] = g; d["vy"] = 0; d["onground"] = True; d["jumps"] = 0
        d["gap"] -= d["spd"] * dt
        if d["gap"] <= 0:
            d["obs"].append([self.W + 10, 14 + random.random() * 20])
            d["gap"] = 150 + random.random() * 170
        for o in d["obs"]: o[0] -= d["spd"] * dt
        d["obs"] = [o for o in d["obs"] if o[0] > -20]
        for o in d["obs"]:
            if o[0] < 32 and o[0] + 10 > 18 and d["py"] > g - o[1]: d["alive"] = False
    def _draw_dash(self, cr):
        d = self.dash; g = self.H - 24
        cr.set_source_rgba(1, 1, 1, 0.06); gx = 0
        while gx < self.W:
            cr.arc(gx + (d["dist"] % 16), g + 14, 1, 0, 6.2832); cr.fill(); gx += 16
        cr.set_source_rgba(1, 1, 1, 0.18); cr.set_line_width(1); cr.move_to(0, g + 8); cr.line_to(self.W, g + 8); cr.stroke()
        cr.set_source_rgba(0.92, 0.92, 0.92, 1) if d["alive"] else cr.set_source_rgba(*self.R, 1)
        cr.rectangle(18, d["py"] - 14, 14, 14); cr.fill()
        cr.set_source_rgba(*self.R, 1)
        for o in d["obs"]: cr.rectangle(o[0], g - o[1], 10, o[1] + 14); cr.fill()
        self._text(cr, "%dm" % (d["dist"] / 10), self.W - 8, 18, 13, (0.55, 0.55, 0.6), 1)
        if not d["alive"]: self._text(cr, "space / click to retry", self.W / 2, 26, 12, (0.85, 0.85, 0.85), 0.5)

    # ---- SNAKE ----
    def _snake_food(self):
        body = set(self.sn["body"])
        while True:
            f = (random.randrange(self.COLS), random.randrange(self.ROWS))
            if f not in body: self.sn["food"] = f; return
    def _snake_tick(self):
        s = self.sn; s["dir"] = s["ndir"]; hx, hy = s["body"][0]
        nx, ny = hx + s["dir"][0], hy + s["dir"][1]
        if nx < 0 or ny < 0 or nx >= self.COLS or ny >= self.ROWS or (nx, ny) in s["body"]:
            s["alive"] = False
            def _revive(): self._reset_game("SNAKE"); self.da.queue_draw(); return False
            GLib.timeout_add(700, _revive); return
        s["body"].insert(0, (nx, ny))
        if (nx, ny) == s["food"]: s["score"] += 1; self._snake_food()
        else: s["body"].pop()
    def _draw_snake(self, cr):
        s = self.sn; C = self.CELL
        ox = (self.W - self.COLS * C) / 2; oy = (self.H - self.ROWS * C) / 2
        cr.set_source_rgba(1, 1, 1, 0.05)
        for i in range(self.COLS):
            for j in range(self.ROWS):
                cr.arc(ox + i * C + C / 2, oy + j * C + C / 2, 1, 0, 6.2832); cr.fill()
        fx, fy = s["food"]; cr.set_source_rgba(*self.R, 1)
        cr.arc(ox + fx * C + C / 2, oy + fy * C + C / 2, 4.6, 0, 6.2832); cr.fill()
        for i, (bx, by) in enumerate(s["body"]):
            cr.set_source_rgba(1, 1, 1, 1) if i == 0 else cr.set_source_rgba(0.92, 0.92, 0.92, max(0.3, 0.9 - i * 0.03))
            cr.arc(ox + bx * C + C / 2, oy + by * C + C / 2, 5.6 if i == 0 else 4.6, 0, 6.2832); cr.fill()
        if not s["alive"]: self._text(cr, "CRASH", self.W / 2, self.H / 2, 22, self.R, 0.5, True)
        self._text(cr, "%d" % s["score"], self.W - 8, 16, 13, (0.55, 0.55, 0.6), 1)

    # ---- REFLEX ----
    def _reflex_click(self):
        r = self.rf; st = r["state"]
        if st in ("idle", "result", "soon"):
            r["state"] = "wait"
            def go():
                r["state"] = "go"; r["t0"] = time.time(); r["timer"] = None
                self.da.queue_draw(); return False
            r["timer"] = GLib.timeout_add(int(900 + random.random() * 2300), go)
        elif st == "wait":
            if r["timer"]: GLib.source_remove(r["timer"]); r["timer"] = None
            r["state"] = "soon"
        elif st == "go":
            r["last"] = int((time.time() - r["t0"]) * 1000)
            if r["best"] is None or r["last"] < r["best"]: r["best"] = r["last"]
            r["state"] = "result"
        self.da.queue_draw()   # loop doesn't redraw REFLEX, so refresh on each click
    def _draw_reflex(self, cr):
        r = self.rf; st = r["state"]
        bg, msg, col = (0.03,0.03,0.035), "CLICK TO START", (0.55,0.55,0.6)
        if st == "go": bg, msg, col = self.R, "CLICK!", (1,1,1)
        elif st == "wait": bg, msg, col = (0.08,0.03,0.04), "WAIT FOR RED…", (0.55,0.55,0.6)
        elif st == "result": msg, col = "%d MS" % r["last"], (0.93,0.93,0.93)
        elif st == "soon": msg, col = "TOO SOON", self.R
        cr.set_source_rgba(*bg, 1); cr.rectangle(0, 0, self.W, self.H); cr.fill()
        self._text(cr, msg, self.W / 2, self.H / 2, 24, col, 0.5, True)
        if r["best"] is not None: self._text(cr, "best %d ms" % r["best"], self.W / 2, self.H - 18, 12, (0.5,0.5,0.55), 0.5)

    # ---- RAIN ----
    def _rain_tick(self, dt):
        for r in self.rain:
            r[0] += r[1] * dt                          # y += speed·dt
            if r[0] > self.H + 20: r[0] = random.uniform(-40, 0)
    def _draw_rain(self, cr):
        for i, r in enumerate(self.rain):
            cx = i * 12 + 6; y = r[0]
            cr.set_source_rgba(*self.R, 1); cr.arc(cx, y, 2, 0, 6.2832); cr.fill()
            cr.set_source_rgba(0.9, 0.9, 0.9, 0.5); cr.arc(cx, y - 10, 1.4, 0, 6.2832); cr.fill()
            cr.set_source_rgba(0.9, 0.9, 0.9, 0.16); cr.arc(cx, y - 20, 1.1, 0, 6.2832); cr.fill()

    # ---- HOOPS (slingshot basketball) ----
    def _hoops_press(self, e):
        h = self.hoops
        if h["flying"]: return
        if math.hypot(e.x - h["bx"], e.y - h["by"]) < 48:
            h["aiming"] = True; h["ax"] = e.x; h["ay"] = e.y; self.da.queue_draw()
    def _hoops_motion(self, e):
        h = self.hoops
        if h["aiming"]: h["ax"] = e.x; h["ay"] = e.y; self.da.queue_draw()
    def _hoops_release(self, e):
        h = self.hoops
        if not h["aiming"]: return
        h["aiming"] = False; h["scored"] = False; h["flying"] = True
        h["vx"] = (h["bx"] - h["ax"]) * self.HOOPS_K
        h["vy"] = (h["by"] - h["ay"]) * self.HOOPS_K
        self._last_t = time.monotonic(); self.da.queue_draw()
    def _hoops_reset_ball(self):
        h = self.hoops
        h.update(bx=80.0, by=150.0, vx=0.0, vy=0.0, flying=False, aiming=False, scored=False)
    def _hoops_new_hoop(self):
        h = self.hoops
        h["hx"] = self.W - 68 + random.uniform(-8, 8); h["hy"] = 60 + random.random() * 34
    def _hoops_tick(self, dt):
        h = self.hoops
        h["vy"] += self.HOOPS_G * dt
        h["bx"] += h["vx"] * dt; h["by"] += h["vy"] * dt
        # score: falling through the rim within its x-span
        if (not h["scored"] and h["vy"] > 0 and h["hy"] - 6 < h["by"] < h["hy"] + 12
                and h["hx"] - h["hw"] / 2 < h["bx"] < h["hx"] + h["hw"] / 2):
            h["scored"] = True; h["score"] += 1; h["streak"] += 1
            def _sc(): self._hoops_reset_ball(); self._hoops_new_hoop(); self.da.queue_draw(); return False
            GLib.timeout_add(360, _sc)
        # backboard bounce
        boardx = h["hx"] + h["hw"] / 2 + 7
        if h["bx"] + h["r"] > boardx and h["hy"] - 26 < h["by"] < h["hy"] + 22 and h["vx"] > 0:
            h["vx"] *= -0.5; h["bx"] = boardx - h["r"]
        # off-screen → miss
        if h["by"] > self.H + 50 or h["bx"] > self.W + 60 or h["bx"] < -60:
            if not h["scored"]: h["streak"] = 0
            self._hoops_reset_ball()
    def _draw_hoops(self, cr):
        h = self.hoops; hx, hy, hw = h["hx"], h["hy"], h["hw"]
        cr.set_source_rgba(1, 1, 1, 0.05)   # court dot grid
        for gx in range(12, self.W, 24):
            for gy in range(12, self.H, 24): cr.arc(gx, gy, 1, 0, 6.2832); cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.22); cr.set_line_width(2)   # backboard
        cr.move_to(hx + hw / 2 + 7, hy - 26); cr.line_to(hx + hw / 2 + 7, hy + 22); cr.stroke()
        cr.set_source_rgba(*self.R, 1); cr.set_line_width(3)      # rim
        cr.move_to(hx - hw / 2, hy); cr.line_to(hx + hw / 2, hy); cr.stroke()
        cr.set_source_rgba(1, 1, 1, 0.28)                          # net dots
        for i in range(7): cr.arc(hx - hw / 2 + i * hw / 6, hy + 9, 1, 0, 6.2832); cr.fill()
        if h["aiming"]:                                            # dotted aim preview
            px, py = h["bx"], h["by"]
            pvx = (h["bx"] - h["ax"]) * self.HOOPS_K; pvy = (h["by"] - h["ay"]) * self.HOOPS_K
            cr.set_source_rgba(*self.R, 0.5)
            for _ in range(22):
                pvy += self.HOOPS_G * 0.045; px += pvx * 0.045; py += pvy * 0.045
                if py > self.H or px > self.W or px < 0: break
                cr.arc(px, py, 1.6, 0, 6.2832); cr.fill()
        cr.set_source_rgba(*self.R, 1)                             # ball
        cr.arc(h["bx"], h["by"], h["r"], 0, 6.2832); cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.4); cr.set_line_width(1)
        cr.move_to(h["bx"] - h["r"], h["by"]); cr.line_to(h["bx"] + h["r"], h["by"]); cr.stroke()
        self._text(cr, "%d · streak %d" % (h["score"], h["streak"]), self.W - 8, 16, 13, (0.55, 0.55, 0.6), 1)

    # ---- draw dispatch + helpers ----
    def _draw(self, da, cr):
        cr.set_source_rgba(0.02, 0.02, 0.024, 1); cr.rectangle(0, 0, self.W, self.H); cr.fill()
        getattr(self, "_draw_" + self.GAMES[self.gi].lower())(cr)
        if self.da.has_focus():
            cr.set_source_rgba(*self.R, 0.5); cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, self.W - 1, self.H - 1); cr.stroke()
        return False
    def _text(self, cr, s, x, y, size, col, align=0, bold=False):
        cr.select_font_face("DejaVu Sans Mono", 0, 1 if bold else 0)
        cr.set_font_size(size); cr.set_source_rgba(*col, 1)
        ext = cr.text_extents(s); cr.move_to(x - ext.width * align, y + ext.height / 2); cr.show_text(s)

# ---------- REPOS :// SYNC ----------
# Panel for sync-repos.sh. The sweep itself runs on a systemd timer; this only
# reads the JSON it leaves behind, so the desktop never blocks on git.
REPO_CACHE   = os.path.expanduser("~/.cache/repo-sync")
REPO_STATUS  = os.path.join(REPO_CACHE, "status.json")
REPO_OFFLINE = os.path.join(REPO_CACHE, "offline")
REPO_CONF_D  = os.path.expanduser("~/.config/repo-sync")
REPO_CONF    = os.path.join(REPO_CONF_D, "config.json")
SYNC_SCRIPT  = os.path.expanduser("~/projects/sync-repos.sh")
REPO_ROOT0   = os.path.expanduser("~/projects")
REPO_ROWS    = 8          # attention rows before the list collapses to "+N more"

def repo_conf():
    """{roots, disabled}; tolerant of a missing or hand-mangled config."""
    try:
        c = json.load(open(REPO_CONF))
        if not isinstance(c, dict): raise ValueError
    except Exception:
        c = {}
    roots = [r for r in (c.get("roots") or []) if isinstance(r, str)]
    dis   = [d for d in (c.get("disabled") or []) if isinstance(d, str)]
    return {"roots": roots or [REPO_ROOT0], "disabled": dis}

def save_repo_conf(roots, disabled):
    os.makedirs(REPO_CONF_D, exist_ok=True)
    tmp = REPO_CONF + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"roots": roots, "disabled": sorted(set(disabled))}, f, indent=2)
    os.replace(tmp, REPO_CONF)     # atomic — a sweep may be reading this right now

def discover_repos(roots):
    """[(path, root)] for every git repo under the roots. A root that is itself
    a repo counts as one, matching sync-repos.sh so the UI can't disagree."""
    found, seen = [], set()
    for root in roots:
        root = os.path.expanduser(root)
        if not os.path.isdir(root): continue
        if os.path.isdir(os.path.join(root, ".git")):
            cand = [root]
        else:
            try: names = sorted(os.listdir(root), key=str.lower)
            except OSError: continue
            cand = [os.path.join(root, n) for n in names
                    if os.path.isdir(os.path.join(root, n, ".git"))]
        for p in cand:
            if p not in seen:
                seen.add(p); found.append((p, root))
    return found

def _mark(r):
    """Right-hand status chip for one repo row, plus whether it needs attention."""
    st, behind, dirty = r.get("state"), r.get("behind", 0), r.get("dirty_tracked", 0)
    if st == "PULLED":      return "↓%d ✓" % behind, False
    if st == "HELD":        return "↓%d ⚠%d" % (behind, dirty), True
    if st == "ERROR":       return "ERR", True
    if st == "LOCAL":       return "LOCAL", False
    if st == "NO_UPSTREAM": return "NO UPSTREAM", False
    return "", False

class Repos(Widget):
    def __init__(self):
        super().__init__("repos", 1440, 40, 430)
        gear = Gtk.Button(label="⚙")
        gear.get_style_context().add_class("caret")
        gear.set_relief(Gtk.ReliefStyle.NONE)
        gear.set_tooltip_text("Choose which repos to sync")
        gear.connect("clicked", self.open_settings)

        b = vbox(6, m=22)
        b.pack_start(self.header("REPOS :// SYNC", subtitle=gear), False, False, 0)
        b.pack_start(rule(), False, False, 0)
        self.summary = L("—", "v")
        b.pack_start(self.summary, False, False, 0)
        b.pack_start(rule(), False, False, 0)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        b.pack_start(self.rows, False, False, 0)
        b.pack_start(rule(), False, False, 0)
        self.foot = L("", "faint")
        b.pack_start(self.foot, False, False, 0)
        self.add(b)

        self._win = None
        self.first(self.refresh)
        GLib.timeout_add(30000, self.refresh)

    # ---- settings ----------------------------------------------------------
    def open_settings(self, *_):
        if self._win is not None:          # already open — just raise it
            self._win.present(); return
        self._win = ReposSettings(self)
        self._win.connect("destroy", self._settings_closed)
        self._win.show_all()

    def _settings_closed(self, *_):
        self._win = None
        self.refresh()

    def sync_now(self):
        sh("%s --quiet" % shlex.quote(SYNC_SCRIPT))
        # the sweep takes ~5s; look again a few times rather than guessing once
        for delay in (6000, 12000, 25000):
            GLib.timeout_add(delay, lambda: (self.refresh(), False)[1])

    # ---- display -----------------------------------------------------------
    def _row(self, r):
        box = Gtk.Box(spacing=10)
        name = L(r.get("name", "?")[:22], "v"); name.set_hexpand(True); name.set_xalign(0)
        br = L((r.get("branch") or "")[:11], "faint")
        text, hot = _mark(r)
        chip = L(text, "dotlit" if hot else "faint")
        box.pack_start(name, True, True, 0)
        box.pack_start(br, False, False, 0)
        box.pack_end(chip, False, False, 0)
        return box

    def refresh(self):
        for ch in self.rows.get_children(): self.rows.remove(ch)
        try:
            st = json.load(open(REPO_STATUS))
        except Exception:
            self.summary.set_text("no sync data yet")
            self.foot.set_text("run sync-repos.sh")
            self.rows.show_all()
            return True

        t = st.get("totals", {})
        self.summary.set_text("✓ %d   ↓ %d   ⚠ %d" % (
            t.get("synced", 0), t.get("pulled", 0),
            t.get("held", 0) + t.get("error", 0)))

        att = [r for r in st.get("repos", []) if r.get("state") != "SYNCED"]
        for r in att[:REPO_ROWS]:
            self.rows.pack_start(self._row(r), False, False, 0)
        if not att:
            self.rows.pack_start(L("all clean", "faint"), False, False, 0)
        elif len(att) > REPO_ROWS:
            self.rows.pack_start(L("+%d more" % (len(att) - REPO_ROWS), "faint"), False, False, 0)
        self.rows.show_all()

        gen = st.get("generated_at", 0)
        nxt = gen + st.get("interval_seconds", 7200)
        foot = "last %s · next %s" % (time.strftime("%H:%M", time.localtime(gen)),
                                           time.strftime("%H:%M", time.localtime(nxt)))
        off = t.get("disabled", 0)
        if off: foot += " · %d off" % off
        if os.path.exists(REPO_OFFLINE):
            # the sweep keeps the last good numbers during an outage and drops
            # this marker; say so, or the panel reads as current when it isn't
            try: since = int(open(REPO_OFFLINE).read().split("\t")[0])
            except Exception: since = 0
            foot = "OFFLINE since %s · %s" % (
                time.strftime("%H:%M", time.localtime(since)), foot)
        self.foot.set_text(foot)
        return True


class ReposSettings(Gtk.Window):
    """Checklist of every discovered repo + the roots they're discovered under.
    Unticked repos are written to config.json's `disabled` list by path."""
    def __init__(self, parent):
        super().__init__(title="REPOS :// SETTINGS")
        self.parent_widget = parent
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_keep_above(True)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_default_size(560, 640)
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis: self.set_visual(vis)
        self.connect("key-press-event", self._key)

        cfg = repo_conf()
        self.roots = list(cfg["roots"])
        self.disabled = set(cfg["disabled"])
        self.checks = {}                    # path -> Gtk.CheckButton

        outer = vbox(8, m=22)
        hdr = Gtk.Box(spacing=8)
        title = L("REPOS :// SETTINGS", "title"); title.set_hexpand(True); title.set_xalign(0)
        close = Gtk.Button(label="✕")
        close.get_style_context().add_class("caret")
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda *_: self.destroy())
        hdr.pack_start(title, True, True, 0)
        hdr.pack_end(close, False, False, 0)
        outer.pack_start(hdr, False, False, 0)
        outer.pack_start(rule(), False, False, 0)

        # --- add a path -----------------------------------------------------
        addrow = Gtk.Box(spacing=6)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("add a folder of repos, or one repo…")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", lambda *_: self.add_root())
        browse = Gtk.Button(label="BROWSE"); browse.get_style_context().add_class("tile")
        browse.set_relief(Gtk.ReliefStyle.NONE); browse.connect("clicked", self.browse)
        addb = Gtk.Button(label="ADD"); addb.get_style_context().add_class("tile")
        addb.set_relief(Gtk.ReliefStyle.NONE); addb.connect("clicked", lambda *_: self.add_root())
        addrow.pack_start(self.entry, True, True, 0)
        addrow.pack_start(browse, False, False, 0)
        addrow.pack_start(addb, False, False, 0)
        outer.pack_start(addrow, False, False, 0)
        self.err = L("", "faint")
        outer.pack_start(self.err, False, False, 0)

        # --- the list -------------------------------------------------------
        self.listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.add(self.listbox)
        outer.pack_start(scroll, True, True, 0)

        outer.pack_start(rule(), False, False, 0)

        # --- bulk actions ---------------------------------------------------
        bulk = Gtk.Box(spacing=6)
        for label, fn in (("ALL", lambda *_: self.set_all(True)),
                          ("NONE", lambda *_: self.set_all(False)),
                          ("INVERT", lambda *_: self.invert())):
            btn = Gtk.Button(label=label); btn.get_style_context().add_class("tile")
            btn.set_relief(Gtk.ReliefStyle.NONE); btn.connect("clicked", fn)
            btn.set_hexpand(True)
            bulk.pack_start(btn, True, True, 0)
        outer.pack_start(bulk, False, False, 0)

        act = Gtk.Box(spacing=6)
        save = Gtk.Button(label="SAVE & SYNC NOW"); save.get_style_context().add_class("tile")
        save.set_relief(Gtk.ReliefStyle.NONE); save.connect("clicked", self.save_and_sync)
        save.set_hexpand(True)
        act.pack_start(save, True, True, 0)
        outer.pack_start(act, False, False, 0)

        self.add(outer)
        self.rebuild()

    def _key(self, w, e):
        if e.keyval == Gdk.KEY_Escape: self.destroy()
        return False

    # ---- roots -------------------------------------------------------------
    def browse(self, *_):
        dlg = Gtk.FileChooserDialog(title="Add a repo folder", parent=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        dlg.set_current_folder(os.path.expanduser("~"))
        if dlg.run() == Gtk.ResponseType.OK:
            self.entry.set_text(dlg.get_filename() or "")
            dlg.destroy(); self.add_root()
        else:
            dlg.destroy()

    def add_root(self):
        p = os.path.abspath(os.path.expanduser(self.entry.get_text().strip()))
        if not p or p == "/":
            return
        if not os.path.isdir(p):
            self.err.set_text("not a folder: %s" % p); return
        if p in [os.path.expanduser(r) for r in self.roots]:
            self.err.set_text("already added"); return
        if not discover_repos([p]):
            self.err.set_text("no git repos found in %s" % p); return
        self.roots.append(p)
        self.entry.set_text(""); self.err.set_text("")
        self.rebuild()

    def remove_root(self, root):
        if len(self.roots) <= 1:
            self.err.set_text("keep at least one folder"); return
        self.roots = [r for r in self.roots if r != root]
        self.rebuild()

    # ---- checklist ---------------------------------------------------------
    def rebuild(self):
        for ch in self.listbox.get_children(): self.listbox.remove(ch)
        self.checks.clear()
        repos = discover_repos(self.roots)
        by_root = {}
        for path, root in repos: by_root.setdefault(root, []).append(path)

        for root in self.roots:
            root_x = os.path.expanduser(root)
            head = Gtk.Box(spacing=8)
            lbl = L(root_x.replace(os.path.expanduser("~"), "~"), "k")
            lbl.set_hexpand(True); lbl.set_xalign(0)
            rm = Gtk.Button(label="✕")
            rm.get_style_context().add_class("caret"); rm.set_relief(Gtk.ReliefStyle.NONE)
            rm.set_tooltip_text("stop scanning this folder")
            rm.connect("clicked", lambda _b, r=root: self.remove_root(r))
            head.pack_start(lbl, True, True, 0)
            head.pack_end(rm, False, False, 0)
            self.listbox.pack_start(head, False, False, 0)

            paths = by_root.get(root_x, [])
            if not paths:
                self.listbox.pack_start(L("   (no repos here)", "faint"), False, False, 0)
            for p in paths:
                cb = Gtk.CheckButton(label=os.path.basename(p))
                cb.set_active(p not in self.disabled)
                cb.get_style_context().add_class("v")
                cb.set_margin_start(10)
                self.checks[p] = cb
                self.listbox.pack_start(cb, False, False, 0)
            self.listbox.pack_start(rule(), False, False, 0)
        self.listbox.show_all()

    def set_all(self, on):
        for cb in self.checks.values(): cb.set_active(on)

    def invert(self):
        for cb in self.checks.values(): cb.set_active(not cb.get_active())

    # ---- persist -----------------------------------------------------------
    def save_and_sync(self, *_):
        # Keep disabled entries for paths we can't currently see (an unplugged
        # drive, a removed root) so unticking them isn't silently forgotten.
        visible = set(self.checks)
        keep = {d for d in self.disabled if d not in visible}
        off = {p for p, cb in self.checks.items() if not cb.get_active()}
        save_repo_conf(self.roots, sorted(keep | off))
        self.disabled = keep | off
        self.parent_widget.sync_now()
        self.destroy()

# ---------- main ----------
def main():
    apply_css()
    assistant = Assistant()               # hidden; opened by the orb
    # --- rail pop-outs: built now, shown only when their icon is clicked ---
    rail_targets = {"controls": Controls(), "pomo": Pomodoro(), "calendar": Calendar(),
                    "notes": Notes(), "arcade": Arcade(), "palette": Palette()}
    for _n, _w in rail_targets.items():
        _w.set_size_request(NOTES_W if _n == "notes" else POPOUT_W, -1)
    # --- the pane: the five cards worth seeing at a glance ---
    pane = [Clock(), System(), Status(), Network(), NowPlaying(), Sessions()]
    chrome = [Rail(rail_targets), AssistantOrb(assistant), Launcher(), Repos()]
    for w in pane + chrome: w.show_all()
    start_pulse()
    GLib.idle_add(relayout_pane)          # stack the column once real sizes are known
    GLib.timeout_add(400, relayout_pane)
    # Muffin nudges sticky windows out of place when you switch workspaces. A
    # light heartbeat re-asserts the stack; relayout_pane() moves only cards that
    # have actually drifted, so this is nearly free when nothing changed.
    GLib.timeout_add(1200, pane_keeper)
    Gtk.main()

if __name__ == "__main__":
    import signal; signal.signal(signal.SIGINT, lambda *a: Gtk.main_quit())
    main()
