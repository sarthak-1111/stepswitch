<#
.SYNOPSIS
Stepswitch installer for Windows: uv, then Stepswitch as a uv tool, then `ssw prefetch`.

.DESCRIPTION
Without administrator rights, and without editing PATH or the profile, it installs:

1. uv (the pinned version below), unless a uv is already installed. Its release archive for
   this CPU is downloaded, checked against the pinned SHA-256 below, and unpacked into
   UV_INSTALL_DIR (default: ~\.local\bin).
2. Stepswitch, as a uv tool (`uv tool install`). uv downloads a Python build first when no
   Python 3.11 or newer is installed.
3. What tasks use offline: `ssw prefetch` downloads the tokenizer files.

Every download is announced first. Nothing else is installed: models come later, with
`ssw models pull`.

Settings, as environment variables:
  STEPSWITCH_PACKAGE       what to install (default: stepswitch==<pinned version>; a wheel
                           file works too)
  STEPSWITCH_NO_PREFETCH   1 skips `ssw prefetch`
  UV_INSTALL_DIR           where a new uv goes

.PARAMETER DryRun
Print the plan and change nothing.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File install.ps1
#>
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

# PowerShell 7 also runs on Linux and macOS, where install.sh is the installer. Windows
# PowerShell 5.1 has no $IsWindows, hence the Test-Path first.
if ((Test-Path variable:IsWindows) -and -not $IsWindows) {
    Write-Host 'stepswitch-install: this installer is for Windows; on Linux and macOS, use install.sh'
    exit 1
}

$UvVersion = '0.13.1'
# SHA-256 of the uv release archives, from the release's .sha256 files (checked 2026-10-19).
$UvArchives = @{
    'AMD64' = @{
        Name = 'uv-x86_64-pc-windows-msvc.zip'
        Sha256 = '3b2661a463a5baea7f5076ea2b25ab71d77f36a6c0d809e5350651792f80a672'
    }
    'ARM64' = @{
        Name = 'uv-aarch64-pc-windows-msvc.zip'
        Sha256 = 'ec7846a5939b4654afee24f2d82bf6fde71bd7a6952d7748f1135ab301dd0a49'
    }
}
$StepswitchVersion = '0.1.0'

function Say([string]$Message) {
    Write-Host "stepswitch-install: $Message"
}

function Get-UvDir {
    if ($env:UV_INSTALL_DIR) { return $env:UV_INSTALL_DIR }
    return (Join-Path $HOME '.local\bin')
}

function Find-Uv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidate = Join-Path (Get-UvDir) 'uv.exe'
    if (Test-Path $candidate) { return $candidate }
    return $null
}

function Get-UvArchive {
    $machine = $env:PROCESSOR_ARCHITEW6432
    if (-not $machine) { $machine = $env:PROCESSOR_ARCHITECTURE }
    if (-not $machine -or -not $UvArchives.ContainsKey($machine)) {
        throw "no uv build for this CPU ($machine); install uv yourself, then run this again"
    }
    $archive = $UvArchives[$machine]
    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$($archive.Name)"
    return @{ Name = $archive.Name; Sha256 = $archive.Sha256; Url = $url }
}

function Install-Uv($Archive) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('stepswitch-' + [guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp $Archive.Name
        Say "downloading uv $UvVersion from $($Archive.Url)"
        [Net.ServicePointManager]::SecurityProtocol = `
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $previous = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Archive.Url -OutFile $zip
        } finally {
            $ProgressPreference = $previous
        }
        $actual = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLowerInvariant()
        if ($actual -ne $Archive.Sha256) {
            throw "the uv archive's SHA-256 is $actual, not the pinned $($Archive.Sha256)"
        }
        $unpacked = Join-Path $tmp 'uv'
        Expand-Archive -Path $zip -DestinationPath $unpacked
        $dir = Get-UvDir
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        foreach ($name in @('uv.exe', 'uvx.exe', 'uvw.exe')) {
            $file = Join-Path $unpacked $name
            if (Test-Path $file) { Copy-Item -Force -Path $file -Destination $dir }
        }
    } finally {
        Remove-Item -Recurse -Force -Path $tmp -ErrorAction SilentlyContinue
    }
}

$package = $env:STEPSWITCH_PACKAGE
if (-not $package) { $package = "stepswitch==$StepswitchVersion" }
$prefetch = $env:STEPSWITCH_NO_PREFETCH -ne '1'

$uv = Find-Uv
$archive = $null
if ($uv) {
    Say "uv: $uv"
} else {
    $archive = Get-UvArchive
    Say "uv: $UvVersion from $($archive.Url), into $(Get-UvDir)"
}
Say "Stepswitch: uv tool install $package"
if ($prefetch) { Say 'then: ssw prefetch (the tokenizer files)' }
if ($DryRun) {
    Say 'dry run: nothing was changed'
    return
}

if (-not $uv) {
    Install-Uv $archive
    $uv = Find-Uv
    if (-not $uv) { throw 'uv was installed but cannot be found; set UV_INSTALL_DIR' }
}
& $uv tool install $package
if ($LASTEXITCODE -ne 0) { throw "uv tool install $package failed" }
$bin = (& $uv tool dir --bin | Out-String).Trim()
$ssw = Join-Path $bin 'ssw.exe'
if (-not (Test-Path $ssw)) { throw "ssw is not in $bin after the install" }
& $ssw --version
if ($prefetch) {
    & $ssw prefetch
    if ($LASTEXITCODE -ne 0) { Say 'warning: ssw prefetch did not finish; run it again later' }
}
$onPath = ($env:PATH -split ';') | Where-Object { $_.TrimEnd('\') -eq $bin.TrimEnd('\') }
if (-not $onPath) { Say "add $bin to your PATH, or run: uv tool update-shell" }
Say 'done. Next, in your repository: ssw init, ssw models pull --tier cpu, ssw run "<task>"'
