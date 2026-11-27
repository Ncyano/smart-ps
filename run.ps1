# 一键启动（Windows PowerShell）
# 用法：右键「使用 PowerShell 运行」，或在终端执行 .\run.ps1
$ErrorActionPreference = "Stop"
try { chcp 65001 | Out-Null } catch {}
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$env:PYTHONUTF8 = "1"

Set-Location $PSScriptRoot
Write-Host "启动智能 PS ..." -ForegroundColor Cyan
python -m server.app.main
