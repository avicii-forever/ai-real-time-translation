# -*- coding: utf-8 -*-
"""打包脚本:把 client/ 打成 Windows onefile 窗口程序 exe(双击即用)。

用法:
    python pack.py

产物:
    dist/AI实时翻译.exe

关键点:
  - --onefile --noconsole:单文件、无控制台窗口(纯 GUI)
  - --collect-all soundcard / paramiko:收集其隐藏依赖(平台 DLL / 动态导入)
  - --paths client:保证 `import config`、`from pipeline import ...` 等解析到 client/ 下
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(ROOT, "client")
NAME = "AI实时翻译"


def main():
    entry = os.path.join(CLIENT, "main.py")
    if not os.path.isfile(entry):
        sys.exit(f"未找到入口:{entry}")

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name", NAME,
        "--paths", CLIENT,
        "--collect-all", "soundcard",
        "--collect-all", "paramiko",
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build"),
        "--specpath", os.path.join(ROOT, "build"),
        entry,
    ]
    print("==>", " ".join(args))
    subprocess.check_call(args, cwd=ROOT)
    out = os.path.join(ROOT, "dist", NAME + ".exe")
    print(f"\n打包完成: {out}")


if __name__ == "__main__":
    main()
