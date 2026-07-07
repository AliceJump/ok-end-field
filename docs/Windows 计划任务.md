# Windows 计划任务

ok-ef 提供了 [日常任务开始时执行结尾外部命令](./日常任务.md#执行结尾外部命令) 和 内置计划任务 功能。

如果想要更高的自由度，可以使用 Windows 计划任务。

### 1. 从模版创建 pre-hook 和 post-hook 文件

模板文件 `终末地.pre.ps1` 执行前的行为，按需修改 `input` 和 `自定义代码` 部分并保存到新文件，记住位置后面会用到：

``` pwsh
# input
#
# 游戏位置
$ef_dir = "C:\Program Files\Hypergryph Launcher\games\Endfield Game"
# ok-ef app 位置
$okef_dir = "C:\ok-ef" 
# 最大执行时间
$minute = 44
# 热更新预留时间，包含在最大执行时间内，若需关闭热更新则设0
$hot_update_minute = 5

# display
Write-Output "$minute minute(s) will be used"

# clean log
$wkdir = $okef_dir + "\data\apps\ok-ef\working";
$log_file_list = Get-ChildItem -Path ".\logs" -File
if ($log_file_list -and $log_file_list.Count -gt 15) {
  $death_date = (Get-Date).AddDays(-30)
  $log_file_list | Where-Object { $_.CreationTime -lt $death_date } | Remove-Item -Force
}

# move log
Set-Location $wkdir;
if (Test-Path -Path '.\logs\ok-script.log') {
  Move-Item -Force '.\logs\ok-script.log' ('.\logs\ok-script.log.' + (Get-Item '.\logs\ok-script.log').CreationTime.ToString('yyyy-MM-dd.HH-mm-ss') + '.log');
}

# update game if needed
if ($hot_update_minute -gt 0) {
  Set-Location $ef_dir
  Start-Process -FilePath "Endfield.exe"
  Start-Sleep($hot_update_minute  * 60);
  $ef_pid = (Get-Process | Where-Object { $_.Name -match 'Endfield' } | Select-Object -First 1).Id;
  if($ef_pid) {
    Stop-Process -Force $ef_pid;
  }
}

# 在这里加入 自定义代码（比如消息通知）

```

模板文件 `终末地.post.ps1` 执行后的行为，按需修改 `input` 和 `自定义代码` 部分并保存到新文件，记住位置后面会用到：

``` pwsh
# input
#
# 游戏位置
$ef_dir = "C:\Program Files\Hypergryph Launcher\games\Endfield Game"
# ok-ef app 位置
$okef_dir = "C:\ok-ef"
# 最大执行时间
$minute = 44
# 热更新预留时间，包含在最大执行时间内，若需关闭热更新则设0
$hot_update_minute = 5

# display
Write-Output "game locates at '$ef_dir'"

# stop
$timeout = ($minute - $hot_update_minute) * 60 - 120;
Start-Sleep(120);
while ($timeout -gt 0) { # 命令 `cmd /c start` 是非阻塞执行，启动程序后立即完成，所以需要轮询 pid 判断 ok-ef 是否执行结束
  $ef_process_list = (Get-Process | Where-Object { $_.Name -match 'Endfield' });
  $okef_process_list = (Get-Process | Where-Object { $_.MainWindowTitle -match 'ok-ef' });
  if (!($ef_process_list -or $okef_process_list)) {
    break;
  }
  Start-Sleep(1);
  $timeout -= 1;
}
$ef_process_list = (Get-Process | Where-Object { $_.Name -match 'Endfield' });
if ($ef_process_list) {
  Stop-Process -Force $ef_process_list;
  Start-Sleep(1);
}
$okef_process_list = (Get-Process | Where-Object { $_.MainWindowTitle -match 'ok-ef' });
if ($okef_process_list) {
  Stop-Process -Force $okef_process_list;
  Start-Sleep(1);
}

# get error message
$wkdir = $okef_dir + "\data\apps\ok-ef\working";
Set-Location $wkdir;
function Get-Message {
  if ($ef_process_list -or $okef_process_list) {
    # screenshot
    Add-Type -AssemblyName System.Windows.Forms
    $screen = [System.Windows.Forms.Screen]::PrimaryScreen
    $bitmap = New-Object System.Drawing.Bitmap $screen.Bounds.Width, $screen.Bounds.Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($screen.Bounds.X, $screen.Bounds.Y, 0, 0, $bitmap.Size)
    $time = Get-Date -Format "yyyyMMdd_HHmmss_fff"
    $bitmap.Save(".\screenshots\$time.png")
    $graphics.Dispose()
    $bitmap.Dispose()
    #
    return "执行超时。\n\n" + ($ef_process_list ? "游戏未关闭。" : "") + ($okef_process_list ? "ok-ef 未关闭。" : "");
  }
  #
  $success = (Get-Content -Encoding UTF8 '.\logs\ok-script.log' | Select-String -Pattern '已完成的任务列表' | Select-Object -Last 1);
  $failure = (Get-Content -Encoding UTF8 '.\logs\ok-script.log' | Select-String -Pattern '已失败的任务列表' | Select-Object -Last 1);
  $skipped = (Get-Content -Encoding UTF8 '.\logs\ok-script.log' | Select-String -Pattern '已跳过的任务列表' | Select-Object -Last 1);
  $unhandled = (Get-Content -Encoding UTF8 '.\logs\ok-script.log' | Select-String -Pattern '未处理的任务列表' | Select-Object -Last 1);
  $current = (Get-Content -Encoding UTF8 '.\logs\ok-script.log' | Select-String -Pattern '当前失败的任务' | Select-Object -Last 1);
  $exception = (Get-Content -Encoding UTF8 '.\logs\ok-script.log' | Select-String -Pattern '发生异常，终止游戏' | Select-Object -Last 1);
  if ([string]::IsNullOrWhiteSpace($current) -and [string]::IsNullOrWhiteSpace($failure) -and [string]::IsNullOrWhiteSpace($exception)) {
    $message = "执行成功。"
  } else {
    $message = "执行失败。"
  }
  if (-not [string]::IsNullOrWhiteSpace($success)) {
    $message += ("\n\n" + $success)
  }
  if (-not [string]::IsNullOrWhiteSpace($failure)) {
    $message += ("\n\n" + $failure)
  }
    if (-not [string]::IsNullOrWhiteSpace($skipped)) {
    $message += ("\n\n" + $skipped)
  }
  if (-not [string]::IsNullOrWhiteSpace($unhandled)) {
    $message += ("\n\n" + $unhandled)
  }
  if (-not [string]::IsNullOrWhiteSpace($current)) {
    $message += ("\n\n" + $current)
  }
  return $message
}
$message = Get-Message

# get version
$version_line = (Get-Content -Encoding UTF8 '.\logs\ok-script.log' | Select-String -Pattern 'app_version:([^,]+)' | Select-Object -Last 1)
if ($version_line -match 'app_version:([^,]+)') {
  $version = " " + $matches[1]
} else {
  $version = ""
}

# move log
Set-Location $wkdir;
if (Test-Path -Path '.\logs\ok-script.log') {
  Move-Item -Force '.\logs\ok-script.log' ('.\logs\ok-script.log.' + (Get-Item '.\logs\ok-script.log').CreationTime.ToString('yyyy-MM-dd.HH-mm-ss') + '.log');
}

# move screenshot
Set-Location $wkdir;
if ((Test-Path "screenshots") && (Get-ChildItem -Path "screenshots" -Force -ErrorAction SilentlyContinue)) {
  if (!(Test-Path "screenshots.backup")) {
    New-Item -ItemType Directory -Path "screenshots.backup" | Out-Null;
  }
  Move-Item -Path (Join-Path "screenshots" "*") -Destination "screenshots.backup" -Force;
}

# 在这里加入 自定义代码（比如消息通知）
#
# 1. $message 为从日志中提取的信息。
# 2. $version 为 ok-ef 版本号。
# 3. 日志移动到 <ok-ef-app>\data\apps\ok-ef\working\logs\ok-script.<creation-time>.log 。（如果日志文件超过15个，30天前创建的日志将会删除。）
# 4. 错误截图保存在 <ok-ef-app>\data\apps\ok-ef\working\screenshots.backup 。

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
      <Command>powershell</Command>
      <Arguments>-WindowStyle Hidden 终末地.pre.ps1</Arguments>
      <WorkingDirectory>TODO_PATH_TO_PRE_SCRIPT_DIRECTORY</WorkingDirectory>
    </Exec>
    <Exec>
      <Command>cmd</Command>
      <Arguments>/c start "" ok-ef.exe -t 1 -e</Arguments>
      <WorkingDirectory>TODO_PATH_TO_OK_EF_APP_DIRECTORY</WorkingDirectory>
    </Exec>
    <Exec>
      <Command>powershell</Command>
      <Arguments>-WindowStyle Hidden 终末地.post.ps1</Arguments>
      <WorkingDirectory>TODO_PATH_TO_POST_SCRIPT_DIRECTORY</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
```

复制内容到新 xml 文件，进行下列修改并保存：

1. 修改 TODO_PATH_TO_OK_EF_APP_DIRECTORY 为 ok-ef app 的绝对路径。
2. 修改 TODO_PATH_TO_PRE_SCRIPT_DIRECTORY 为 `终末地.pre.ps1` 文件所在位置。
3. 修改 TODO_PATH_TO_POST_SCRIPT_DIRECTORY 为 `终末地.post.ps1` 文件所在位置。

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
    <Select Path="Microsoft-Windows-TaskScheduler/Operational">*[System[(EventID=102)]] and *[EventData[Data[@Name='TaskName'] and (Data='TODO_TASK_PATH')]]</Select>
  </Query>
</QueryList>
```

将 TODO_TASK_PATH 替换成前序任务的路径（可以在对应计划任务的详情页 `常规` 中找到，是 `位置` 和 `名称` 用反斜杠拼接）。

一路点击 `确定` 关闭所有弹出窗口。
