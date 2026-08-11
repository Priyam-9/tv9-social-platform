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
Write-Host "Adding media_s3_key_override to post_targets..." -ForegroundColor Cyan
$alterSql = "ALTER TABLE post_targets ADD COLUMN IF NOT EXISTS media_s3_key_override VARCHAR(500);"
docker compose exec -T db psql -U $pgUser -d $pgDb -c $alterSql

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: the psql command failed. See output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Restarting API container..." -ForegroundColor Cyan
docker compose restart api

Write-Host ""
Write-Host "Done. Tail the logs to confirm a clean startup:" -ForegroundColor Green
Write-Host "  docker compose logs api --tail=30"
