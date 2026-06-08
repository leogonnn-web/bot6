# Compare local and server bot logs every 5 minutes
param(
    [int]$IntervalMinutes = 5,
    [int]$MaxRuns = 12
)

$serverIP = "54.179.1.197"
$keyPath = "C:\Users\leogo\Desktop\triada-key2.pem"

function Get-ServerMetrics {
    $output = ssh -i $keyPath -o StrictHostKeyChecking=no -o ConnectTimeout=10 ubuntu@$serverIP "curl -s http://localhost:9090/metrics | grep -E '^hydra_(scan_cycles_total|orders_total|active_positions|session_profit)'" 2>$null
    return $output
}

function Get-LocalMetrics {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:9090/metrics" -UseBasicParsing -TimeoutSec 5
        $lines = $r.Content -split "`n"
        return ($lines | Where-Object { $_ -match '^hydra_(scan_cycles_total|orders_total|active_positions|session_profit)' }) -join "`n"
    } catch {
        return "ERROR: $_"
    }
}

function Get-ServerErrors {
    $output = ssh -i $keyPath -o StrictHostKeyChecking=no -o ConnectTimeout=10 ubuntu@$serverIP "cd triada && sudo docker compose logs hydra-bot --tail 50 | grep -E 'ERROR|Exception|error'" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
        return "No errors"
    }
    return $output
}

function Get-LocalErrors {
    $lines = Get-Content "C:\Users\leogo\Desktop\bot4-main\bot4-main\bot_local.log" | Select-Object -Last 100
    $errors = $lines | Select-String -Pattern 'ERROR|Exception|error'
    if ($errors) {
        return ($errors | ForEach-Object { $_.Line }) -join "`n"
    }
    return "No errors"
}

Write-Host "=== Starting comparison every $IntervalMinutes minutes (max $MaxRuns runs) ===" -ForegroundColor Green
Write-Host ""

for ($i = 1; $i -le $MaxRuns; $i++) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] Run $i/$MaxRuns" -ForegroundColor Cyan
    Write-Host "---" -ForegroundColor Gray

    Write-Host "Server metrics:" -ForegroundColor Yellow
    $serverMetrics = Get-ServerMetrics
    if ($serverMetrics) {
        Write-Host $serverMetrics
    } else {
        Write-Host "Server unreachable" -ForegroundColor Red
    }

    Write-Host "Local metrics:" -ForegroundColor Yellow
    $localMetrics = Get-LocalMetrics
    Write-Host $localMetrics

    Write-Host "Server errors:" -ForegroundColor Yellow
    $serverErrors = Get-ServerErrors
    if ($serverErrors -eq "No errors") {
        Write-Host $serverErrors -ForegroundColor Green
    } else {
        Write-Host $serverErrors -ForegroundColor Red
    }

    Write-Host "Local errors:" -ForegroundColor Yellow
    $localErrors = Get-LocalErrors
    if ($localErrors -eq "No errors") {
        Write-Host $localErrors -ForegroundColor Green
    } else {
        Write-Host $localErrors -ForegroundColor Red
    }

    Write-Host "---" -ForegroundColor Gray
    Write-Host ""

    if ($i -lt $MaxRuns) {
        Write-Host "Waiting $IntervalMinutes minutes..." -ForegroundColor Gray
        Start-Sleep -Seconds ($IntervalMinutes * 60)
    }
}

Write-Host "=== Comparison finished ===" -ForegroundColor Green
