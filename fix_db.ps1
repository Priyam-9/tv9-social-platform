
$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\docker-compose.yml")) {
    Write-Host "ERROR: docker-compose.yml not found in this folder. Run this script from D:\New folder\tv9-publisher" -ForegroundColor Red
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

# Parse postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME
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
Write-Host "Checking docker compose services..." -ForegroundColor Cyan
docker compose ps

Write-Host ""
Write-Host "Adding missing columns to post_targets..." -ForegroundColor Cyan
$alterSql = "ALTER TABLE post_targets ADD COLUMN IF NOT EXISTS language VARCHAR(10); ALTER TABLE post_targets ADD COLUMN IF NOT EXISTS title_override VARCHAR(500); ALTER TABLE post_targets ADD COLUMN IF NOT EXISTS caption_override TEXT;"
docker compose exec -T db psql -U $pgUser -d $pgDb -c $alterSql

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: the psql command failed. See output above for details." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Verifying columns landed..." -ForegroundColor Cyan
docker compose exec -T db psql -U $pgUser -d $pgDb -c "\d post_targets"

Write-Host ""
Write-Host "Restarting API container..." -ForegroundColor Cyan
docker compose restart api

Write-Host ""
Write-Host "Done. Tail the logs to confirm a clean startup:" -ForegroundColor Green
Write-Host "  docker compose logs api --tail=30"