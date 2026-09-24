# ============================================================================
# Sara Wallet installer - Windows 10/11 (64-bit Intel/AMD)
#
# Read this before you run it. What it does, in order:
#   1. Downloads a pinned copy of `uv` (a small Python package manager) from
#      github.com/astral-sh/uv and checks it against a SHA-256 hash written
#      into this file.
#   2. Downloads this Sara release's source code and checks it against the
#      SHA-256 hash written into this file. A mismatch aborts the install.
#   3. Uses uv to fetch a private copy of Python 3.12 and installs Sara's
#      reviewed, version-locked dependencies (backend\requirements-lock.txt)
#      as prebuilt wheels only - nothing is compiled, no build scripts run.
#   4. Writes a launcher and a Start Menu shortcut.
#
# Everything is kept in %LOCALAPPDATA%\SaraWalletApp. It does not need
# administrator rights and does not touch PATH or the registry. Your wallet
# data is stored separately and is never touched by install, update, or
# -Uninstall.
# ============================================================================
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Filled in by install/build_installers.py when a release is published. --
$SaraVersion   = '@@SARA_VERSION@@'
$SaraSourceUrl = '@@SARA_SOURCE_URL@@'
$SaraSourceSha = '@@SARA_SOURCE_SHA256@@'
# ---------------------------------------------------------------------------

# Pinned uv build. The hash is from astral-sh/uv's own release file and was
# re-computed from the downloaded archive before being written here.
$UvVersion = '0.12.18'
$UvUrl     = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
$UvSha     = 'cae6a3bc25239f83dffb467a4b180508d9da23986c04639ebfa44e43e6a84bff'

$Port = 8888

function Say($msg) { Write-Host $msg }

function Fail($msg) {
    Write-Host ''
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

# Plain .NET, not Get-FileHash: when this runs under a polluted PSModulePath
# (e.g. started from a PowerShell 7 window) Windows PowerShell 5.1 can fail to
# load its own utility module, and a checksum step must never depend on that.
function Get-Sha256($path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try { $bytes = $sha.ComputeHash($stream) } finally { $stream.Dispose(); $sha.Dispose() }
    return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLower()
}

function Get-Verified($url, $dest, $expected, $label) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $dest
    } catch {
        Fail "Couldn't download $label from $url ($($_.Exception.Message))"
    }
    $actual = Get-Sha256 $dest
    if ($actual -ne $expected.ToLower()) {
        Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue
        Fail ("Checksum mismatch for $label.`n  expected: $expected`n  got:      $actual`n" +
              "The download was corrupted or tampered with. Nothing has been installed.")
    }
}

function Test-SaraRunning {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if ($SaraVersion -like '*@@*' -or $SaraSourceUrl -like '*@@*' -or $SaraSourceSha -like '*@@*') {
    Write-Host 'This is the unfilled installer template, not a release installer.'
    Write-Host 'Download the installer from https://github.com/rohasnagpal/sara-wallet/releases'
    exit 2
}

$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if ($arch -ne 'AMD64') {
    Fail ("Unsupported processor ($arch). This installer supports 64-bit Intel/AMD Windows only: one of " +
          "Sara's required libraries (ckzg) publishes no prebuilt Windows-on-ARM build, and this installer " +
          "never compiles code. Use the Docker or run-from-source instructions in the README instead.")
}

# The program lives in SaraWalletApp, NOT SaraWallet: Sara keeps wallet data in
# %LOCALAPPDATA%\SaraWallet\Sara (see backend/desktop_launcher.py), and an
# uninstall that removes the program folder must never be able to reach it.
$Root = if ($env:SARA_HOME) { $env:SARA_HOME } else { Join-Path $env:LOCALAPPDATA 'SaraWalletApp' }
$Marker = Join-Path $Root '.sara-wallet-root'
$Shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Sara.lnk'
$DataDir = Join-Path $env:LOCALAPPDATA 'SaraWallet\Sara'
$rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
$dataFull = [IO.Path]::GetFullPath($DataDir).TrimEnd('\') + '\'
if ($dataFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase) -or
    $rootFull.StartsWith($dataFull, [StringComparison]::OrdinalIgnoreCase)) {
    Fail "The install folder ($Root) overlaps the wallet-data folder ($DataDir). Refusing to continue so wallet data can't be deleted."
}

# ---------------------------------------------------------------- uninstall
if ($Uninstall) {
    if ((Test-Path -LiteralPath $Root) -and -not (Test-Path -LiteralPath $Marker)) {
        Fail "$Root exists but wasn't created by this installer, so it won't be deleted."
    }
    if (Test-SaraRunning) { Fail 'Sara is running. Close its window first, then run this again.' }
    if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force }
    if (Test-Path -LiteralPath $Shortcut) { Remove-Item -LiteralPath $Shortcut -Force }
    Say 'Sara has been removed.'
    Say 'Your wallet data was NOT deleted. It is still at:'
    Say "  $DataDir"
    Say 'Keep it - together with your recovery phrase it is the only way to recover your wallets.'
    exit 0
}

