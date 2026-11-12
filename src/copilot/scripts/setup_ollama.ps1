# 结算复核助手 · Ollama 一键部署（Windows / PowerShell）
#
# 用法（在 src/copilot 目录下）：
#   powershell -ExecutionPolicy Bypass -File scripts/setup_ollama.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/setup_ollama.ps1 -Model qwen2.5:3b
#
# 它做四件事：检测/安装 Ollama → 启动服务 → 拉模型 → 用 Modelfile 固化管理人设。
# 全程本机运行，不访问任何大模型云服务。

param(
    [string]$Model = "qwen2.5:7b",          # 显存小改用 qwen2.5:3b；CPU 慢用 qwen2.5:0.5b
    [string]$Name  = "settlement-copilot"   # 固化后的模型名，与 config.yaml 的 llm.model 对应
)

$ErrorActionPreference = "Stop"
Write-Host "==> 1/4 检查 Ollama" -ForegroundColor Cyan

# 优先用 PATH 里的 ollama；没有则看默认用户级安装目录
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) {
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $candidate) { $ollama = $candidate }
}
if (-not $ollama) {
    Write-Host "未检测到 Ollama，尝试用 winget 安装（约 700MB，仅首次需要）" -ForegroundColor Yellow
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    $ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (-not (Test-Path $ollama)) {
        Write-Host "安装未完成。可手动下载：https://ollama.com/download/windows" -ForegroundColor Red
        exit 1
    }
}
Write-Host "    ollama: $ollama" -ForegroundColor Green

Write-Host "==> 2/4 确认服务端口 11434" -ForegroundColor Cyan
$up = $false
try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 | Out-Null; $up = $true } catch { }
if (-not $up) {
    # 后台拉起服务；不阻塞当前窗口
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 800
        try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 | Out-Null; $up = $true; break } catch { }
    }
}
if (-not $up) { Write-Host "服务未起来，请手动执行：ollama serve" -ForegroundColor Red; exit 1 }
Write-Host "    服务已就绪：http://127.0.0.1:11434" -ForegroundColor Green

Write-Host "==> 3/4 拉取模型 $Model" -ForegroundColor Cyan
& $ollama pull $Model
if ($LASTEXITCODE -ne 0) { Write-Host "拉取失败，检查网络或换更小的模型（qwen2.5:0.5b）" -ForegroundColor Red; exit 1 }

Write-Host "==> 4/4 用 Modelfile 固化系统提示词 -> $Name" -ForegroundColor Cyan
& $ollama create $Name -f "ollama/Modelfile"
if ($LASTEXITCODE -ne 0) { Write-Host "创建失败：确认 ollama/Modelfile 里 FROM 的模型已拉取" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "完成。把 config.yaml 的 llm.model 改成 $Name，然后重启服务：" -ForegroundColor Green
Write-Host "    python -m uvicorn app.main:app --reload" -ForegroundColor Gray
Write-Host "    curl http://127.0.0.1:8000/llm/ping" -ForegroundColor Gray
