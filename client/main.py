# -*- coding: utf-8 -*-
"""实时翻译桌面客户端(GUI) —— 现代深色主题。

用法:
    python main.py

线程安全:后台线程(pipeline)不直接碰 tkinter,只把事件 push 到 queue;
主线程每 100ms poll queue 更新 UI。
"""
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, ".")

from pipeline import Pipeline
from pipeline_ws import WSPipeline
from audio.capture import LoopbackCapture, MicCapture, list_devices
from config import GUI_TITLE, GUI_SIZE
from subtitle_overlay import SubtitleOverlay
import tunnel

# VAD 灵敏度档位 → VAD_THRESHOLD(能量阈值,越低越灵敏)
_VAD_SENS = {"高": 0.005, "中": 0.012, "低": 0.03}

# ============================================================
# 主题样式(现代深色)
# ============================================================
BG          = "#0f1419"   # 主背景
BG_CARD     = "#1a212b"   # 卡片背景
BG_INPUT    = "#232c38"   # 输入框背景
FG          = "#e6edf3"   # 主文字
FG_DIM      = "#8b949e"   # 次要文字
ACCENT      = "#2dd4bf"   # 青绿主色
ACCENT_DARK = "#0d9488"
RED         = "#f87171"
GREEN       = "#4ade80"
BLUE        = "#60a5fa"
BORDER      = "#2d3a47"
FONT        = "Microsoft YaHei UI"

STATE_COLORS = {
    "空闲":   "#8b949e",
    "采集中": GREEN,
    "翻译中": BLUE,
    "收尾中": BLUE,
    "启动中": BLUE,
    "已停止": "#8b949e",
    "错误":   RED,
}


