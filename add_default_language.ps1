# add_default_language.ps1
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
Write-Host "Adding default_language to social_accounts..." -ForegroundColor Cyan
$alterSql = "ALTER TABLE social_accounts ADD COLUMN IF NOT EXISTS default_language VARCHAR(10);"
docker compose exec -T db psql -U $pgUser -d $pgDb -c $alterSql

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: the psql command failed. See output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Restarting API container so the new column is picked up..." -ForegroundColor Cyan
docker compose restart api

Write-Host ""
Write-Host "Here are your current accounts - note the id and platform for each channel you want to set a default language for:" -ForegroundColor Yellow
docker compose exec -T db psql -U $pgUser -d $pgDb -c "SELECT id, platform, account_name, default_language FROM social_accounts ORDER BY platform, account_name;"

Write-Host ""
Write-Host "Now set each channel's default language. Example (edit the id and language code, then run this line yourself for each real channel):" -ForegroundColor Yellow
Write-Host ""
Write-Host "  docker compose exec -T db psql -U $pgUser -d $pgDb -c ""UPDATE social_accounts SET default_language = 'hi' WHERE id = 'PASTE-ACCOUNT-ID-HERE';""" -ForegroundColor Green
Write-Host ""
Write-Host "Language codes in use: hi, te, bn, mr, ta, kn, ml, en" -ForegroundColor Yellow
