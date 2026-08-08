# Windows 计划任务

返回：[文档索引](README.md) / [README](../README.md)

ok-ef 提供了 [日常任务外部命令](./日常任务.md#执行外部命令) 和内置计划任务功能。

如果想要更高的自由度，可以使用 Windows 计划任务。

### 1. 从模版创建 pre-hook 和 post-hook 文件

下面的 `终末地.pre.ps1` 用于执行前清理旧日志，并可选地提前启动游戏等待热更新。修改路径后保存；示例兼容系统自带的 Windows PowerShell 5.1：

``` pwsh
$ErrorActionPreference = "Stop"
$efDir = "C:\Program Files\Hypergryph Launcher\games\Endfield Game"
$okefDir = "C:\ok-ef"
$workingDir = Join-Path $okefDir "data\apps\ok-ef\working"
$hotUpdateMinutes = 5 # 设为 0 可跳过提前启动

if (-not (Test-Path -LiteralPath $workingDir -PathType Container)) {
  throw "ok-ef 工作目录不存在: $workingDir"
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
    throw "游戏程序不存在: $gameExe"
  }
  Start-Process -FilePath $gameExe -WorkingDirectory $efDir
  Start-Sleep -Seconds ($hotUpdateMinutes * 60)
  Get-Process -Name "Endfield" -ErrorAction SilentlyContinue | Stop-Process -Force
}

# 在这里加入自定义代码（例如消息通知）

```

下面的 `终末地.post.ps1` 等待任务退出，超时后结束相关进程，并归档日志与截图。`$maxMinutes` 包含前置脚本的热更新时间：

``` pwsh
$okefDir = "C:\ok-ef"
$workingDir = Join-Path $okefDir "data\apps\ok-ef\working"
$maxMinutes = 44
$hotUpdateMinutes = 5
$deadline = (Get-Date).AddMinutes([Math]::Max(1, $maxMinutes - $hotUpdateMinutes))

# `cmd /c start` 非阻塞，给 ok-ef 和游戏进程预留启动时间。
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
  $message = "执行超时；已终止仍在运行的游戏或 ok-ef 进程。"
} else {
  $message = "执行结束。"
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

# 在这里加入自定义代码（例如使用 $message 和 $version 发送通知）

```

### 2. 创建计划任务文件并导入

计划任务模版如下：

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

复制内容到新 xml 文件，进行下列修改并保存：

1. 修改 `PLACEHOLDER_PATH_TO_OK_EF_APP_DIRECTORY` 为 ok-ef app 的绝对路径。
2. 修改 `PLACEHOLDER_PATH_TO_PRE_SCRIPT_DIRECTORY` 为 `终末地.pre.ps1` 文件所在位置。
3. 修改 `PLACEHOLDER_PATH_TO_POST_SCRIPT_DIRECTORY` 为 `终末地.post.ps1` 文件所在位置。

`-t 1` 表示执行 [src/config.py](../src/config.py) 中 `onetime_tasks` 的第 1 项，即日常任务。列表顺序变化时编号也会变化；改成其他编号前应先核对当前注册顺序。`-e` 表示任务结束后退出 ok-ef。

> **启动时自动校正索引**：通过 ok-ef 内置「计划任务」功能创建的任务（GUI 中「计划任务」标签页），其 `-t` 参数保存在 `configs/schedule_tasks_cache.json` 与 Windows 计划任务中。应用每次启动时会读取缓存里本应用（`\ok-ef\`）的任务，用其名称对照当前 `onetime_tasks` 顺序，自动把 `-t` 改写为正确的当前索引，并同步更新 Windows 计划任务。因此重排 `onetime_tasks` 后无需手动改编号——下次启动即自动校正；若本次是由计划任务触发的，启动瞬间也会先校正再运行，保证运行到正确的任务。其余 ok-* 应用（如 `\ok-gf2\`）的任务不会被改动。

使用 `Win + R` 运行 `taskschd.msc` 打开计划任务程序。点击 `操作 > 导入任务` 加入上述 xml 文件。修改 `名称` 后点击 `确认`。

（计划任务创建后不能重命名和移动，如果想要修改，可以右键任务删除后重新导入。上述 xml 文件导入后就不再需要，可以删除。）

这样计划任务就创建好了。每天上午4点，计算机自动执行 ok-ef 。

### 3. 修改计划任务

#### 基础用法

使用 `Win + R` 运行 `taskschd.msc` 打开计划任务程序，右击上述计划任务。

- 点击 `运行` 可以手动执行计划任务。
- 点击 `停止` 可以停止正在执行的计划任务。注意 ok-ef 是非阻塞运行，需要手动关闭。
- 点击 `禁用` 可以禁止计划任务自动执行。
- 点击 `启用` 可以允许计划任务自动执行。

#### 修改执行时间和频率

使用 `Win + R` 运行 `taskschd.msc` 打开计划任务程序，双击上述计划任务，进入详情页，切换到 `触发器` ，可以修改执行时间和频率。

#### 在另一个计划任务结束后执行

如果希望 ok-ef 在另一个计划任务（比如 ok-ww）结束后执行，而不是定时执行。可以使用 `自定义触发器` 。

使用 `Win + R` 运行 `taskschd.msc` 打开计划任务程序，双击上述计划任务，进入详情页，切换到 `触发器` 。

点击 `新建`，在 `开始任务` 选择 `发生时间时` 。点击 `自定义` 和 `新建事件选择器` 。

在弹出的窗口中，切换到 `XML` 。勾选 `手动编辑查询`，在文本框中贴入：

``` xml
<QueryList>
  <Query Id="0" Path="Microsoft-Windows-TaskScheduler/Operational">
    <Select Path="Microsoft-Windows-TaskScheduler/Operational">*[System[(EventID=102)]] and *[EventData[Data[@Name='TaskName'] and (Data='PLACEHOLDER_TASK_PATH')]]</Select>
  </Query>
</QueryList>
```

将 `PLACEHOLDER_TASK_PATH` 替换成前序任务的路径（可以在对应计划任务的详情页 `常规` 中找到，是 `位置` 和 `名称` 用反斜杠拼接）。

一路点击 `确定` 关闭所有弹出窗口。