# ------------------------------------------------------------------ install
if (Test-SaraRunning) {
    Fail "Something is already running on port $Port (probably Sara). Close its window first, then run this installer again."
}

New-Item -ItemType Directory -Force -Path $Root | Out-Null
New-Item -ItemType File -Force -Path $Marker | Out-Null
# Short temp names on purpose: Windows' 260-character path limit applies while
# the source archive is being extracted.
Get-ChildItem -LiteralPath $Root -Directory -Filter '.t*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
$Work = Join-Path $Root ('.t' + [Guid]::NewGuid().ToString('N').Substring(0, 6))
New-Item -ItemType Directory -Force -Path $Work | Out-Null

try {
    Say "Installing Sara $SaraVersion into $Root"
    Say ''

    # ---- uv
    $Uv = Join-Path $Root 'bin\uv.exe'
    $uvOk = $false
    if (Test-Path -LiteralPath $Uv) {
        $ver = & $Uv --version 2>$null
        if ($ver -like "uv $UvVersion *") { $uvOk = $true }
    }
    if ($uvOk) {
        Say "[1/4] uv $UvVersion already present."
    } else {
        Say "[1/4] Downloading uv $UvVersion (Python package manager)..."
        Get-Verified $UvUrl (Join-Path $Work 'uv.zip') $UvSha "uv $UvVersion"
        Expand-Archive -LiteralPath (Join-Path $Work 'uv.zip') -DestinationPath (Join-Path $Work 'uv')
        New-Item -ItemType Directory -Force -Path (Join-Path $Root 'bin') | Out-Null
        Copy-Item -LiteralPath (Join-Path $Work 'uv\uv.exe') -Destination $Uv -Force
    }

    # ---- Sara source + dependencies
    $Target = Join-Path $Root "versions\$SaraVersion"
    $VenvPy = Join-Path $Target '.venv\Scripts\python.exe'
    $Installed = Join-Path $Target '.installed'

    if (Test-Path -LiteralPath $Installed) {
        Say "[2/4] Sara $SaraVersion source already installed."
        Say '[3/4] Dependencies already installed.'
    } else {
        if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
        Say "[2/4] Downloading Sara $SaraVersion..."
        Get-Verified $SaraSourceUrl (Join-Path $Work 'sara.zip') $SaraSourceSha "Sara $SaraVersion"
        Expand-Archive -LiteralPath (Join-Path $Work 'sara.zip') -DestinationPath (Join-Path $Work 'src')
        $top = @(Get-ChildItem -LiteralPath (Join-Path $Work 'src'))
        if ($top.Count -ne 1) { Fail 'Unexpected layout in the Sara download.' }
        New-Item -ItemType Directory -Force -Path (Join-Path $Root 'versions') | Out-Null
        Move-Item -LiteralPath $top[0].FullName -Destination $Target
        if (-not ((Test-Path -LiteralPath (Join-Path $Target 'backend\desktop_launcher.py')) -and
                  (Test-Path -LiteralPath (Join-Path $Target 'backend\requirements-lock.txt')))) {
            Fail 'The Sara download is missing expected files.'
        }

        Say "[3/4] Installing Python 3.12 and Sara's dependencies (a few minutes the first time)..."
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $Root 'python'
        $env:UV_CACHE_DIR = Join-Path $Root 'cache'
        & $Uv venv --no-config --managed-python --python 3.12 (Join-Path $Target '.venv')
        if ($LASTEXITCODE -ne 0) { Fail "Couldn't set up Python 3.12." }
        & $Uv pip install --no-config --python $VenvPy --only-binary ':all:' -r (Join-Path $Target 'backend\requirements-lock.txt')
        if ($LASTEXITCODE -ne 0) { Fail "Couldn't install Sara's dependencies." }
        & $Uv pip check --python $VenvPy
        if ($LASTEXITCODE -ne 0) { Fail 'Installed dependencies are inconsistent.' }
        & $VenvPy -c 'import fastapi, uvicorn, web3, eth_account, mnemonic, platformdirs'
        if ($LASTEXITCODE -ne 0) { Fail 'Installed Python environment failed its import check.' }
        New-Item -ItemType File -Force -Path $Installed | Out-Null
    }

    # ---- launcher
    Say '[4/4] Creating the launcher and Start Menu shortcut...'
    $Launcher = Join-Path $Root 'Sara.cmd'
    # Paths are written relative to the launcher's own folder (%~dp0) so this
    # file stays pure ASCII even if your Windows user name has accented
    # characters - cmd expands %~dp0 correctly at run time.
    $cmdText = "@echo off`r`n" +
               "title Sara - close this window to stop Sara`r`n" +
               "`"%~dp0versions\$SaraVersion\.venv\Scripts\python.exe`" `"%~dp0versions\$SaraVersion\backend\desktop_launcher.py`" %*`r`n" +
               "echo.`r`n" +
               "echo Sara has stopped.`r`n" +
               "pause`r`n"
    [IO.File]::WriteAllText($Launcher, $cmdText, (New-Object Text.ASCIIEncoding))

    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($Shortcut)
    $lnk.TargetPath = $Launcher
    $lnk.WorkingDirectory = $Root
    $lnk.Description = 'Sara AI Wallet'
    $lnk.Save()

    # ---- tidy up older versions (wallet data lives elsewhere, so this is safe)
    $versionsDir = Join-Path $Root 'versions'
    Get-ChildItem -LiteralPath $versionsDir -Directory |
        Where-Object { $_.Name -ne $SaraVersion } |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

    $dataDir = (& $VenvPy -c "import platformdirs; print(platformdirs.user_data_dir('Sara', 'SaraWallet'))" 2>$null)
    if (-not $dataDir) { $dataDir = $DataDir }
} finally {
    if (Test-Path -LiteralPath $Work) { Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue }
}

Say ''
Say "Sara $SaraVersion is installed."
Say ''
Say "  Your wallet data:  $dataDir"
Say '  (never touched by updates or -Uninstall)'
Say ''
Say '  The first time you create a wallet, Sara shows a 24-word recovery'
Say '  phrase. Write it down and keep it offline - it is the only way to'
Say '  recover your wallets.'
Say ''
Say '  To start Sara later: open "Sara" from the Start Menu.'
Say '  To update: run the installer from a newer release.'
Say '  To remove: run Install-Sara.bat -Uninstall'
Say ''

if ($env:SARA_NO_LAUNCH -eq '1') { exit 0 }

Say 'Starting Sara - your browser will open automatically...'
Start-Process -FilePath $Launcher -WorkingDirectory $Root
for ($i = 0; $i -lt 90; $i++) {
    if (Test-SaraRunning) {
        Say "Sara is running at http://127.0.0.1:$Port"
        Say 'Leave the Sara window open while you use it; close it to stop Sara.'
        exit 0
    }
    Start-Sleep -Seconds 1
}
Say "Sara is still starting. If your browser doesn't open, look at the Sara window for errors."
exit 0
