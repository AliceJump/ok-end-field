# Windows Task Scheduler

Back: [Documentation home](index.md) / [README](https://github.com/AliceJump/ok-end-field/blob/master/README.md)

ok-ef provides [daily task external commands](./daily-tasks.md) and built-in scheduled-task support.

For more freedom, you can use the Windows Task Scheduler.

### 1. Create pre-hook and post-hook files from the templates

The following `终末地.pre.ps1` cleans up old logs before execution and optionally starts the game early to wait for a hot update. Modify the paths and save; the examples are compatible with the built-in Windows PowerShell 5.1:

``` pwsh
$ErrorActionPreference = "Stop"
$efDir = "C:\Program Files\Hypergryph Launcher\games\Endfield Game"
$okefDir = "C:\ok-ef"
$workingDir = Join-Path $okefDir "data\apps\ok-ef\working"
$hotUpdateMinutes = 5 # set to 0 to skip early startup

if (-not (Test-Path -LiteralPath $workingDir -PathType Container)) {
  throw "ok-ef working directory does not exist: $workingDir"
}

$logDir = Join-Path $workingDir "logs"
if (Test-Path -LiteralPath $logDir -PathType Container) {
  $logs = @(Get-ChildItem -LiteralPath $logDir -File)
  if ($logs.Count -gt 15) {
    $cutoff = (Get-Date).AddDays(-30)
    $logs | Where-Object { $_.CreationTime -lt $cutoff } | Remove-Item -Force
  }

  $currentLog = Join-Path $logDir "ok-script.log"
  if (Test-Path -LiteralPath $currentLog -PathType Leaf) {
    $timestamp = (Get-Item -LiteralPath $currentLog).CreationTime.ToString("yyyy-MM-dd.HH-mm-ss")
    Move-Item -LiteralPath $currentLog -Destination "$currentLog.$timestamp.log" -Force
  }
}

if ($hotUpdateMinutes -gt 0) {
  $gameExe = Join-Path $efDir "Endfield.exe"
  if (-not (Test-Path -LiteralPath $gameExe -PathType Leaf)) {
    throw "Game executable does not exist: $gameExe"
  }
  Start-Process -FilePath $gameExe -WorkingDirectory $efDir
  Start-Sleep -Seconds ($hotUpdateMinutes * 60)
  Get-Process -Name "Endfield" -ErrorAction SilentlyContinue | Stop-Process -Force
}

# Add your custom code here (e.g. message notification)

```

The following `终末地.post.ps1` waits for the task to exit, ends the relevant processes on timeout, and archives logs and screenshots. `$maxMinutes` includes the pre-script's hot-update time:

``` pwsh
$okefDir = "C:\ok-ef"
$workingDir = Join-Path $okefDir "data\apps\ok-ef\working"
$maxMinutes = 44
$hotUpdateMinutes = 5
$deadline = (Get-Date).AddMinutes([Math]::Max(1, $maxMinutes - $hotUpdateMinutes))

# `cmd /c start` is non-blocking; leave startup time for ok-ef and the game process.
Start-Sleep -Seconds 5
do {
  $gameProcesses = @(Get-Process -Name "Endfield" -ErrorAction SilentlyContinue)
  $okefProcesses = @(Get-Process -Name "ok-ef" -ErrorAction SilentlyContinue)
  if ($gameProcesses.Count -eq 0 -and $okefProcesses.Count -eq 0) { break }
  Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)

$timedOut = $gameProcesses.Count -gt 0 -or $okefProcesses.Count -gt 0
if ($timedOut) {
  $gameProcesses | Stop-Process -Force
  $okefProcesses | Stop-Process -Force
  $message = "Execution timed out; terminated the still-running game or ok-ef processes."
} else {
  $message = "Execution finished."
}

$logPath = Join-Path $workingDir "logs\ok-script.log"
$version = ""
if (Test-Path -LiteralPath $logPath -PathType Leaf) {
  $logLines = Get-Content -LiteralPath $logPath -Encoding UTF8
  $summaryPatterns = @("已完成的任务列表", "已失败的任务列表", "已跳过的任务列表", "未处理的任务列表", "当前失败的任务")
  foreach ($pattern in $summaryPatterns) {
    $line = $logLines | Select-String -Pattern $pattern | Select-Object -Last 1
    if ($null -ne $line) { $message += "`n`n$line" }
  }
  $versionLine = $logLines | Select-String -Pattern "app_version:([^,]+)" | Select-Object -Last 1
  if ($null -ne $versionLine -and $versionLine -match "app_version:([^,]+)") {
    $version = " " + $matches[1]
  }
  $timestamp = (Get-Item -LiteralPath $logPath).CreationTime.ToString("yyyy-MM-dd.HH-mm-ss")
  Move-Item -LiteralPath $logPath -Destination "$logPath.$timestamp.log" -Force
}

$screenshotDir = Join-Path $workingDir "screenshots"
$backupDir = Join-Path $workingDir "screenshots.backup"
if (Test-Path -LiteralPath $screenshotDir -PathType Container) {
  $screenshots = @(Get-ChildItem -LiteralPath $screenshotDir -File -ErrorAction SilentlyContinue)
  if ($screenshots.Count -gt 0) {
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $screenshots | Move-Item -Destination $backupDir -Force
  }
}

# Add your custom code here (e.g. send a notification using $message and $version)

```

### 2. Create and import the scheduled-task file

The scheduled-task template is as follows:

``` xml
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <SecurityDescriptor></SecurityDescriptor>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T04:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <DisallowStartOnRemoteAppSession>false</DisallowStartOnRemoteAppSession>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "终末地.pre.ps1"</Arguments>
      <WorkingDirectory>PLACEHOLDER_PATH_TO_PRE_SCRIPT_DIRECTORY</WorkingDirectory>
    </Exec>
    <Exec>
      <Command>cmd</Command>
      <Arguments>/c start "" "ok-ef.exe" -t 1 -e</Arguments>
      <WorkingDirectory>PLACEHOLDER_PATH_TO_OK_EF_APP_DIRECTORY</WorkingDirectory>
    </Exec>
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "终末地.post.ps1"</Arguments>
      <WorkingDirectory>PLACEHOLDER_PATH_TO_POST_SCRIPT_DIRECTORY</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
```

Copy the content into a new xml file, make the following changes, and save:

1. Change `PLACEHOLDER_PATH_TO_OK_EF_APP_DIRECTORY` to the absolute path of the ok-ef app.
2. Change `PLACEHOLDER_PATH_TO_PRE_SCRIPT_DIRECTORY` to the location of the `终末地.pre.ps1` file.
3. Change `PLACEHOLDER_PATH_TO_POST_SCRIPT_DIRECTORY` to the location of the `终末地.post.ps1` file.

`-t 1` runs the 1st item of `onetime_tasks` in [src/config.py](../../src/config.py), i.e. Daily Tasks. The number changes when the list order changes; verify the current registration order before changing to another number. `-e` exits ok-ef after the task finishes.

> **Index auto-correction at startup**: tasks created through ok-ef's built-in 「Scheduled Tasks」 feature (the 「Scheduled Tasks」 tab in the GUI) store their `-t` argument in `configs/schedule_tasks_cache.json` and in the Windows scheduled task. On every app startup, it reads this app's tasks (`\ok-ef\`) from the cache, compares each name against the current `onetime_tasks` order, automatically rewrites `-t` to the correct current index, and syncs the Windows scheduled task. So after reordering `onetime_tasks` you don't need to fix the number manually — the next startup corrects it automatically; if this run was triggered by the scheduled task, it also corrects before running, ensuring the right task runs. Other ok-* apps' tasks (e.g. `\ok-gf2\`) are not modified.

Use `Win + R` and run `taskschd.msc` to open the Task Scheduler. Click `Action > Import Task` to add the xml file above. Change the `Name` and click `OK`.

(After creation a scheduled task cannot be renamed or moved; to modify it, right-click the task, delete it, and re-import. The xml file above is no longer needed after import and can be deleted.)

The scheduled task is now created. At 4:00 AM every day, the computer automatically runs ok-ef.

### 3. Modify the scheduled task

#### Basic usage

Use `Win + R` and run `taskschd.msc` to open the Task Scheduler, then right-click the scheduled task.

- Click `Run` to run the scheduled task manually.
- Click `Stop` to stop a running scheduled task. Note that ok-ef runs non-blocking and must be closed manually.
