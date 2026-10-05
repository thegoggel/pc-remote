# Listens on the PC's LAN address and starts steam.exe. Nothing else.
# Run at logon in the desktop session. See the README.

$ErrorActionPreference = "Stop"

$configPath = Join-Path $PSScriptRoot "config.ps1"
if (Test-Path -LiteralPath $configPath) {
    . $configPath
}

if (-not $ListenerSecret) { $ListenerSecret = $env:LISTENER_SECRET }
if (-not $ListenPrefix) { $ListenPrefix = $env:LISTENER_PREFIX }
if (-not $SteamExe) { $SteamExe = $env:STEAM_EXE }
if (-not $SteamExe) { $SteamExe = "C:\Program Files (x86)\Steam\steam.exe" }

$placeholders = @(
    "replace-with-a-long-random-secret",
    "change-me"
)
if ([string]::IsNullOrWhiteSpace($ListenerSecret) -or $ListenerSecret.Length -lt 16 -or $placeholders -contains $ListenerSecret) {
    throw "Set a long ListenerSecret in config.ps1 (or LISTENER_SECRET). Do not use a placeholder."
}
if ($ListenPrefix -notmatch '^http://([0-9]{1,3}(\.[0-9]{1,3}){3}):([0-9]+)/$') {
    throw "ListenPrefix must look like http://192.168.88.50:8765/"
}
$bindHost = $Matches[1]
$bindPort = [int]$Matches[3]
if ($bindHost -eq "0.0.0.0" -or $bindHost -eq "127.0.0.1") {
    throw "Bind ListenPrefix to the PC's LAN address so the LXC can reach it."
}
foreach ($octet in $bindHost.Split(".")) {
    if ([int]$octet -gt 255) {
        throw "ListenPrefix address is not a valid IPv4 address."
    }
}
if ($bindPort -lt 1 -or $bindPort -gt 65535) {
    throw "ListenPrefix port is invalid."
}
if (-not (Test-Path -LiteralPath $SteamExe)) {
    throw "Steam was not found. Set SteamExe in config.ps1."
}

function Test-Secret([string]$Expected, [string]$Got) {
    $left = [System.Text.Encoding]::UTF8.GetBytes($Expected)
    $right = [System.Text.Encoding]::UTF8.GetBytes([string]$Got)
    $count = [Math]::Max($left.Length, $right.Length)
    if ($count -eq 0) { return $false }
    $diff = $left.Length -bxor $right.Length
    for ($i = 0; $i -lt $count; $i++) {
        $a = 0
        $b = 0
        if ($i -lt $left.Length) { $a = $left[$i] }
        if ($i -lt $right.Length) { $b = $right[$i] }
        $diff = $diff -bor ($a -bxor $b)
    }
    return $diff -eq 0
}

function Write-Json($Response, [int]$Status, [string]$Json) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Json)
    $Response.StatusCode = $Status
    $Response.ContentType = "application/json; charset=utf-8"
    $Response.ContentLength64 = $bytes.Length
    $Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $Response.OutputStream.Close()
}

function Start-SteamClient([string]$Path) {
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $existing = Get-Process -Name $name -ErrorAction SilentlyContinue
    if ($existing) { return }
    $folder = [System.IO.Path]::GetDirectoryName($Path)
    Start-Process -FilePath $Path -WorkingDirectory $folder | Out-Null
}

function Handle-Request($Context, [string]$Secret, [string]$SteamPath) {
    $request = $Context.Request
    $response = $Context.Response
    try {
        if ($request.ContentLength64 -gt 1024) {
            Write-Json $response 400 '{"ok":false}'
            return
        }
        $path = $request.Url.AbsolutePath
        if ($request.HttpMethod -ne "POST" -or $path -ne "/start") {
            Write-Json $response 404 '{"ok":false}'
            return
        }
        $auth = [string]$request.Headers["Authorization"]
        $got = ""
        if ($auth.StartsWith("Bearer ")) {
            $got = $auth.Substring(7)
        }
        if (-not (Test-Secret $Secret $got)) {
            Write-Json $response 401 '{"ok":false}'
            return
        }
        Start-SteamClient $SteamPath
        Write-Json $response 200 '{"ok":true}'
    } catch {
        Write-Output "Steam did not start."
        try { Write-Json $response 500 '{"ok":false}' } catch { }
    }
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($ListenPrefix)
$listener.Start()
Write-Output "Steam listener is waiting."
try {
    while ($listener.IsListening) {
        $context = $null
        try {
            $context = $listener.GetContext()
            Handle-Request -Context $context -Secret $ListenerSecret -SteamPath $SteamExe
        } catch {
            Write-Output "Request was not completed."
            if ($context) {
                try { $context.Response.Abort() } catch { }
            }
        }
    }
} finally {
    $listener.Stop()
}
