# Read and control whatever is playing anywhere on this PC.
#
# Windows' GlobalSystemMediaTransportControlsSessionManager reports every app
# that registers media transport controls — browsers included. That is how the
# deck can see and drive a YouTube tab in Opera or Chrome, the YourMusic app
# from the Store, Spotify or Media Player, with no browser extension and no
# per-app integration.
#
# Every session is reported, not just the one in front, and every action takes
# an optional -App so the deck can pause the video in one browser without
# touching the music in another. Omitting -App keeps the old behaviour: act on
# whichever session Windows currently considers current.
#
# Always prints one JSON object on stdout, including on failure, so the caller
# never has to distinguish "no output" from "nothing playing".
#
#   nowplaying.ps1                                  -> current session + all sessions
#   nowplaying.ps1 -Action play_pause -App <appid>
#   nowplaying.ps1 -Action play | pause | next | previous | stop
#   nowplaying.ps1 -Action seek -Position 42        -> seconds from the start

[CmdletBinding()]
param(
    [ValidateSet('read', 'play_pause', 'play', 'pause', 'next', 'previous', 'stop', 'seek')]
    [string]$Action = 'read',
    [string]$App = '',
    [int]$Position = 0
)

$ErrorActionPreference = 'Stop'

function Write-Result($obj) {
    # Depth 6: the session list is one level deeper than a single session.
    $obj | ConvertTo-Json -Compress -Depth 6
    exit 0
}

function Write-Unavailable($reason) {
    Write-Result ([ordered]@{ available = $false; error = $reason; status = 'stopped'; sessions = @() })
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop

    # WinRT async methods return IAsyncOperation, which PowerShell cannot await
    # directly. Reflect out the generic AsTask overload once and reuse it.
    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

    if (-not $asTask) { Write-Unavailable 'WindowsRuntime AsTask unavailable' }

    function Await($op, $type) {
        $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
        # Bounded: a hung session manager must not wedge the request thread.
        if (-not $task.Wait(4000)) { throw 'timed out waiting on WinRT' }
        $task.Result
    }

    function Read-Session($session) {
        $props = Await ($session.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
        $info = $session.GetPlaybackInfo()
        $timeline = $session.GetTimelineProperties()
        $controls = $info.Controls

        $status = switch ("$($info.PlaybackStatus)") {
            'Playing' { 'playing' }
            'Paused'  { 'paused' }
            default   { 'stopped' }
        }

        # EndTime is relative to StartTime, which is not always zero — a live
        # stream reports a moving window. Subtracting keeps the scrubber honest.
        $start = $timeline.StartTime.TotalSeconds
        $duration = [int]($timeline.EndTime.TotalSeconds - $start)
        $position = [int]($timeline.Position.TotalSeconds - $start)

        [ordered]@{
            app_id        = "$($session.SourceAppUserModelId)"
            title         = "$($props.Title)"
            artist        = "$($props.Artist)"
            album         = "$($props.AlbumTitle)"
            status        = $status
            position_secs = [Math]::Max(0, $position)
            duration_secs = [Math]::Max(0, $duration)
            # What this particular app will actually accept. A YouTube tab takes
            # seeks; a live stream does not. The deck greys out the rest rather
            # than offering a button that silently does nothing.
            can_seek      = [bool]$controls.IsPlaybackPositionEnabled
            can_next      = [bool]$controls.IsNextEnabled
            can_previous  = [bool]$controls.IsPreviousEnabled
            can_pause     = [bool]$controls.IsPauseEnabled
        }
    }

    $mgrType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]
    $mgr = Await ($mgrType::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
    if (-not $mgr) { Write-Unavailable 'no session manager' }

    $all = @($mgr.GetSessions())
    $current = $mgr.GetCurrentSession()

    # An explicit -App wins; otherwise the app Windows considers current. The
    # match is a prefix so the deck can pass the readable id it was given
    # without carrying the full '_8wekyb3d8bbwe!App' suffix around.
    $target = $current
    if ($App) {
        $target = $all | Where-Object { "$($_.SourceAppUserModelId)".StartsWith($App, 'OrdinalIgnoreCase') } | Select-Object -First 1
        if (-not $target) { Write-Result ([ordered]@{ available = $true; ok = $false; error = "no player matching '$App'" }) }
    }

    if ($Action -ne 'read') {
        if (-not $target) { Write-Result ([ordered]@{ available = $true; ok = $false; error = 'nothing is playing' }) }

        # Each Try*Async returns a bool for whether the app accepted it; an app
        # that cannot skip or seek simply says no.
        $ok = switch ($Action) {
            'play_pause' { Await ($target.TryTogglePlayPauseAsync()) ([bool]) }
            'play'       { Await ($target.TryPlayAsync())            ([bool]) }
            'pause'      { Await ($target.TryPauseAsync())           ([bool]) }
            'next'       { Await ($target.TrySkipNextAsync())        ([bool]) }
            'previous'   { Await ($target.TrySkipPreviousAsync())    ([bool]) }
            'stop'       { Await ($target.TryStopAsync())            ([bool]) }
            'seek'       {
                # The API takes 100ns ticks from the timeline's own start, so a
                # stream whose window does not begin at zero still lands right.
                $origin = $target.GetTimelineProperties().StartTime.Ticks
                Await ($target.TryChangePlaybackPositionAsync($origin + [int64]$Position * 10000000)) ([bool])
            }
        }
        Write-Result ([ordered]@{ available = $true; ok = [bool]$ok; action = $Action; app_id = "$($target.SourceAppUserModelId)" })
    }

    # Album art is deliberately not read.
    #
    # The session does expose a Thumbnail, but OpenReadAsync() hands Windows
    # PowerShell 5.1 a bare System.__ComObject: the WinRT stream interface is
    # not projected, GetInputStreamAt() does not exist on it, and it cannot be
    # cast to IRandomAccessStream. The usual workaround is a compiled C# shim
    # via Add-Type — but Add-Type caches per *process*, and this script runs as
    # a fresh process on every poll, so that would recompile every few seconds
    # for a thumbnail. PowerShell 7 projects WinRT properly and would make this
    # straightforward; it is not installed on this machine.
    #
    # The deck renders a placeholder when this is empty, so the strip is
    # complete without it. Revisit if pwsh 7 ever lands here.

    $sessions = @()
    foreach ($session in $all) {
        try { $sessions += (Read-Session $session) }
        catch { <# one wedged app must not cost the deck the whole list #> }
    }

    if (-not $target) {
        Write-Result ([ordered]@{ available = $true; status = 'stopped'; error = ''; sessions = $sessions })
    }

    $now = Read-Session $target
    $now['available'] = $true
    $now['error'] = ''
    $now['thumbnail'] = ''
    $now['sessions'] = $sessions
    Write-Result $now
}
catch {
    Write-Unavailable "$($_.Exception.Message)"
}
