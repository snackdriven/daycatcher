param(
    [Parameter(Mandatory=$true)][string]$Config,
    [ValidateSet('Install','Plan','Status','Start','Stop','Remove')][string]$Mode = 'Install',
    [string]$PythonwPath
)
$ErrorActionPreference = 'Stop'
$configPath = [IO.Path]::GetFullPath($Config)
$runtimePath = Split-Path -Parent $configPath
if ((Split-Path -Leaf $configPath) -ne 'personal-summary.json') { throw 'Expected canonical personal-summary.json configuration' }
$stopPath = Join-Path $runtimePath 'personal-summary-STOP'
$taskName = 'Personal Journal Sync - ' + $env:USERNAME
$description = 'Private personal journal summary sync; managed exclusively by collector-core/install_personal_summary.ps1 v1'

function Write-Settings($settings) {
    $temporary = Join-Path $runtimePath ('.personal-summary-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllText($temporary, ($settings | ConvertTo-Json -Depth 20), [Text.UTF8Encoding]::new($false))
        [IO.File]::Replace($temporary, $configPath, [NullString]::Value)
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
}
function Worker-IsActive {
    $lock = Join-Path $runtimePath '.personal-summary-worker/local-worker.lock'
    if (-not (Test-Path -LiteralPath $lock)) { return $false }
    try {
        $stream = [IO.File]::Open($lock, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        $stream.Dispose()
        return $false
    } catch [IO.IOException] { return $true }
}

# A corrupt or missing JSON file must never prevent an emergency stop.
if ($Mode -in @('Stop','Remove')) {
    [IO.Directory]::CreateDirectory($runtimePath) | Out-Null
    [IO.File]::WriteAllText($stopPath, 'Stopped by personal summary controller')
}
$servicePath = Join-Path $PSScriptRoot 'personal_summary_service.py'
$arguments = '"' + $servicePath + '" --config "' + $configPath + '"'
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if (-not $PythonwPath) {
    $pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
    $PythonwPath = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
}
if (-not [IO.Path]::IsPathRooted($PythonwPath) -or -not (Test-Path -LiteralPath $PythonwPath -PathType Leaf) -or (Split-Path -Leaf $PythonwPath) -ne 'pythonw.exe') { throw 'Absolute pythonw.exe is required' }
$PythonwPath = [IO.Path]::GetFullPath($PythonwPath)
function Normalize-PrincipalSid([string]$identity) {
    if ($identity -match '^S-1-') { return [Security.Principal.SecurityIdentifier]::new($identity).Value }
    return [Security.Principal.NTAccount]::new($identity).Translate([Security.Principal.SecurityIdentifier]).Value
}
function Assert-OwnedTask($existing) {
    try {
        if ($existing.Description -cne $description -or @($existing.Actions).Count -ne 1) { throw 'ownership' }
        $action = $existing.Actions[0]
        if ([string]$action.Execute -ine $PythonwPath -or [string]$action.Arguments -cne $arguments -or
            [string]$action.WorkingDirectory -ine $PSScriptRoot) { throw 'action_binding' }
        if ((Normalize-PrincipalSid ([string]$existing.Principal.UserId)) -ne $currentSid -or
            [string]$existing.Principal.LogonType -notin @('Interactive','InteractiveToken','3') -or
            [string]$existing.Principal.RunLevel -notin @('Limited','LeastPrivilege','0')) { throw 'principal_binding' }
    } catch { throw 'An unrelated or redirected task has this name; leaving it unchanged' }
}
if ($Mode -ne 'Plan') {
    $task = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($task) { Assert-OwnedTask $task }
}
if ($Mode -in @('Stop','Remove')) {
    if ($task) {
        Disable-ScheduledTask -TaskName $taskName -TaskPath '\' | Out-Null
        if ([string]$task.State -eq 'Running') { Stop-ScheduledTask -TaskName $taskName -TaskPath '\' }
    }
    try {
        $settings = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        if ($settings -is [pscustomobject]) { $settings | Add-Member enabled $false -Force; Write-Settings $settings }
    } catch { } # STOP and disabled task are authoritative even when JSON is broken.
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ((Worker-IsActive) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    $active = Worker-IsActive
    if ($Mode -eq 'Remove' -and $task) { Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false }
    @{task_name=$taskName; stopped=(-not $active); worker_active=$active; removed=($Mode -eq 'Remove'); data_retained=$true} | ConvertTo-Json -Compress
    return
}
if ($Mode -eq 'Status') {
    $result = @{task_name=$taskName; installed=[bool]$task; stop_requested=(Test-Path -LiteralPath $stopPath); worker_active=(Worker-IsActive)}
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $taskName -TaskPath '\'
        $result.state=[string]$task.State; $result.last_result=$info.LastTaskResult
        $result.executable=$task.Actions[0].Execute; $result.arguments=$task.Actions[0].Arguments
        $result.logon_type=[string]$task.Principal.LogonType; $result.run_level=[string]$task.Principal.RunLevel
    }
    $result | ConvertTo-Json -Compress
    return
}
$settings = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
foreach ($entry in @(@('data_dir',$runtimePath), @('source_db',(Join-Path $runtimePath 'collector.sqlite3')), @('queue_db',(Join-Path $runtimePath 'personal-summary.sqlite3')))) {
    $value = [string]$settings.($entry[0])
    if (-not [IO.Path]::IsPathRooted($value) -or [IO.Path]::GetFullPath($value) -ne $entry[1]) { throw 'Personal configuration runtime binding failed' }
}
foreach ($key in @('gh_path','tzdata_dir')) {
    if (-not [IO.Path]::IsPathRooted([string]$settings.$key)) { throw 'Absolute gh_path and tzdata_dir required' }
}
if ($Mode -eq 'Start') {
    if (-not $task) { throw 'Install the personal task before starting it' }
    $settings | Add-Member enabled $true -Force
    Write-Settings $settings
    if (Test-Path -LiteralPath $stopPath) { Remove-Item -LiteralPath $stopPath }
    Enable-ScheduledTask -TaskName $taskName -TaskPath '\' | Out-Null
    Start-ScheduledTask -TaskName $taskName -TaskPath '\'
    @{task_name=$taskName; enabled=$true; launch_requested=$true} | ConvertTo-Json -Compress
    return
}
if (-not (Test-Path -LiteralPath $servicePath -PathType Leaf)) { throw 'personal_summary_service.py is missing' }
function Escape-Xml([string]$value) { return [Security.SecurityElement]::Escape($value) }
$boundary = (Get-Date).AddMinutes(5).ToString('yyyy-MM-ddTHH:mm:ss')
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>$(Escape-Xml $description)</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><Delay>PT20S</Delay><UserId>$currentSid</UserId></LogonTrigger>
    <TimeTrigger><Repetition><Interval>PT1H</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled></TimeTrigger>
  </Triggers>
  <Principals><Principal id="PersonalUser"><UserId>$currentSid</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><Enabled>false</Enabled><Hidden>true</Hidden><ExecutionTimeLimit>PT10M</ExecutionTimeLimit></Settings>
  <Actions Context="PersonalUser"><Exec><Command>$(Escape-Xml $PythonwPath)</Command><Arguments>$(Escape-Xml $arguments)</Arguments><WorkingDirectory>$(Escape-Xml $PSScriptRoot)</WorkingDirectory></Exec></Actions>
</Task>
"@
if ($Mode -eq 'Plan') {
    @{task_name=$taskName; description=$description; enabled=$false; xml=$xml; config=$configPath} | ConvertTo-Json -Compress
    return
}
# Reinstallation is deliberately fail closed, including when the old config was enabled.
$settings | Add-Member enabled $false -Force
Write-Settings $settings
Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Xml $xml -Force | Out-Null
Export-ScheduledTask -TaskName $taskName -TaskPath '\' | Set-Content -LiteralPath (Join-Path $runtimePath 'personal-summary-task.xml') -Encoding utf8
@{installed=$true; task_name=$taskName; enabled=$false; started=$false; executable=$PythonwPath} | ConvertTo-Json -Compress
