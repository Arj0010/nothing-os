#!/usr/bin/env python3
"""Nothing OS — interactive, movable desktop widgets (GTK3).
Drag a widget by its body to move it (position persists); buttons stay clickable."""
import gi, os, json, subprocess, math, time, random, urllib.request, glob, threading
from collections import deque
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango  # noqa

ACCENT   = "#D71921"
CONF_DIR = os.path.expanduser("~/.config/nothing-widgets")
POS_FILE = os.path.join(CONF_DIR, "positions.json")
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

# ---------- styling ----------
CSS = ("""
.card { background-color: rgba(11,11,13,0.86); border: 1px solid rgba(255,255,255,0.13);
        border-radius: 8px; padding: 18px 20px; margin: 10px;
        transition: border-color 180ms ease, box-shadow 180ms ease; }
.card:hover, .card.hov {
        border-color: rgba(215,25,33,0.85);
        background-color: rgba(22,7,8,0.93);
        box-shadow: inset 0 0 34px rgba(215,25,33,0.32), 0 0 24px 3px rgba(215,25,33,0.38); }
.sbar   { font-family:"DejaVu Sans Mono"; font-size:12px; letter-spacing:1px; }
.calmonth { color:#ededed; font-family:"Ndot 57"; font-size:30px; letter-spacing:2px; }
.calwd  { color:#6a6a6a; font-family:"NType 82"; font-size:11px; letter-spacing:1px; }
.cal    { color:#c0c0c0; font-family:"Lettera Mono LL"; font-size:15px; }
.calcell { background-color:transparent; border:1px solid transparent; border-radius:5px;
           color:#c0c0c0; font-family:"Lettera Mono LL"; font-size:15px; padding:0; }
.calcell:hover { border-color:%(a)s; color:#ffffff;
                 background-color:rgba(22,7,8,0.9);
                 box-shadow: inset 0 0 12px rgba(215,25,33,0.30); }
.calcell.today { color:#ffffff; background-color:%(a)s; border-color:%(a)s; }
.calinfo { color:#6a6a6a; font-family:"Lettera Mono LL"; font-size:11px; letter-spacing:1px; }
.title  { color:#707070; font-family:"NType 82"; font-size:12px; letter-spacing:3px; }
.hdot   { color:%(a)s; font-family:"DejaVu Sans Mono"; font-size:11px; }
.rule   { background-color: rgba(255,255,255,0.07); min-height:1px; }
.clock  { color:#f2f2f2; font-family:"Ndot 57"; font-size:104px; }
.csec   { color:%(a)s; font-family:"Ndot 57"; font-size:34px; }
.date   { color:#8a8a8a; font-family:"Ndot 57"; font-size:26px; letter-spacing:3px; }
.k      { color:#8a8a8a; font-family:"NType 82"; font-size:13px; letter-spacing:2px; }
.v      { color:#ededed; font-family:"Lettera Mono LL"; font-size:14px; }
.meter  { font-family:"DejaVu Sans Mono"; font-size:15px; color:#e8e8e8; }
.meter.hot { color:%(a)s; }
.big    { color:#ededed; font-family:"Ndot 57"; font-size:30px; }
.dim    { color:#9a9a9a; font-family:"Lettera Mono LL"; font-size:13px; }
.faint  { color:#5a5a5a; font-family:"Lettera Mono LL"; font-size:12px; }
.dotlit { color:%(a)s; font-family:"DejaVu Sans Mono"; font-size:10px; }
.dotidle{ color:#3a3a3a; font-family:"DejaVu Sans Mono"; font-size:10px; }
.tile   { background-color:#0a0a0a; border:1px solid rgba(255,255,255,0.08); border-radius:5px;
          color:#8a8a8a; font-family:"NType 82"; font-size:13px; letter-spacing:2px; padding:16px 14px; }
.tile:hover { border-color:%(a)s; color:#ededed; box-shadow: 0 0 12px 0 rgba(215,25,33,0.22); }
.tile.on { background-color:#160607; border-color:%(a)s; color:#f2f2f2; }
.app    { background-color:#0a0a0a; border:1px solid rgba(255,255,255,0.08); border-radius:8px;
          padding:10px 8px 7px;
          transition: background-color 160ms ease, border-color 160ms ease, box-shadow 160ms ease; }
.app:hover { border-color:%(a)s; background-color:#160607; box-shadow: 0 0 16px 1px rgba(215,25,33,0.34); }
.applabel { color:#9a9a9a; font-family:"NType 82"; font-size:10px; letter-spacing:1px; }
.app:hover .applabel { color:#ffffff; }
.rundot   { color:%(a)s; font-family:"DejaVu Sans Mono"; font-size:9px; }
.rundotoff{ color:#2c2c2c; font-family:"DejaVu Sans Mono"; font-size:9px; }
.dockbar  { background-color: rgba(17,17,20,0.80); border:1px solid rgba(255,255,255,0.12);
            border-radius: 22px; padding: 7px 12px; }
.dapp     { background-color:transparent; border:0; border-radius:14px; padding:2px 5px;
            transition: background-color 140ms ease; }
.dapp:hover { background-color: rgba(255,255,255,0.09); }
.viz      { font-family:"DejaVu Sans Mono"; font-size:22px; color:%(a)s; letter-spacing:1px; }
.vizoff   { font-family:"DejaVu Sans Mono"; font-size:22px; color:#333333; letter-spacing:1px; }
.clock2   { color:#f2f2f2; font-family:"Ndot 57"; font-size:60px; }
.notes, .notes text { background-color:transparent; color:#d8d8d8;
            font-family:"Lettera Mono LL"; font-size:14px; caret-color:%(a)s; }
.chat, .chat text { background-color:transparent; color:#d0d0d0;
            font-family:"Lettera Mono LL"; font-size:13px; }
.chatin, .chatin text { background-color:#0a0a0a; color:#ededed; caret-color:%(a)s;
            font-family:"Lettera Mono LL"; font-size:13px; border:1px solid rgba(255,255,255,0.12);
            border-radius:6px; padding:7px 10px; }
.chatin:focus { border-color:%(a)s; }
.sendbtn { background-color:#160607; border:1px solid %(a)s; border-radius:6px;
            color:#f2f2f2; font-family:"NType 82"; font-size:12px; letter-spacing:2px; padding:7px 16px; }
.sendbtn:hover { background-color:%(a)s; }
.chatq { color:%(a)s; font-family:"NType 82"; font-size:11px; letter-spacing:1px; }
.chata { color:#cfcfcf; font-family:"Lettera Mono LL"; font-size:13px; }
.chatsys { color:#6a6a6a; font-family:"Lettera Mono LL"; font-size:11px; }
.media  { background-color:transparent; border:0; color:#9a9a9a;
          font-family:"DejaVu Sans Mono"; font-size:20px; padding:2px 12px; }
.media:hover { color:%(a)s; }
""" % {"a": ACCENT}).encode()

