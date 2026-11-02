# Koseli menu - one-time network fix (run via fix-network-access.bat, as administrator).
#  1. Opens TCP ports 8080-8089 in Windows Firewall for the menu server.
#  2. Offers to switch the current Wi-Fi from Public to Private.
#  3. Disables old firewall rules that block Python.
$ErrorActionPreference = 'Continue'
Write-Host ''
Write-Host '  Koseli menu - network access fix' -ForegroundColor Cyan
Write-Host ''

$rule = 'Koseli Menu (TCP 8080-8089)'
if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort 8080-8089 `
        -Action Allow -Profile Private,Public,Domain | Out-Null
    Write-Host '  [ok] Firewall: ports 8080-8089 opened.' -ForegroundColor Green
} else {
    Write-Host '  [ok] Firewall rule already present.' -ForegroundColor Green
}

$public = Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' }
foreach ($p in $public) {
    $answer = Read-Host "  Network '$($p.Name)' is Public. Switch it to Private so phones can connect? (Y/N)"
    if ($answer -match '^[Yy]') {
        Set-NetConnectionProfile -InterfaceIndex $p.InterfaceIndex -NetworkCategory Private
        Write-Host "  [ok] '$($p.Name)' is now Private." -ForegroundColor Green
    }
}

$blocks = Get-NetFirewallRule -Direction Inbound -Action Block -ErrorAction SilentlyContinue |
    Where-Object { $_.Enabled -eq 'True' -and $_.DisplayName -match 'python' }
foreach ($b in $blocks) {
    Disable-NetFirewallRule -Name $b.Name
    Write-Host "  [ok] Disabled blocking rule: $($b.DisplayName)" -ForegroundColor Green
}

Write-Host ''
Write-Host '  Done. Start the menu with start-server.bat.' -ForegroundColor Cyan
