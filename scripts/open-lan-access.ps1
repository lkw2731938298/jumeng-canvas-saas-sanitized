# Run as Administrator: right-click PowerShell -> "Run as administrator"
# Then: Set-ExecutionPolicy -Scope Process Bypass; .\scripts\open-lan-access.ps1

$ErrorActionPreference = "Stop"

$rules = @(
    @{ DisplayName = "Jumeng Canvas Frontend (TCP 3000)"; Port = 3000 },
    @{ DisplayName = "Jumeng Canvas API (TCP 8001)"; Port = 8001 }
)

foreach ($rule in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $rule.DisplayName -ErrorAction SilentlyContinue
    if ($existing) {
        Enable-NetFirewallRule -DisplayName $rule.DisplayName | Out-Null
        Write-Host "[OK] Rule already exists, enabled: $($rule.DisplayName)"
        continue
    }

    New-NetFirewallRule `
        -DisplayName $rule.DisplayName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort $rule.Port `
        -Profile Private,Domain,Public | Out-Null

    Write-Host "[OK] Created rule: $($rule.DisplayName)"
}

Write-Host ""
Write-Host "LAN access URLs (use your machine IP):"
Write-Host "  Frontend: http://192.168.100.231:3000"
Write-Host "  API:      http://192.168.100.231:8001"
Write-Host ""
Write-Host "Ensure Wi-Fi/Ethernet network is set to Private (not Public)."
