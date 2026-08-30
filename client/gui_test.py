# -*- coding: utf-8 -*-
"""GUI 端到端测试 v2:打印日志诊断。"""
import sys, time, threading, winsound
sys.path.insert(0, '.')
import tkinter as tk
import main as gui_main

path = r'E:\ai-real-time-translation\audio_test\edge_long_24k_local.wav'
stop_play = threading.Event()
def play_loop():
    while not stop_play.is_set():
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        time.sleep(9)
threading.Thread(target=play_loop, daemon=True).start()
time.sleep(1)  # 确保播放开始

root = tk.Tk()
app = gui_main.App(root)
result = {"got": ""}
deadline = time.time() + 75

def poll():
    txt = app.trans_text.get('1.0', 'end').strip()
    logs = app.log_text.get('1.0', 'end').strip()
    if txt:
        result["got"] = txt
        root.quit()
    elif time.time() > deadline:
        result["logs"] = logs
        root.quit()
    else:
        root.after(500, poll)

app._start()
root.after(500, poll)
root.mainloop()

stop_play.set()
print('== 结果 ==')
print('译文:', result.get("got") or "(无)")
print('--- 日志 ---')
print(result.get("logs", "")[-1500:])
app._on_close()