def _style(theme):
    theme.configure(
        "Card.TFrame", background=BG_CARD,
    )
    theme.configure(
        "Bg.TFrame", background=BG,
    )
    theme.configure(
        "TLabel", background=BG_CARD, foreground=FG, font=(FONT, 10),
    )
    theme.configure(
        "Dim.TLabel", background=BG_CARD, foreground=FG_DIM, font=(FONT, 9),
    )
    theme.configure(
        "CardTitle.TLabel", background=BG_CARD, foreground=ACCENT,
        font=(FONT, 10, "bold"),
    )
    theme.configure(
        "Big.TLabel", background=BG, foreground=FG, font=(FONT, 12, "bold"),
    )
    theme.configure(
        "TButton", background=ACCENT_DARK, foreground="#ffffff",
        font=(FONT, 10, "bold"), borderwidth=0,
    )
    theme.map("TButton",
              background=[("active", ACCENT), ("pressed", ACCENT_DARK)],
              foreground=[("disabled", "#9ca3af")])
    theme.configure(
        "TRadiobutton", background=BG_CARD, foreground=FG, font=(FONT, 10),
    )
    theme.map("TRadiobutton", background=[("active", BG_CARD)])
    theme.configure(
        "TCheckbutton", background=BG_CARD, foreground=FG, font=(FONT, 10),
    )
    theme.map("TCheckbutton", background=[("active", BG_CARD)])
    theme.configure(
        "TCombobox", background=BG_INPUT, foreground=FG, fieldbackground=BG_INPUT,
        arrowcolor=FG_DIM, font=(FONT, 10),
    )
    theme.map("TCombobox",
              fieldbackground=[("readonly", BG_INPUT)],
              foreground=[("readonly", FG)],
              background=[("readonly", BG_INPUT)])


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(GUI_TITLE)
        self.root.geometry(GUI_SIZE)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.minsize(720, 560)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        _style(style)

        self.pipe = None
        self._tunnel = None
        self.loopbacks = []
        self.mics = []
        self.events = queue.Queue()
        self.tts_var = tk.BooleanVar(value=True)
        self.mode_var = tk.StringVar(value="duplex")
        self.src_var = tk.StringVar(value="loopback")
        self.status_var = tk.StringVar(value="空闲")
        self.lang_hint_var = tk.StringVar(value="")
        self.device_var = tk.StringVar()
        self.src_lang_var = tk.StringVar(value="中文")
        self.tgt_lang_var = tk.StringVar(value="English")
        self.slice_var = tk.StringVar(value="1.5")   # 切片时长(s)
        self.vad_var = tk.StringVar(value="中")      # VAD 灵敏度

        # 半透明字幕弹窗
        self.overlay = SubtitleOverlay(self.root)
        self.overlay_var = tk.BooleanVar(value=False)
        self.overlay_lock_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._refresh_devices()
        self._start_tunnel()
        self._poll_events()

    # ---- UI ----
    def _card(self, parent, title):
        """卡片容器:圆角感 + 标题行。"""
        frame = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER,
                         highlightthickness=1, bd=0)
        if title:
            tk.Label(frame, text=title, bg=BG_CARD, fg=ACCENT,
                     font=(FONT, 10, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        return frame

    def _build_ui(self):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=14, pady=14)

        # ============ 顶部:标题 + 状态 ============
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x", pady=(0, 10))
        tk.Label(header, text="AI 实时翻译", bg=BG, fg=ACCENT,
                 font=(FONT, 18, "bold")).pack(side="left")
        tk.Label(header, text="语音转语音 · 全双工流式", bg=BG, fg=FG_DIM,
                 font=(FONT, 10)).pack(side="left", padx=10, pady=(4, 0))
        # 状态指示:圆点 + 文字
        self.dot = tk.Canvas(header, width=16, height=16, bg=BG,
                             highlightthickness=0)
        self.dot.pack(side="right", padx=(8, 4))
        self.dot_id = self.dot.create_oval(3, 3, 13, 13, fill=STATE_COLORS["空闲"],
                                           outline="")
        ttk.Label(header, textvariable=self.status_var, style="Big.TLabel"
                  ).pack(side="right")
        # 后端连接状态(最左侧)
        self.conn_label = tk.Label(header, text="后端: 连接中…", bg=BG, fg=FG_DIM,
                                   font=(FONT, 10))
        self.conn_label.pack(side="right", padx=(0, 12))

        # ============ 设置卡 ============
        card = self._card(outer, "设置")
        card.pack(fill="x", pady=(0, 10))

        # 输入源
        src_row = tk.Frame(card, bg=BG_CARD)
        src_row.pack(fill="x", padx=14, pady=3)
        ttk.Label(src_row, text="输入源").pack(side="left")
        ttk.Radiobutton(src_row, text="系统声音", variable=self.src_var,
                        value="loopback", command=self._refresh_devices
                        ).pack(side="left", padx=(14, 0))
        ttk.Radiobutton(src_row, text="麦克风", variable=self.src_var,
                        value="mic", command=self._refresh_devices
                        ).pack(side="left", padx=6)
        self.device_cb = ttk.Combobox(src_row, textvariable=self.device_var,
                                      state="readonly", width=28)
        self.device_cb.pack(side="right")

        # 语言对
        lang_row = tk.Frame(card, bg=BG_CARD)
        lang_row.pack(fill="x", padx=14, pady=3)
        ttk.Label(lang_row, text="语言").pack(side="left")
        self._lang_cb(lang_row, self.src_lang_var).pack(side="left", padx=(14, 0))
        tk.Label(lang_row, text="→", bg=BG_CARD, fg=ACCENT,
                 font=(FONT, 12, "bold")).pack(side="left", padx=6)
        self._lang_cb(lang_row, self.tgt_lang_var).pack(side="left")
        ttk.Label(lang_row, textvariable=self.lang_hint_var,
                  style="Dim.TLabel").pack(side="right")

        # 参数:切片时长 / VAD 灵敏度(运行时写回 config)
        param_row = tk.Frame(card, bg=BG_CARD)
        param_row.pack(fill="x", padx=14, pady=3)
        ttk.Label(param_row, text="切片时长").pack(side="left")
        ttk.Combobox(param_row, textvariable=self.slice_var,
                     values=("1.0", "1.5", "2.0"), state="readonly",
                     width=6).pack(side="left", padx=(14, 0))
        ttk.Label(param_row, text="s").pack(side="left")
        ttk.Label(param_row, text="VAD 灵敏度").pack(side="left", padx=(16, 0))
        ttk.Combobox(param_row, textvariable=self.vad_var,
                     values=("高", "中", "低"), state="readonly",
                     width=6).pack(side="left", padx=(14, 0))

        # 模式 + 语音输出 + 按钮
        opt_row = tk.Frame(card, bg=BG_CARD)
        opt_row.pack(fill="x", padx=14, pady=(3, 12))
        ttk.Label(opt_row, text="模式").pack(side="left")
        ttk.Combobox(opt_row, textvariable=self.mode_var, values=("duplex", "http"),
                     state="readonly", width=8).pack(side="left", padx=(14, 0))
        ttk.Checkbutton(opt_row, text="语音输出", variable=self.tts_var
                        ).pack(side="left", padx=16)
        self.btn = tk.Button(opt_row, text="▶  开始", command=self._toggle,
                             bg=ACCENT_DARK, fg="#ffffff", activebackground=ACCENT,
                             activeforeground="#ffffff", bd=0, relief="flat",
                             font=(FONT, 11, "bold"), padx=22, pady=6,
                             cursor="hand2")
        self.btn.pack(side="right")

        # 字幕弹窗(半透明置顶,可拖动/锁定穿透)
        sub_row = tk.Frame(card, bg=BG_CARD)
        sub_row.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Checkbutton(sub_row, text="字幕弹窗", variable=self.overlay_var,
                        command=self._toggle_overlay).pack(side="left")
        ttk.Checkbutton(sub_row, text="锁定(点击穿透)", variable=self.overlay_lock_var,
                        command=self._toggle_overlay_lock).pack(side="left", padx=16)

        # ============ 译文卡 ============
        card = self._card(outer, "译文")
        card.pack(fill="both", expand=True, pady=(0, 10))
        self.trans_text = tk.Text(card, bg=BG_INPUT, fg=FG, insertbackground=ACCENT,
                                  font=(FONT, 13), wrap="word", bd=0,
                                  highlightthickness=0, padx=16, pady=12)
        self.trans_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.trans_text.config(state="disabled")

        # ============ 日志卡 ============
        card = self._card(outer, "日志")
        card.pack(fill="x")
        self.log_text = tk.Text(card, bg=BG_INPUT, fg=FG_DIM,
                                font=("Consolas", 9), wrap="word", bd=0,
                                highlightthickness=0, height=6, padx=10, pady=6)
        self.log_text.pack(fill="both", padx=10, pady=(0, 10))
        self.log_text.config(state="disabled")

    def _lang_cb(self, parent, var):
        import config as cfg
        return ttk.Combobox(parent, textvariable=var,
                            values=list(cfg.LANG_NAMES.keys()),
                            state="readonly", width=9)

    # ---- 设备 ----
    def _refresh_devices(self):
        try:
            self.loopbacks, self.mics = list_devices()
        except Exception as e:
            self._push("log", f"枚举设备失败: {e}")
            self.loopbacks, self.mics = [], []
        if self.src_var.get() == "loopback":
            names = [d.name for d in self.loopbacks]
        else:
            names = [d.name for d in self.mics]
        self.device_cb["values"] = names
        if names:
            self.device_cb.current(0)

    def _selected_device(self):
        idx = self.device_cb.current()
        pool = self.loopbacks if self.src_var.get() == "loopback" else self.mics
        if 0 <= idx < len(pool):
            return pool[idx]
        return None

    # ---- 线程安全 ----
    def _push(self, kind, payload):
        self.events.put((kind, payload))

    def _poll_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._set_status(payload)
                elif kind == "log":
                    self._append(self.log_text, payload)
                elif kind == "translation":
                    self._set_translation(payload)
                elif kind == "conn":
                    self._set_conn(*payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _set_status(self, s):
        self.status_var.set(s)
        color = STATE_COLORS.get(s, STATE_COLORS["空闲"])
        self.dot.itemconfig(self.dot_id, fill=color)

    def _append(self, text_widget, s):
        text_widget.config(state="normal")
        text_widget.insert("end", s + "\n")
        text_widget.see("end")
        text_widget.config(state="disabled")

    def _set_translation(self, t):
        self.trans_text.config(state="normal")
        self.trans_text.delete("1.0", "end")
        self.trans_text.insert("end", t)
        self.trans_text.config(state="disabled")
        self.overlay.set_text(t)

    # ---- 后端连接(SSH 隧道一键连接 + 保活)----
    def _start_tunnel(self):
        """后台线程建隧道(带保活),避免阻塞 GUI;结果通过 queue 回传。"""
        t = tunnel.Tunnel()
        self._tunnel = t

        def on_state(s):
            color = (GREEN if s == "已连接"
                     else RED if ("失败" in s or "超时" in s)
                     else FG_DIM)
            self._push("conn", (f"后端: {s}", color))
            self._push("log", f"[连接] {s}")

        def worker():
            on_state("连接中…")
            if t.start():
                if t.wait_ready():
                    on_state("已连接")
                    # 保活:后端/隧道掉线后自动重建
                    t.monitor(interval=5, on_state=on_state)
                else:
                    on_state(t.state)
            else:
                on_state(t.state)

        threading.Thread(target=worker, daemon=True).start()

    def _set_conn(self, text, color):
        self.conn_label.config(text=text, fg=color)

    # ---- 字幕弹窗 ----
    def _toggle_overlay(self):
        if self.overlay_var.get():
            self.overlay.open()
            self.overlay.set_locked(self.overlay_lock_var.get())
        else:
            self.overlay.close()

    def _toggle_overlay_lock(self):
        self.overlay.set_locked(self.overlay_lock_var.get())

    # ---- 控制 ----
    def _toggle(self):
        if self.pipe and self.pipe._thread and self.pipe._thread.is_alive():
            self._stop()
        else:
            self._start()

    def _start(self):
        if self.src_var.get() == "loopback":
            src = LoopbackCapture(self._selected_device())
        else:
            src = MicCapture(self._selected_device())

        import config as cfg
        cfg.TTS_PLAY = self.tts_var.get()
        cfg.SLICE_SECONDS = float(self.slice_var.get())
        cfg.VAD_THRESHOLD = _VAD_SENS.get(self.vad_var.get(), cfg.VAD_THRESHOLD)

        src_lang = self.src_lang_var.get()
        tgt_lang = self.tgt_lang_var.get()
        vc, ap = cfg.make_prompts(src_lang, tgt_lang)
        cfg.VOICE_CLONE_PROMPT, cfg.ASSISTANT_PROMPT = vc, ap
        self.lang_hint_var.set(f"{src_lang} → {tgt_lang}")

        pipe_cls = WSPipeline if self.mode_var.get() == "duplex" else Pipeline
        kwargs = dict(
            source=src,
            on_status=lambda s: self._push("status", s),
            on_translation=lambda t: self._push("translation", t),
            on_error=lambda e: self._push("log", f"[错误] {e}"),
            on_log=lambda s: self._push("log", s),
        )
        # 语音输出(配音)只在 duplex 路径实现;http 路径仍走老的 USE_TTS 配置
        if self.mode_var.get() == "duplex":
            kwargs["dub"] = self.tts_var.get()
        self.pipe = pipe_cls(**kwargs)
        threading.Thread(target=self.pipe.start, daemon=True).start()
        self.btn.config(text="■  停止", bg=RED, activebackground=RED)
        self._set_translation("")

    def _stop(self):
        if self.pipe:
            self.pipe.stop()
        self.btn.config(text="▶  开始", bg=ACCENT_DARK, activebackground=ACCENT)
        self._set_status("已停止")

    def _on_close(self):
        self._stop()
        if self.overlay is not None:
            self.overlay.close()
        if self._tunnel is not None:
            self._tunnel.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
