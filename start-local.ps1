# 本地测试环境一键启动（Windows PowerShell）
# 用法：在 canvas 目录执行 .\start-local.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "==> 检查 Docker..." -ForegroundColor Cyan
try {
  docker info | Out-Null
} catch {
  Write-Host "Docker 未运行，正在启动 Docker Desktop..." -ForegroundColor Yellow
  Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  $ready = $false
  for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 3
    try {
      docker info 2>$null | Out-Null
      if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    } catch {}
    Write-Host "  等待 Docker... ($i)"
  }
  if (-not $ready) {
    Write-Host "Docker 启动超时，请手动打开 Docker Desktop 后重试。" -ForegroundColor Red
    exit 1
  }
}

# 释放旧项目占用的 6380（若存在）
$oldRedis = docker ps --format "{{.Names}}" | Select-String -Pattern "jumengai_comfyui-redis"
if ($oldRedis) {
  Write-Host "==> 停止占用 6380 的旧 Redis: $oldRedis" -ForegroundColor Yellow
  docker stop $oldRedis.ToString().Trim() | Out-Null
}

Write-Host "==> 启动 MySQL / Redis / API / Workers..." -ForegroundColor Cyan
docker compose up -d

Write-Host "==> 前端依赖..." -ForegroundColor Cyan
if (-not (Test-Path "node_modules")) {
  npm install
}

Write-Host ""
Write-Host "后端已启动：" -ForegroundColor Green
Write-Host "  API     http://localhost:8001/docs"
Write-Host "  MySQL   localhost:3306"
Write-Host "  Redis   localhost:6380"
Write-Host ""
Write-Host "请另开终端启动前端：" -ForegroundColor Green
Write-Host "  cd $PSScriptRoot"
Write-Host "  npm run dev"
Write-Host "  然后打开 http://localhost:3000"
Write-Host ""
docker compose ps
