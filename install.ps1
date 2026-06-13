<#
  decant installer (Windows).
  Creates .venv, installs Python deps, and sets up the browser engine.
  Default engine on Windows is the installed Edge (no download); if Edge is
  absent, falls back to Playwright's bundled Chromium. Safe to re-run.

  Usage:  powershell -ExecutionPolicy Bypass -File install.ps1
#>
$ErrorActionPreference = 'Stop'
$root = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
Set-Location $root

# 1) locate Python
$py = $null
foreach ($c in 'python', 'py') {
  $cmd = Get-Command $c -ErrorAction SilentlyContinue
  if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py) { throw "Python 3.9+ not found. Install from https://python.org and re-run." }
Write-Host "[decant] python: $py"

# 2) venv
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
  Write-Host "[decant] creating .venv ..."
  & $py -m venv (Join-Path $root '.venv')
}

# 3) dependencies
Write-Host "[decant] installing dependencies ..."
& $venvPy -m pip install --disable-pip-version-check -q --upgrade pip
& $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $root 'requirements.txt')

# 4) browser engine
$edge = @("$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
          "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
  Write-Host "[decant] using installed Edge (no browser download)."
} else {
  Write-Host "[decant] Edge not found - installing bundled Chromium ..."
  & $venvPy -m playwright install chromium
  Write-Host "[decant] NOTE: set DECANT_BROWSER to empty (setx DECANT_BROWSER `"`") or pass --channel `"`" to use bundled Chromium."
}

Write-Host ""
Write-Host "[decant] done. Next steps:"
Write-Host "  .\decant.cmd login https://your-confluence.example.com   # one-time auth (2FA ok)"
Write-Host "  .\decant.cmd get <URL> --out .\captures                  # rip one page"
Write-Host "  .\decant.cmd serve                                       # daemon + bookmarklet"
