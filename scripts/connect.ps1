# 实时翻译后端连接助手(Windows PowerShell)
# 用法: powershell -ExecutionPolicy Bypass -File connect.ps1
# 功能: 建 SSH 隧道到新节点后端,保持前台运行(Ctrl+C 退出)

$ErrorActionPreference = "Stop"
$hostAlias = "<SSH别名>"

Write-Host "== 连接后端 $hostAlias (隧道 28099) ==" -ForegroundColor Green
Write-Host "按 Ctrl+C 退出"

# 清理已存在的 28099 隧道
$existing = Get-NetTCPConnection -LocalPort 28099 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "端口 28099 已被占用,先清理..." -ForegroundColor Yellow
    # 不强制 kill 已有监听,可能隧道已在跑;先测试
}

ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -N -L 28099:127.0.0.1:28099 $hostAlias
