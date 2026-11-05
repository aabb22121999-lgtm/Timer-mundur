"""
Timer Mundur Overlay
--------------------
Timer hitung mundur dengan latar transparan, selalu tampil di paling depan
(di atas aplikasi lain, termasuk pemutar video mode jendela / borderless).

Cara pakai:
    python timer.py            -> default 5 menit
    python timer.py 10:30      -> 10 menit 30 detik
    python timer.py 90s        -> 90 detik

Kontrol:
    Klik kiri + geser   : pindahkan timer
    Panah keyboard      : geser timer 10 px (Shift = 50 px)
    Scroll mouse        : perbesar / perkecil ukuran
    Ctrl + / Ctrl -     : perbesar / perkecil ukuran
    Klik 2x             : atur waktu
    Spasi               : mulai / jeda
    R                   : reset
    F atau F11          : layar penuh (Esc untuk kembali)
    B                   : ganti latar (hilang -> transparan -> pekat)
    Klik kanan          : menu lengkap (latar, suara, warna, preset)
    Esc                 : keluar layar penuh / keluar aplikasi
"""

import json
import math
import os
import platform
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import simpledialog

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".timer_overlay.json")
SYSTEM = platform.system()

BG_NONE, BG_GLASS, BG_SOLID = "none", "glass", "solid"


def parse_time(text):
    """Ubah teks menjadi detik. Contoh: '5:30', '1:05:00', '90s', '10m', '2m30s', '5'."""
    s = text.strip().lower().replace(" ", "")
    if not s:
        return None

    m = re.fullmatch(r"(\d+):(\d+)(?::(\d+))?", s)
    if m:
        a, b, c = m.groups()
        if c is None:  # mm:ss
            return int(a) * 60 + int(b)
        return int(a) * 3600 + int(b) * 60 + int(c)  # hh:mm:ss

    m = re.fullmatch(r"(?:(\d+)(?:j|h))?(?:(\d+)(?:m|menit))?(?:(\d+)(?:s|d|detik))?", s)
    if m and any(m.groups()):
        h, mi, se = (int(x) if x else 0 for x in m.groups())
        return h * 3600 + mi * 60 + se

    if s.isdigit():  # angka saja = menit
        return int(s) * 60
    return None


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