def apply_css():
    prov = Gtk.CssProvider(); prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)

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
        self.set_app_paintable(True)
        vis = self.get_screen().get_rgba_visual()
        if vis: self.set_visual(vis)
        if w: self.set_size_request(w, -1)
        self._last_press = 0
        self._cardbox = None
        self._armed = False; self._moved = False
        self._px = self._py = 0; self._save_scheduled = False
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
        # persist position after a move, throttled to one write per move
        if not self._save_scheduled:
            self._save_scheduled = True
            GLib.timeout_add(500, self._flush_pos)
        return False

    def _flush_pos(self):
        self._save_scheduled = False
        x, y = self.get_position(); save_pos(self.wname, x, y)
        return False

    def header(self, text):
        box = Gtk.Box(spacing=8)
        lbl = L(text, "title"); lbl.set_hexpand(True); lbl.set_xalign(0)
        dot = L("●", "hdot"); dot.set_valign(Gtk.Align.CENTER)
        HDOTS.append(dot)   # pulsed together by start_pulse()
        box.pack_start(lbl, True, True, 0)
        box.pack_end(dot, False, False, 0)
        return box

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
        b = vbox(8, m=24)
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
        self.secbar.set_markup(two_tone(t.tm_sec / 60 * 100, 44))
        hh = t.tm_hour
        greet = ("GOOD MORNING" if hh < 12 else "GOOD AFTERNOON" if hh < 17
                 else "GOOD EVENING" if hh < 21 else "GOOD NIGHT")
        self.date.set_text("%s · %s" % (greet, time.strftime("%a %d %b %Y", t).upper()))
        return True

