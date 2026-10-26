param(
    [ValidateSet('Shadow','Live','Status','Stop')][string]$Mode = 'Shadow',
    [string]$TulpaRoot = '',
    [string]$Workspace = '',
    [int]$MaxSeconds = 300,
    [int]$MaxBatches = 5,
    [switch]$Resume,
    [switch]$Background
)
$ErrorActionPreference = 'Stop'
$chatSourceRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $TulpaRoot) { $TulpaRoot = $chatSourceRoot }
if (-not $Workspace) { $Workspace = $chatSourceRoot }
$chatPython = Join-Path $chatSourceRoot 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $chatPython)) { $chatPython = Join-Path $chatSourceRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $chatPython)) { throw 'Use the installed MCP package or its configured Python environment.' }
$chatState = Join-Path $TulpaRoot '.tmp\chat-agent'
New-Item -ItemType Directory -Force -Path $chatState | Out-Null
$chatCommand = if ($Mode -eq 'Status') { 'status' } elseif ($Mode -eq 'Stop') { 'stop' } else { 'run' }
$chatArgs = @('-X','utf8',(Join-Path $chatSourceRoot 'scripts\chat_agent.py'),$chatCommand,'--tulpa-root',$TulpaRoot,'--workspace',$Workspace,'--state-dir',$chatState)
if ($chatCommand -eq 'run') { $chatArgs += @('--mode',$Mode.ToLowerInvariant(),'--max-seconds',[string]$MaxSeconds,'--max-batches',[string]$MaxBatches); if ($Resume) { $chatArgs += '--resume' } }
if ($Background -and $chatCommand -eq 'run') {
    $chatQuotedArgs = @($chatArgs | ForEach-Object { '"' + $_.Replace('"','\"') + '"' })
    $chatProcess = Start-Process -FilePath $chatPython -ArgumentList $chatQuotedArgs -WindowStyle Hidden -WorkingDirectory $Workspace -RedirectStandardOutput (Join-Path $chatState 'runner.stdout.log') -RedirectStandardError (Join-Path $chatState 'runner.stderr.log') -PassThru
    Write-Output "Chat process started: PID $($chatProcess.Id). Verify state with -Mode Status."
} else {
    & $chatPython @chatArgs
    exit $LASTEXITCODE
}
