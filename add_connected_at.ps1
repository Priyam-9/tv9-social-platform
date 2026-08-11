# add_connected_at.ps1
# Adds the connected_at column to social_accounts, used to warn about
# YouTube's 7-day Testing-mode refresh token expiry.
#
# Run from the project ROOT (D:\New folder\tv9-publisher):
#   .\add_connected_at.ps1

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\docker-compose.yml")) {
    Write-Host "ERROR: docker-compose.yml not found in this folder. Run this from D:\New folder\tv9-publisher" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".\.env")) {
    Write-Host "ERROR: .env not found in this folder." -ForegroundColor Red
    exit 1
}

$envContent = Get-Content ".\.env"
$dbUrlLine = $envContent | Select-String "^DATABASE_URL="
if (-not $dbUrlLine) {
    Write-Host "ERROR: Could not find DATABASE_URL in .env" -ForegroundColor Red
    exit 1
}

$dbUrl = $dbUrlLine.ToString() -replace "^DATABASE_URL=", ""
if ($dbUrl -match "://([^:]+):([^@]+)@[^/]+/(.+)$") {
    $pgUser = $matches[1]
    $pgDb = $matches[3]
} else {
    Write-Host "ERROR: Could not parse DATABASE_URL: $dbUrl" -ForegroundColor Red
    exit 1
}

Write-Host "Using DB user $pgUser, database $pgDb" -ForegroundColor Cyan

Write-Host ""
Write-Host "Adding connected_at to social_accounts..." -ForegroundColor Cyan
$alterSql = "ALTER TABLE social_accounts ADD COLUMN IF NOT EXISTS connected_at TIMESTAMPTZ;"
docker compose exec -T db psql -U $pgUser -d $pgDb -c $alterSql

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: the psql command failed. See output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Backfilling connected_at for existing accounts to now, so the warning banner has a baseline instead of showing every account as unknown-age..." -ForegroundColor Cyan
$backfillSql = "UPDATE social_accounts SET connected_at = NOW() WHERE connected_at IS NULL;"
docker compose exec -T db psql -U $pgUser -d $pgDb -c $backfillSql

Write-Host ""
Write-Host "Verifying..." -ForegroundColor Cyan
docker compose exec -T db psql -U $pgUser -d $pgDb -c "\d social_accounts"

Write-Host ""
Write-Host "Restarting API container..." -ForegroundColor Cyan
docker compose restart api

Write-Host ""
Write-Host "Done. IMPORTANT: this backfill only sets a baseline of 'now' - it does" -ForegroundColor Yellow
Write-Host "NOT know your real original connection date. Reconnect each YouTube" -ForegroundColor Yellow
Write-Host "account via /auth/youtube/login once more so connected_at reflects the" -ForegroundColor Yellow
Write-Host "actual OAuth grant time going forward (see the auth callback change below)." -ForegroundColor Yellow