# ---------- QUICK CONTROLS ----------
class Controls(Widget):
    def __init__(self):
        super().__init__("controls", 1130, 40, 710)
        b = vbox(12, m=22); b.pack_start(self.header("QUICK CONTROLS"), False, False, 0)
        grid = Gtk.Grid(row_spacing=11, column_spacing=11)
        grid.set_column_homogeneous(True); grid.set_row_homogeneous(True)
        self.tiles = {}
        defs = [("wifi","WI-FI",0,0),("bt","BLUETOOTH",0,1),
                ("air","AIRPLANE",1,0),("dnd","DO NOT DISTURB",1,1)]
        for key,label,r,c in defs:
            btn = Gtk.Button(label=label); btn.get_style_context().add_class("tile")
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.set_hexpand(True); btn.set_vexpand(True)
            btn.set_size_request(-1, 66)
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
        b = vbox(9, m=22); b.pack_start(self.header("SYSTEM · i5-1135G7"), False, False, 0)
        self.rows = {}
        self.hist = {"CPU": deque(maxlen=34), "RAM": deque(maxlen=34)}
        for k in ("CPU","RAM","TMP","BAT"):
            row = Gtk.Box(spacing=18)
            key = L(k, "k"); key.set_size_request(44,-1)
            meter = L("", "meter"); meter.set_xalign(0)
            val = L("", "dim"); val.set_xalign(1); val.set_size_request(72,-1)
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
                meter.set_text(dots(data[k], 30))
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
        b = vbox(9, m=22); b.pack_start(self.header("NETWORK"), False, False, 0)
        self.iface = out("ip route 2>/dev/null | awk '/default/{print $5; exit}'") or "wlp0s20f3"
        self.ssid = self._kv(b, "SSID")
        self.ip   = self._kv(b, "LOCAL IP")
        sp = Gtk.Box(spacing=22)
        self.down = L("↓ 0", "dim"); self.up = L("↑ 0", "dim")
        sp.pack_start(self.down, False, False, 0)
        sp.pack_start(self.up, False, False, 0)
        sp.set_margin_top(2); b.pack_start(sp, False, False, 0)
        self.nhist = deque(maxlen=42)
        self.nspark = L("", "meter"); self.nspark.set_xalign(0); self.nspark.set_margin_top(2)
        b.pack_start(self.nspark, False, False, 0)
        self.add(b); self._rx=self._tx=None; self.first(self.tick); GLib.timeout_add(2000, self.tick)
    def _kv(self, b, k):
        row = Gtk.Box(spacing=14)
        key = L(k, "k"); key.set_size_request(96,-1)
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
        b = vbox(10, m=22); b.pack_start(self.header("NOW PLAYING"), False, False, 0)
        self.track = L("—", "dim"); self.track.set_line_wrap(True); self.track.set_max_width_chars(48)
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
        self._bars = [0.05] * 30; self._playing = False
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
        b = vbox(9, m=22); b.pack_start(self.header("SYSTEM :// STATUS"), False, False, 0)
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
        b = vbox(8, m=22); b.pack_start(self.header("CALENDAR"), False, False, 0)
        b.pack_start(L(today.strftime("%B %Y").upper(), "calmonth"), False, False, 0)
        grid = Gtk.Grid(row_spacing=6, column_spacing=6); grid.set_column_homogeneous(True)
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
                cell.set_size_request(44, 30)
                grid.attach(cell, c, r, 1, 1)
        b.pack_start(grid, False, False, 0)
        # offline "agenda": derived facts, no external data needed
        wk = today.isocalendar()[1]; doy = today.timetuple().tm_yday
        yr_days = 366 if calendar.isleap(today.year) else 365
        left = calendar.monthrange(today.year, today.month)[1] - today.day
        info = L("WEEK %02d · DAY %d/%d · %d LEFT IN MONTH" % (wk, doy, yr_days, left), "calinfo")
        info.set_margin_top(4); b.pack_start(info, False, False, 0)
        self.add(b)

# ---------- LOCAL MODEL (Ollama + iGPU) ----------
class LLMStatus(Widget):
    def __init__(self):
        super().__init__("llm", 690, 300, 420)   # fills the empty centre
        b = vbox(9, m=22); b.pack_start(self.header("LOCAL MODEL"), False, False, 0)
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
        b = vbox(8, m=22); b.pack_start(self.header("FOCUS"), False, False, 0)
        self.disp = L("25:00", "clock2"); self.disp.set_xalign(0)
        b.pack_start(self.disp, False, False, 0)
        self.bar = Gtk.Label(); self.bar.set_xalign(0)
        self.bar.get_style_context().add_class("sbar")
        b.pack_start(self.bar, False, False, 0)
        ctrl = Gtk.Box(spacing=8)
        self.startbtn = Gtk.Button(label="START")
        self.startbtn.get_style_context().add_class("tile")
        self.startbtn.set_relief(Gtk.ReliefStyle.NONE)
        self.startbtn.set_size_request(-1, 42); self.startbtn.set_hexpand(True)
        self.startbtn.connect("clicked", self._toggle)
        rb = Gtk.Button(label="RESET"); rb.get_style_context().add_class("tile")
        rb.set_relief(Gtk.ReliefStyle.NONE); rb.set_size_request(-1, 42); rb.set_hexpand(True)
        rb.connect("clicked", self._reset)
        ctrl.pack_start(self.startbtn, True, True, 0); ctrl.pack_start(rb, True, True, 0)
        b.pack_start(ctrl, False, False, 0)
        self.add(b); self._render()
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
        self.bar.set_markup(two_tone((self.WORK - self.remaining) / self.WORK * 100, 38))

