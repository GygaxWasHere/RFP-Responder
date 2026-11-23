# Install or update RFP Responder for the current Windows user (no admin rights needed).
#   - copies the app to %LOCALAPPDATA%\RFPResponder\app
#   - creates a private Python environment in %LOCALAPPDATA%\RFPResponder\venv
#   - adds "RFP Responder" to the Start menu and the desktop
# Run again to update. Settings, projects and knowledge caches are kept.
param([switch]$NoShortcuts)   # -NoShortcuts: for testing
$ErrorActionPreference = "Stop"
$src  = Split-Path -Parent $PSScriptRoot
$base = Join-Path $env:LOCALAPPDATA "RFPResponder"
$app  = Join-Path $base "app"
$venv = Join-Path $base "venv"

Write-Host "RFP Responder installer" -ForegroundColor Cyan
Write-Host "From: $src"
Write-Host "To:   $app`n"

# 1. Python 3.11 or newer
$py = $null
foreach ($c in @(@("py", "-3"), @("python"))) {
    try {
        $v = & $c[0] $c[1..9] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$v -ge [version]"3.11") { $py = $c; break }
    } catch { }
}
if (-not $py) {
    Write-Host "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $(& $py[0] $py[1..9] --version)"

# 2. Copy the app (without caches and git data)
New-Item -ItemType Directory -Force $base | Out-Null
if (Test-Path $app) { Remove-Item -Recurse -Force $app }
New-Item -ItemType Directory -Force $app | Out-Null
foreach ($item in @("responder", "templates", "RFPResponder.pyw", "requirements.txt", "README.md")) {
    $p = Join-Path $src $item
    if (Test-Path $p) { Copy-Item -Recurse $p $app }
}
Get-ChildItem $app -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# 3. Private Python environment
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "Creating the Python environment..."
    & $py[0] $py[1..9] -m venv $venv
}
$vpy = Join-Path $venv "Scripts\python.exe"
Write-Host "Installing packages (a minute or two the first time)..."
& $vpy -m pip install --quiet --disable-pip-version-check --upgrade pip
& $vpy -m pip install --quiet --disable-pip-version-check -r (Join-Path $app "requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Host "Package installation failed." -ForegroundColor Red; exit 1 }

# 4. Shortcuts
if (-not $NoShortcuts) {
$target = Join-Path $venv "Scripts\pythonw.exe"
$sh = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $sh.CreateShortcut((Join-Path $dir "RFP Responder.lnk"))
    $lnk.TargetPath = $target
    $lnk.Arguments = "-B `"$(Join-Path $app 'RFPResponder.pyw')`""
    $lnk.WorkingDirectory = $app
    $lnk.Description = "Milestone RFP Responder"
    $lnk.Save()
}
}

# 5. Claude Code
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    Write-Host "Claude Code: $(& claude --version 2>$null)"
} else {
    Write-Host "`nClaude Code was not found. Install it and sign in with your own account (see https://docs.claude.com/en/docs/claude-code), then start RFP Responder." -ForegroundColor Yellow
}
Write-Host "`nDone. Start 'RFP Responder' from the Start menu or the desktop." -ForegroundColor Green
