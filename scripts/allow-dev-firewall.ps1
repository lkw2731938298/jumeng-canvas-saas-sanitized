# 放行 Next.js 开发端口，供局域网其他设备访问
# 必须以管理员身份运行 PowerShell

$ErrorActionPreference = "Stop"

$rules = @(
  @{ Name = "Jumeng Canvas Dev TCP 3000"; Port = 3000 },
  @{ Name = "Jumeng Canvas API TCP 8001"; Port = 8001 }
)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
  Write-Host "错误: 需要管理员权限。请右键 PowerShell -> 以管理员身份运行，再执行 npm run lan:firewall" -ForegroundColor Red
  exit 1
}

foreach ($rule in $rules) {
  $existing = Get-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
  if ($existing) {
    Write-Host "规则已存在: $($rule.Name)"
    continue
  }
  New-NetFirewallRule `
    -DisplayName $rule.Name `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $rule.Port `
    -Profile Private, Domain | Out-Null
  Write-Host "已添加防火墙规则: $($rule.Name) (端口 $($rule.Port))"
}

Write-Host "完成。其他设备请访问 config/lan.env 中配置的地址，例如:"
$lanHost = (node "$PSScriptRoot\detect-lan.mjs" 2>$null)
if ($lanHost) {
  Write-Host "  http://${lanHost}:3000/projects"
} else {
  Write-Host "  http://192.168.1.100:3000/projects"
}
