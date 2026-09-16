# add_default_language.ps1
$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\docker-compose.yml")) {
    Write-Host "ERROR: docker-compose.yml not found in this folder. Run this from the project root." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".\.env")) {
    Write-Host "ERROR: .env not found in this folder." -ForegroundColor Red
    exit 1
}

# Keep this list synchronized with app/services/content_limits.py.
$supportedLanguages = [ordered]@{
    "en" = "English"
    "hi" = "Hindi"
    "te" = "Telugu"
    "bn" = "Bengali"
    "mr" = "Marathi"
    "ta" = "Tamil"
    "kn" = "Kannada"
    "ml" = "Malayalam"
}

$envContent = Get-Content ".\.env"
$dbUrlLine = $envContent | Select-String "^DATABASE_URL=" | Select-Object -First 1

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
Write-Host "Checking social_accounts.default_language..." -ForegroundColor Cyan

# The column already belongs to the SocialAccount model. IF NOT EXISTS keeps
# this legacy local-development helper safe to run against an older database.
$alterSql = "ALTER TABLE social_accounts ADD COLUMN IF NOT EXISTS default_language VARCHAR(10);"
docker compose exec -T db psql -U $pgUser -d $pgDb -c $alterSql

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: the psql command failed. See output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Current social accounts:" -ForegroundColor Yellow
docker compose exec -T db psql -U $pgUser -d $pgDb -c "SELECT id, platform, account_name, default_language FROM social_accounts ORDER BY platform, account_name;"

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: could not read social_accounts." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Supported language codes:" -ForegroundColor Yellow
foreach ($entry in $supportedLanguages.GetEnumerator()) {
    Write-Host ("  {0} = {1}" -f $entry.Key, $entry.Value)
}

Write-Host ""
Write-Host "IMPORTANT: default_language must be one of the supported codes above."
Write-Host "Leave the script here if you only want to inspect accounts."
Write-Host ""

$setDefaults = Read-Host "Do you want to set a default language now? (y/n)"

if ($setDefaults -notmatch "^(y|yes)$") {
    Write-Host ""
    Write-Host "No changes made to default_language." -ForegroundColor Green
    exit 0
}

while ($true) {
    $accountId = (Read-Host "Enter the social account UUID (blank to finish)").Trim()

    if (-not $accountId) {
        break
    }

    if ($accountId -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$') {
        Write-Host "Invalid UUID format. Please try again." -ForegroundColor Red
        continue
    }

    $language = (Read-Host "Enter language code (en/hi/te/bn/mr/ta/kn/ml)").Trim().ToLowerInvariant()

    if (-not $supportedLanguages.Contains($language)) {
        Write-Host "Unsupported language '$language'. Choose one of: $($supportedLanguages.Keys -join ', ')" -ForegroundColor Red
        continue
    }

    $escapedAccountId = $accountId.Replace("'", "''")
    $escapedLanguage = $language.Replace("'", "''")

    $updateSql = "UPDATE social_accounts SET default_language = '$escapedLanguage' WHERE id = '$escapedAccountId';"

    docker compose exec -T db psql -U $pgUser -d $pgDb -c $updateSql

    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: the update failed." -ForegroundColor Red
        exit 1
    }

    Write-Host "Updated account $accountId -> $language ($($supportedLanguages[$language]))" -ForegroundColor Green
    Write-Host ""
}

Write-Host ""
Write-Host "Final account language configuration:" -ForegroundColor Yellow
docker compose exec -T db psql -U $pgUser -d $pgDb -c "SELECT id, platform, account_name, default_language FROM social_accounts ORDER BY platform, account_name;"

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: could not display final configuration." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Default-language configuration complete." -ForegroundColor Green