class OverlayTimer:
    COLORS = {
        "Putih": "#FFFFFF",
        "Kuning": "#FFE600",
        "Hijau": "#3CFF6B",
        "Cyan": "#40E0FF",
        "Merah": "#FF4B4B",
    }

    def __init__(self, start_seconds):
        cfg = load_settings()
        self.total = start_seconds if start_seconds is not None else cfg.get("total", 300)
        self.remaining = float(self.total)
        self.running = False
        self.end_at = 0.0
        self.font_size = cfg.get("font_size", 48)
        self.color = cfg.get("color", "#FFFFFF")
        self.text_color = self.color
        self.opacity = min(0.95, max(0.3, cfg.get("opacity", 0.8)))
        self.bg_mode = cfg.get("bg_mode", BG_GLASS)
        if self.bg_mode not in (BG_NONE, BG_GLASS, BG_SOLID):
            self.bg_mode = BG_GLASS
        self.show_cs = bool(cfg.get("show_cs", True))  # tampilkan 1/100 detik
        self.beep_secs = max(0, int(cfg.get("beep_secs", 5)))  # bunyi di N detik terakhir
        self.fullscreen = False
        self._normal_pos = None
        self.flashing = False
        self._last_sec = None
        self._layout_key = None
        self._char_ids = []
        self._outline_ids = []
        self._shown = []

        self.root = tk.Tk()
        self.root.title("Timer")
        self.root.overrideredirect(True)  # tanpa bingkai
        self.root.attributes("-topmost", True)

        # --- latar transparan ---
        if SYSTEM == "Windows":
            self.bg = "#010101"
            self.root.config(bg=self.bg)
            self.root.wm_attributes("-transparentcolor", self.bg)
        elif SYSTEM == "Darwin":
            self.bg = "systemTransparent"
            self.root.config(bg=self.bg)
            self.root.wm_attributes("-transparent", True)
        else:  # Linux: butuh compositor untuk transparansi
            self.bg = "#000000"
            self.root.config(bg=self.bg)
        self.apply_opacity()

        families = ("Arial", "Liberation Sans", "Arimo", "Helvetica")
        self.font_family = "Helvetica"
        try:
            available = set(tkfont.families())
            for fam in families:
                if fam in available:
                    self.font_family = fam
                    break
        except Exception:
            pass

        self.font = tkfont.Font(family=self.font_family, size=self.font_size, weight="bold")
        self.canvas = tk.Canvas(self.root, bg=self.bg, highlightthickness=0, bd=0)
        self.canvas.pack()

        pos = cfg.get("pos")
        if pos:
            self.root.geometry(f"+{pos[0]}+{pos[1]}")
        else:
            self.root.geometry("+80+80")

        self._build_menu()
        self._bind_events()
        self.refresh_font()
        self.root.after(15, self.tick)
        self.root.after(1000, self.keep_on_top)

    # ---------- UI ----------
    def _build_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Mulai / Jeda   (Spasi)", command=self.toggle)
        self.menu.add_command(label="Reset   (R)", command=self.reset)
        self.menu.add_command(label="Atur waktu…   (klik 2x)", command=self.ask_time)
        self.menu.add_command(label="Layar penuh   (F)", command=self.toggle_fullscreen)
        self.menu.add_separator()
        self.menu.add_command(label="Perbesar   (Ctrl +)", command=lambda: self.change_size(+6))
        self.menu.add_command(label="Perkecil   (Ctrl -)", command=lambda: self.change_size(-6))
        color_menu = tk.Menu(self.menu, tearoff=0)
        for name, hexv in self.COLORS.items():
            color_menu.add_command(label=name, command=lambda h=hexv: self.set_color(h))
        self.menu.add_cascade(label="Warna teks", menu=color_menu)

        # latar: hilang / transparan / pekat
        self.bg_var = tk.StringVar(value=self.bg_mode)
        bg_menu = tk.Menu(self.menu, tearoff=0)
        for label, val in (("Hilang (hanya angka)", BG_NONE),
                           ("Hitam transparan", BG_GLASS),
                           ("Hitam pekat", BG_SOLID)):
            bg_menu.add_radiobutton(label=label, variable=self.bg_var, value=val,
                                    command=self.on_bg_menu)
        bg_menu.add_separator()
        opacity_menu = tk.Menu(bg_menu, tearoff=0)
        for label, val in (("40%", 0.4), ("55%", 0.55), ("70%", 0.7), ("80%", 0.8), ("90%", 0.9)):
            opacity_menu.add_command(label=label, command=lambda v=val: self.set_opacity(v))
        bg_menu.add_cascade(label="Kepekatan (mode transparan)", menu=opacity_menu)
        self.menu.add_cascade(label="Latar   (B)", menu=bg_menu)

        self.cs_var = tk.BooleanVar(value=self.show_cs)
        self.menu.add_checkbutton(label="Tampilkan 1/100 detik (.00)", variable=self.cs_var,
                                  command=self.toggle_cs)

        # suara hitungan di detik-detik terakhir
        self.beep_var = tk.IntVar(value=self.beep_secs)
        beep_menu = tk.Menu(self.menu, tearoff=0)
        for label, val in (("Mati", 0), ("3 detik terakhir", 3), ("5 detik terakhir", 5),
                           ("10 detik terakhir", 10)):
            beep_menu.add_radiobutton(label=label, variable=self.beep_var, value=val,
                                      command=self.on_beep_menu)
        beep_menu.add_command(label="Atur sendiri…", command=self.ask_beep_secs)
        beep_menu.add_separator()
        beep_menu.add_command(label="Tes suara", command=lambda: self.play_sound("tick"))
        self.menu.add_cascade(label="Suara hitungan akhir", menu=beep_menu)

        preset = tk.Menu(self.menu, tearoff=0)
        for label, sec in (("30 detik", 30), ("1 menit", 60), ("5 menit", 300),
                           ("10 menit", 600), ("25 menit", 1500), ("1 jam", 3600)):
            preset.add_command(label=label, command=lambda s=sec: self.set_time(s))
        self.menu.add_cascade(label="Preset waktu", menu=preset)
        self.menu.add_separator()
        self.menu.add_command(label="Keluar   (Esc)", command=self.quit)

    def _bind_events(self):
        r = self.root
        for w in (r, self.canvas):
            w.bind("<ButtonPress-1>", self.on_press)
            w.bind("<B1-Motion>", self.on_drag)
            w.bind("<Double-Button-1>", lambda e: self.ask_time())
            w.bind("<MouseWheel>", self.on_wheel)
            w.bind("<Button-4>", lambda e: self.change_size(+4))  # Linux
            w.bind("<Button-5>", lambda e: self.change_size(-4))  # Linux
            w.bind("<Button-3>", self.show_menu)
            if SYSTEM == "Darwin":
                w.bind("<Button-2>", self.show_menu)
        r.bind("<space>", lambda e: self.toggle())
        r.bind("<r>", lambda e: self.reset())
        r.bind("<R>", lambda e: self.reset())
        r.bind("<f>", lambda e: self.toggle_fullscreen())
        r.bind("<F>", lambda e: self.toggle_fullscreen())
        r.bind("<F11>", lambda e: self.toggle_fullscreen())
        r.bind("<b>", lambda e: self.cycle_bg())
        r.bind("<B>", lambda e: self.cycle_bg())
        r.bind("<Escape>", lambda e: self.on_escape())
        r.bind("<Control-plus>", lambda e: self.change_size(+6))
        r.bind("<Control-equal>", lambda e: self.change_size(+6))
        r.bind("<Control-minus>", lambda e: self.change_size(-6))
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            r.bind(f"<{key}>", lambda e, a=dx, b=dy: self.nudge(a * 10, b * 10))
            r.bind(f"<Shift-{key}>", lambda e, a=dx, b=dy: self.nudge(a * 50, b * 50))
        r.protocol("WM_DELETE_WINDOW", self.quit)

    def on_press(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()
        self.root.focus_force()

    def on_drag(self, e):
        if self.fullscreen:
            return
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def nudge(self, dx, dy):
        if self.fullscreen:
            return
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")

    def on_wheel(self, e):
        step = 4 if abs(e.delta) < 120 else 6
        self.change_size(step if e.delta > 0 else -step)

    def show_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def keep_on_top(self):
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.after(1000, self.keep_on_top)

    def on_escape(self):
        if self.fullscreen:
            self.exit_fullscreen()
        else:
            self.quit()

    # ---------- layar penuh ----------
    def toggle_fullscreen(self):
        if self.fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        self._normal_pos = (self.root.winfo_x(), self.root.winfo_y())
        self.fullscreen = True
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{sw}x{sh}+0+0")
        self.apply_opacity()  # layar penuh selalu pekat
        self._layout_key = None
        self.update_display()
        self.root.lift()
        self.root.focus_force()

    def exit_fullscreen(self):
        self.fullscreen = False
        x, y = self._normal_pos or (80, 80)
        self.root.geometry("")  # lepas ukuran tetap, ikuti ukuran isi lagi
        self.root.geometry(f"+{x}+{y}")
        self.apply_opacity()
        self.refresh_font()

    # ---------- latar ----------
    def apply_opacity(self):
        alpha = self.opacity if (self.bg_mode == BG_GLASS and not self.fullscreen) else 1.0
        try:
            self.root.attributes("-alpha", alpha)
        except tk.TclError:
            pass

    def _redraw(self):
        self.apply_opacity()
        self._layout_key = None
        self.update_display()

    def on_bg_menu(self):
        self.bg_mode = self.bg_var.get()
        self._redraw()

    def cycle_bg(self):
        order = [BG_NONE, BG_GLASS, BG_SOLID]
        self.bg_mode = order[(order.index(self.bg_mode) + 1) % 3]
        self.bg_var.set(self.bg_mode)
        self._redraw()

    def set_opacity(self, value):
        self.opacity = value
        self.bg_mode = BG_GLASS
        self.bg_var.set(BG_GLASS)
        self._redraw()

    # ---------- tampilan ----------
    def change_size(self, delta):
        if self.fullscreen:  # di layar penuh ukuran otomatis memenuhi layar
            return
        self.font_size = max(10, min(400, self.font_size + delta))
        self.refresh_font()

    def refresh_font(self):
        self.font.configure(size=self.font_size)
        self._layout_key = None  # paksa gambar ulang panel
        self.update_display()

    def set_color(self, hexv):
        self.color = hexv
        self.set_text_color(hexv)

    def set_text_color(self, hexv):
        self.text_color = hexv
        for cid in self._char_ids:
            self.canvas.itemconfig(cid, fill=hexv)

    def toggle_cs(self):
        self.show_cs = bool(self.cs_var.get())
        self.update_display()

    def format_time(self):
        t = max(self.remaining, 0.0)
        if self.show_cs:
            cs_total = int(t * 100)
            m, s, cs = cs_total // 6000, (cs_total // 100) % 60, cs_total % 100
            return f"{m:02d}:{s:02d}.{cs:02d}"
        total = math.ceil(t - 1e-9)  # dibulatkan ke atas, seperti timer hitung mundur umumnya
        return f"{total // 60:02d}:{total % 60:02d}"

    def _rrect(self, x1, y1, x2, y2, r, **kw):
        """Persegi panjang dengan sudut membulat."""
        r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _slots(self, text):
        """Setiap karakter punya "kotak" lebar tetap (semua angka selebar angka terlebar),
        sehingga posisi teks tidak bergeser/bergetar saat angka berubah."""
        digit_w = max(self.font.measure(str(d)) for d in range(10))
        return [digit_w if ch.isdigit() else self.font.measure(ch) for ch in text]

    def _fit_font_size(self, text, max_w, max_h):
        """Ukuran font terbesar agar teks muat di area max_w x max_h (untuk layar penuh)."""
        self.font.configure(size=100)
        tw = sum(self._slots(text))
        th = self.font.metrics("linespace")
        return max(10, int(min(max_w / tw, max_h / th) * 100))

    def layout(self, text):
        """Gambar latar sesuai mode; hanya diulang jika ukuran/mode berubah."""
        key = (len(text), self.font_size, self.bg_mode, self.fullscreen)
        if key == self._layout_key:
            return
        self._layout_key = key

        c = self.canvas
        c.delete("all")
        self._char_ids, self._outline_ids = [], []

        if self.fullscreen:
            W, H = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            size = self._fit_font_size(text, W * 0.92, H * 0.78)
        else:
            size = self.font_size
        self.font.configure(size=size)

        slots = self._slots(text)
        tw = sum(slots)
        th = self.font.metrics("linespace")
        outline = []  # offset (dx, dy) untuk garis tepi/bayangan teks

        if self.fullscreen:
            c.config(width=W, height=H)
            c.create_rectangle(0, 0, W, H, fill="#000000", outline="")
            cx, cy = W / 2, H / 2
        elif self.bg_mode == BG_NONE:
            # tanpa latar: ukuran jendela pas dengan teks, teks diberi garis tepi gelap
            ro = max(1, round(size / 28))
            pad = 3 * ro
            W, H = tw + 2 * pad, th + 2 * pad
            c.config(width=W, height=H)
            cx, cy = W / 2, H / 2
            outline = [(dx * ro, dy * ro) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
            outline.append((2 * ro, 2 * ro))  # bayangan jatuh
        else:
            sh = max(5, int(size * 0.16))   # tebal bayangan
            dy = max(2, sh // 3)             # bayangan sedikit turun ke bawah
            px = int(size * 0.40)
            py = int(size * 0.12)
            radius = int(size * 0.22)
            W = tw + 2 * px + 2 * sh
            H = th + 2 * py + 2 * sh + dy
            x1, y1, x2, y2 = sh, sh, W - sh, H - sh - dy
            c.config(width=W, height=H)
            # bayangan: lapisan luar lebih terang -> memudar keluar (hindari #010101 = warna kunci)
            for i in range(sh, 0, -1):
                g = 2 + int(0x20 * i / sh)
                self._rrect(x1 - i, y1 - i + dy, x2 + i, y2 + i + dy, radius + i,
                            fill=f"#{g:02x}{g:02x}{g:02x}", outline="")
            self._rrect(x1, y1, x2, y2, radius, fill="#000000", outline="")
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

        x = cx - tw / 2
        centers = []
        for w in slots:
            centers.append(x + w / 2)
            x += w
        for ox, oy in outline:  # lapisan belakang dulu
            self._outline_ids.append([
                c.create_text(px_ + ox, cy + oy, text="", font=self.font, fill="#000000",
                              anchor="center") for px_ in centers])
        for px_ in centers:
            self._char_ids.append(c.create_text(px_, cy, text="", font=self.font,
                                                fill=self.text_color, anchor="center"))
        self._shown = [None] * len(text)

    def update_display(self):
        text = self.format_time()
        self.layout(text)
        for i, ch in enumerate(text):
            if self._shown[i] != ch:  # hanya perbarui karakter yang berubah
                self.canvas.itemconfig(self._char_ids[i], text=ch)
                for layer in self._outline_ids:
                    self.canvas.itemconfig(layer[i], text=ch)
                self._shown[i] = ch

    # ---------- logika timer ----------
    def tick(self):
        if self.running:
            self.remaining = self.end_at - time.perf_counter()
            if self.remaining <= 0:
                self.remaining = 0
                self.running = False
                self.update_display()
                self.finished()
            else:
                self.check_countdown_sound()
                self.update_display()
        self.root.after(15, self.tick)

    def check_countdown_sound(self):
        """Bunyi tiap detik pada N detik terakhir (N bisa diatur dari menu)."""
        sec = math.ceil(self.remaining - 1e-9)
        if sec != self._last_sec:
            self._last_sec = sec
            if self.beep_secs > 0 and 1 <= sec <= self.beep_secs:
                self.play_sound("tick")

    def toggle(self):
        if self.flashing:
            return
        if self.running:
            self.running = False
        else:
            if self.remaining <= 0:
                self.remaining = float(self.total)
            # lanjut dari jeda: jangan bunyi ulang untuk detik yang sama
            fresh = self.remaining >= self.total - 1e-6
            self._last_sec = None if fresh else math.ceil(self.remaining - 1e-9)
            self.end_at = time.perf_counter() + self.remaining
            self.running = True

    def reset(self):
        self.running = False
        self.flashing = False
        self._last_sec = None
        self.remaining = float(self.total)
        self.set_text_color(self.color)
        self.update_display()

    def set_time(self, seconds):
        self.total = max(1, int(seconds))
        self.reset()

    def ask_time(self):
        was_running = self.running
        self.running = False
        remaining_backup = self.remaining
        answer = simpledialog.askstring(
            "Atur waktu",
            "Masukkan waktu:\n"
            "  5:30   = 5 menit 30 detik\n"
            "  90s    = 90 detik\n"
            "  10m    = 10 menit\n"
            "  1:00:00 = 1 jam\n"
            "  5      = 5 menit",
            parent=self.root,
        )
        if answer is not None:
            sec = parse_time(answer)
            if sec and sec > 0:
                self.set_time(sec)
                return
        # batal / tidak valid -> lanjutkan seperti semula
        self.remaining = remaining_backup
        if was_running:
            self.end_at = time.perf_counter() + self.remaining
            self.running = True

    # ---------- suara ----------
    def on_beep_menu(self):
        self.beep_secs = int(self.beep_var.get())

    def ask_beep_secs(self):
        n = simpledialog.askinteger(
            "Suara hitungan akhir",
            "Bunyikan beep setiap detik pada berapa detik terakhir?\n(0 = tanpa suara)",
            parent=self.root, initialvalue=self.beep_secs, minvalue=0, maxvalue=3600,
        )
        if n is not None:
            self.beep_secs = n
            self.beep_var.set(n)

    def play_sound(self, kind):
        """kind: 'tick' = bunyi pendek tiap detik, 'end' = alarm waktu habis."""
        try:
            if SYSTEM == "Windows":
                import winsound
                freq, ms = (1200, 100) if kind == "tick" else (1500, 500)
                threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()
            elif SYSTEM == "Darwin":
                import subprocess
                name = "Tink" if kind == "tick" else "Glass"
                subprocess.Popen(["afplay", f"/System/Library/Sounds/{name}.aiff"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.root.bell()
        except Exception:
            pass

    def finished(self):
        self.flashing = True
        self.play_sound("end")
        self.flash(0)

    def flash(self, n):
        if not self.flashing:
            return
        if n >= 24:  # ~12 detik lalu berhenti berkedip
            self.flashing = False
            self.set_text_color("#FF4B4B")
            return
        self.set_text_color("#FF4B4B" if n % 2 == 0 else self.color)
        if n % 6 == 0 and n > 0:
            self.play_sound("end")
        self.root.after(500, lambda: self.flash(n + 1))

    def quit(self):
        if self.fullscreen and self._normal_pos:
            x, y = self._normal_pos
        else:
            x, y = self.root.winfo_x(), self.root.winfo_y()
        save_settings({
            "total": self.total,
            "font_size": self.font_size,
            "color": self.color,
            "opacity": self.opacity,
            "bg_mode": self.bg_mode,
            "show_cs": self.show_cs,
            "beep_secs": self.beep_secs,
            "pos": [x, y],
        })
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    start = parse_time(sys.argv[1]) if len(sys.argv) > 1 else None
    OverlayTimer(start).run()
