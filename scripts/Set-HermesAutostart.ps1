[CmdletBinding()]
param(
    [switch]$Disable,
    [string]$Executable = ''
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $PSScriptRoot '..\dist\ListenerSuite\ListenerHermes.exe'
}
$startupDirectory = [Environment]::GetFolderPath('Startup')
if ([string]::IsNullOrWhiteSpace($startupDirectory)) {
    throw 'The current user Startup folder is unavailable.'
}
$shortcutPath = Join-Path $startupDirectory 'Listener Hermes.lnk'

if ($Disable) {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath
    }
    [PSCustomObject]@{ Enabled = $false; Shortcut = $shortcutPath }
    return
}

$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
if (-not (Test-Path -LiteralPath $resolvedExecutable -PathType Leaf)) {
    throw "Executable not found: $resolvedExecutable"
}
if ([IO.Path]::GetFileName($resolvedExecutable) -ine 'ListenerHermes.exe') {
    throw 'Expected ListenerHermes.exe.'
}

$shell = New-Object -ComObject WScript.Shell
try {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    if ((Test-Path -LiteralPath $shortcutPath) -and $shortcut.TargetPath -and
        $shortcut.TargetPath -ine $resolvedExecutable) {
        throw "An existing shortcut points elsewhere: $($shortcut.TargetPath)"
    }
    $shortcut.TargetPath = $resolvedExecutable
    $shortcut.WorkingDirectory = Split-Path -Parent $resolvedExecutable
    $shortcut.Arguments = ''
    $shortcut.Description = 'Start Listener Hermes voice commands at user sign-in.'
    $shortcut.IconLocation = "$resolvedExecutable,0"
    $shortcut.WindowStyle = 7
    $shortcut.Save()

    $verified = $shell.CreateShortcut($shortcutPath)
    if ($verified.TargetPath -ine $resolvedExecutable -or $verified.Arguments) {
        throw 'Startup shortcut verification failed.'
    }
    [PSCustomObject]@{
        Enabled = $true
        Shortcut = $shortcutPath
        Executable = $verified.TargetPath
        WorkingDirectory = $verified.WorkingDirectory
    }
}
finally {
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
}
