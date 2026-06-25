# Publish this repo to GitHub (requires: gh auth login)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$gh = "C:\Program Files\GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) {
    $gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
}
if (-not $gh) {
    throw "Install GitHub CLI: winget install GitHub.cli"
}

& $gh auth status
if ($LASTEXITCODE -ne 0) {
    Write-Host "Run: gh auth login"
    exit 1
}

$repo = "easy-pika-cli"
if (git remote get-url origin 2>$null) {
    Write-Host "Remote origin exists, pushing..."
    git push -u origin main
} else {
    & $gh repo create $repo --public --source=. --remote=origin --push
}
Write-Host "Done: https://github.com/$(& $gh api user -q .login)/$repo"
