param(
    [Parameter(Mandatory=$true)][string]$Config,
    [ValidateSet('Install','Status','Remove')][string]$Mode = 'Install'
)
$ErrorActionPreference = 'Stop'
$packagePath = $PSScriptRoot
$configPath = (Resolve-Path -LiteralPath $Config).Path
$settingsObject = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$runtimePath = [string]$settingsObject.data_dir
if (-not [IO.Path]::IsPathRooted($runtimePath)) { throw 'Runtime path must be absolute' }
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$pythonwPath = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath -PathType Leaf)) { throw 'pythonw.exe is required for windowless startup' }
$startupPath = Join-Path $packagePath 'startup.py'
if (-not (Test-Path -LiteralPath $startupPath -PathType Leaf)) { throw 'startup.py is missing' }
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskName = 'Conversation Collector - ' + $env:USERNAME
$description = 'Local conversation collector sign-in startup; managed by collector-core/install_windows_startup.ps1'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task -and $task.Description -ne $description) { throw 'An unrelated task has this name; leaving it unchanged' }
if ($Mode -eq 'Remove') {
    if ($task) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
    @{removed=$true; task_name=$taskName; workers_unchanged=$true} | ConvertTo-Json -Compress
    exit 0
}
if ($Mode -eq 'Status') {
    if (-not $task) { @{installed=$false; task_name=$taskName} | ConvertTo-Json -Compress; exit 0 }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    @{installed=$true; task_name=$taskName; state=[string]$task.State;
      last_result=$info.LastTaskResult; executable=$task.Actions[0].Execute;
      arguments=$task.Actions[0].Arguments; logon_type=[string]$task.Principal.LogonType;
      run_level=[string]$task.Principal.RunLevel} | ConvertTo-Json -Compress
    exit 0
}
$actionArgs = '"' + $startupPath + '" --config "' + $configPath + '"'
$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument $actionArgs -WorkingDirectory $packagePath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentSid
$trigger.Delay = 'PT20S'
$principal = New-ScheduledTaskPrincipal -UserId $currentSid -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$registration = @{TaskName=$taskName; Action=$action; Trigger=$trigger; Principal=$principal;
    Settings=$taskSettings; Description=$description}
if ($task) { $registration.Force=$true }
Register-ScheduledTask @registration | Out-Null
Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath (Join-Path $runtimePath 'windows-startup-task.xml') -Encoding utf8
@{installed=$true; task_name=$taskName; trigger='current_user_logon'; delay_seconds=20;
  executable=$pythonwPath; run_level='Limited'; interactive_desktop_control=$false} | ConvertTo-Json -Compress