# ---------- NOTES (editable, autosaved) ----------
class Notes(Widget):
    FILE = os.path.join(CONF_DIR, "notes.txt")
    def __init__(self):
        super().__init__("notes", 1130, 762, 710, focusable=True)
        b = vbox(8, m=22); b.pack_start(self.header("NOTES"), False, False, 0)
        self.buf = Gtk.TextBuffer()
        try: self.buf.set_text(open(self.FILE).read())
        except Exception: pass
        tv = Gtk.TextView.new_with_buffer(self.buf)
        tv.get_style_context().add_class("notes")
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)  # vertical scroll
        sc.set_size_request(-1, 108); sc.get_style_context().add_class("notes")
        sc.add(tv)
        b.pack_start(sc, True, True, 0)
        self.add(b)
        self.buf.connect("changed", self._save)
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
        b = vbox(8, m=18)
        head = Gtk.Box(spacing=6)
        self.titlelbl = L("JARVIS", "title"); self.titlelbl.set_hexpand(True); self.titlelbl.set_xalign(0)
        self.mbtn = Gtk.Button(label="qwen2.5-coder:3b"); self.mbtn.get_style_context().add_class("chatq")
        self.mbtn.set_relief(Gtk.ReliefStyle.NONE); self.mbtn.connect("clicked", self._cycle_model)
        clr = Gtk.Button(label="CLEAR"); clr.get_style_context().add_class("media")
        clr.set_relief(Gtk.ReliefStyle.NONE); clr.connect("clicked", self._clear)
        dot = L("●", "hdot"); HDOTS.append(dot)
        head.pack_start(self.titlelbl, True, True, 0)
        head.pack_end(dot, False, False, 0); head.pack_end(clr, False, False, 0)
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
        self.sc.set_size_request(-1, 150); self.sc.add(self.tv)
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

