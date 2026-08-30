# 打包成 exe · 实现计划

> 状态:待批准
> 日期:2026-08-26

## 目标

把客户端打包成**双击即用**的 Windows exe,无需安装 Python、无需手动建隧道。

## 现状

- Python 3.13 + **PyInstaller 6.21 已装**,所有依赖(soundcard/paramiko/numpy/websocket/tkinter/winsound)可用
- 客户端代码:`client/`(入口 `main.py`,GUI + duplex 流式翻译)
- 客户端连 `127.0.0.1:28099` —— **现在依赖外部手动 SSH 隧道**

## 核心设计:exe 内置"一键连接"

用户要"直接打开应用",所以 exe 必须**自己解决隧道**:

1. **启动时自动建 SSH 隧道**(调用 Windows 自带 OpenSSH `C:\Windows\System32\OpenSSH\ssh.exe`)
   - 检查 28099 是否已通 → 通则直接用
   - 不通 → 自动 `ssh -N -L 28099:127.0.0.1:28099 <SSH别名>`(后台子进程)
   - 等待 `/health` OK → 界面就绪
2. **界面显示连接状态**:连接中(建隧道)/ 已连接 / 连接失败(给出错误提示)
3. **退出时关闭隧道子进程**(不留孤儿进程)

**前提**:exe 运行机必须:
- 有 `C:\Windows\System32\OpenSSH\ssh.exe`(Win10+ 自带)
- 有 `C:\Users\<用户>\.ssh\config`(含 <SSH别名> + 网关配置)
- 有私钥 `id_rsa`(已在用户 .ssh 下)
- 能访问 OpenLibing 网关(网络)

## 打包步骤

1. **新增 `client/tunnel.py`**:封装"检测/建/关 SSH 隧道"(子进程 + health 轮询)
2. **改 `main.py`**:启动时调 tunnel 建立连接,状态栏显示连接状态;失败则弹窗提示
3. **写 `pack.py`(打包脚本)**:PyInstaller onefile + windowed
   - `--onefile --noconsole --name AI实时翻译`
   - `--collect-all soundcard --collect-all paramiko`(隐藏依赖)
   - 入口 `main.py`,排除测试脚本
4. **打包 → 测试 exe**:双击启动 → 自动建隧道 → 翻译链路验证
5. **(可选)生成图标 .ico**

## 风险与对策

| 风险 | 对策 |
|---|---|
| PyInstaller 漏打包 soundcard 的 DLL/依赖 | `--collect-all soundcard`,打包后实测采集 |
| paramiko 动态导入 | `--collect-all paramiko` + 实测 SFTP 上传 |
| ssh.exe 路径差异 | 自动探测:`C:\Windows\System32\OpenSSH\ssh.exe` → PATH 兜底 |
| 隧道建好但后端服务挂了 | health 检测失败 → 提示用户"后端服务未运行",给重启指引 |
| onefile 启动慢 | 可接受(首次解压 ~几秒);或改 onedir |

## 验证

1. `pack.py` 成功产出 exe
2. 双击 exe → 自动连隧道 → 界面出现"已连接"
3. 播放中文 → 译文滚动 → 全程无手动 SSH
4. 退出 exe → 隧道子进程被关闭(无残留)
