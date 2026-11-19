[CmdletBinding()]
param(
    [ValidateSet('default', 'chrome')]
    [string]$Browser = 'default',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$url = 'https://www.youtube.com/'
if ($env:OS -ne 'Windows_NT') {
    throw 'This skill requires a local Windows desktop session.'
}

$chromePath = $null
if ($Browser -eq 'chrome') {
    $candidates = @()
    foreach ($key in @(
        'Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
        'Registry::HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
        'Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'
    )) {
        if (Test-Path -LiteralPath $key) {
            $value = (Get-Item -LiteralPath $key).GetValue('')
            if ($value) { $candidates += ([string]$value).Trim('"') }
        }
    }
    foreach ($base in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base) { $candidates += Join-Path $base 'Google\Chrome\Application\chrome.exe' }
    }
    $chromePath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $chromePath) {
        throw 'Google Chrome was requested but was not found. Use -Browser default for the default browser.'
    }
}

if (-not $DryRun) {
    if ($Browser -eq 'chrome') {
        Start-Process -FilePath $chromePath -ArgumentList $url -ErrorAction Stop
    }
    else {
        Start-Process -FilePath $url -ErrorAction Stop
    }
}

[PSCustomObject]@{
    url = $url
    browser = $Browser
    executable = $chromePath
    requested = (-not [bool]$DryRun)
    dry_run = [bool]$DryRun
} | ConvertTo-Json -Compress