# ---------- ARCADE (switchable mini-games) ----------
class Arcade(Widget):
    GAMES = ["DASH", "SNAKE", "REFLEX", "RAIN"]
    HINTS = {"DASH":"space / click to jump", "SNAKE":"click, then arrows / WASD",
             "REFLEX":"click when it turns red", "RAIN":"ambient · dot rain"}
    W, H = 404, 236
    COLS, ROWS, CELL = 25, 14, 16
    R = (0.843, 0.098, 0.129)   # accent

    def __init__(self):
        super().__init__("arcade", 690, 610, 440, focusable=True)
        self.gi = 0; self.frame = 0
        b = vbox(8, m=18)
        head = Gtk.Box(spacing=6)
        self.titlelbl = L("ARCADE · DASH", "title"); self.titlelbl.set_hexpand(True); self.titlelbl.set_xalign(0)
        prev = Gtk.Button(label="‹"); nxt = Gtk.Button(label="›")
        for btn, d in ((prev, -1), (nxt, 1)):
            btn.get_style_context().add_class("media"); btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.connect("clicked", lambda w, dd=d: self.switch(dd))
        dot = L("●", "hdot"); HDOTS.append(dot)
        head.pack_start(self.titlelbl, True, True, 0)
        head.pack_end(dot, False, False, 0); head.pack_end(nxt, False, False, 0); head.pack_end(prev, False, False, 0)
        b.pack_start(head, False, False, 0)
        self.da = Gtk.DrawingArea(); self.da.set_size_request(self.W, self.H); self.da.set_can_focus(True)
        self.da.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        self.da.connect("draw", self._draw)
        self.da.connect("button-press-event", self._click)
        self.da.connect("key-press-event", self._key)
        b.pack_start(self.da, False, False, 0)
        self.hint = L(self.HINTS["DASH"], "faint"); self.hint.set_xalign(0)
        b.pack_start(self.hint, False, False, 0)
        self.add(b)
        for n in self.GAMES: self._reset_game(n)
        GLib.timeout_add(33, self._loop)

    def switch(self, d):
        self.gi = (self.gi + d) % len(self.GAMES)
        name = self.GAMES[self.gi]
        self.titlelbl.set_text("ARCADE · " + name); self.hint.set_text(self.HINTS[name])
        self._reset_game(name); self.da.grab_focus(); self.da.queue_draw()

    def _reset_game(self, name):
        if name == "DASH":
            self.dash = dict(py=self.H - 24, vy=0, onground=True, obs=[], spd=3.4, dist=0, alive=True, spawn=60)
        elif name == "SNAKE":
            self.sn = dict(body=[(5, 8), (4, 8), (3, 8)], dir=(1, 0), ndir=(1, 0), alive=True, food=(12, 8), score=0)
            self._snake_food()
        elif name == "REFLEX":
            self.rf = dict(state="idle", t0=0.0, best=None, last=None, timer=None)
        elif name == "RAIN":
            self.rain = [random.uniform(-self.H, 0) for _ in range(self.W // 12)]

    # ---- input ----
    def _click(self, da, e):
        da.grab_focus(); name = self.GAMES[self.gi]
        if name == "DASH": self._dash_jump()
        elif name == "REFLEX": self._reflex_click()
        return True

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
        try:
            self.frame += 1; name = self.GAMES[self.gi]
            if name == "DASH" and self.dash["alive"]: self._dash_tick()
            elif name == "SNAKE" and self.frame % 4 == 0 and self.sn["alive"]: self._snake_tick()
            elif name == "RAIN": self._rain_tick()
            self.da.queue_draw()
        except Exception: pass
        return True

    # ---- DASH ----
    def _dash_jump(self):
        d = self.dash
        if not d["alive"]: self._reset_game("DASH"); return
        if d["onground"]: d["vy"] = -8.6; d["onground"] = False
    def _dash_tick(self):
        d = self.dash; g = self.H - 24
        d["dist"] += d["spd"]; d["spd"] += 0.0018
        d["vy"] += 0.5; d["py"] += d["vy"]
        if d["py"] >= g: d["py"] = g; d["vy"] = 0; d["onground"] = True
        d["spawn"] -= d["spd"]
        if d["spawn"] <= 0: d["obs"].append([self.W + 10, 14 + random.random() * 20]); d["spawn"] = 90 + random.random() * 80
        for o in d["obs"]: o[0] -= d["spd"]
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
            s["alive"] = False; GLib.timeout_add(700, lambda: (self._reset_game("SNAKE"), False)[1]); return
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
            def go(): r["state"] = "go"; r["t0"] = time.time(); r["timer"] = None; return False
            r["timer"] = GLib.timeout_add(int(900 + random.random() * 2300), go)
        elif st == "wait":
            if r["timer"]: GLib.source_remove(r["timer"]); r["timer"] = None
            r["state"] = "soon"
        elif st == "go":
            r["last"] = int((time.time() - r["t0"]) * 1000)
            if r["best"] is None or r["last"] < r["best"]: r["best"] = r["last"]
            r["state"] = "result"
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
    def _rain_tick(self):
        for i in range(len(self.rain)):
            self.rain[i] += 2.4 + (i % 3)
            if self.rain[i] > self.H + 20: self.rain[i] = random.uniform(-40, 0)
    def _draw_rain(self, cr):
        for i, y in enumerate(self.rain):
            cx = i * 12 + 6
            cr.set_source_rgba(*self.R, 1); cr.arc(cx, y, 2, 0, 6.2832); cr.fill()
            cr.set_source_rgba(0.9, 0.9, 0.9, 0.5); cr.arc(cx, y - 10, 1.4, 0, 6.2832); cr.fill()
            cr.set_source_rgba(0.9, 0.9, 0.9, 0.16); cr.arc(cx, y - 20, 1.1, 0, 6.2832); cr.fill()

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

# ---------- main ----------
def main():
    apply_css()
    widgets = [Clock(), Controls(), System(), Network(), NowPlaying(),
               Status(), Calendar(), Pomodoro(), Notes(), Arcade(),
               Assistant(), Launcher()]
    for w in widgets: w.show_all()
    start_pulse()
    Gtk.main()

if __name__ == "__main__":
    import signal; signal.signal(signal.SIGINT, lambda *a: Gtk.main_quit())
    main()
