# Registers the PC daily runs (run_daily.bat) as Windows scheduled tasks.
#   stockDB_close  weekdays 15:40  regular-session close (REG) -> dashboard updates ~16:37
#   stockDB_daily  weekdays 20:15  final Kiwoom daily bars incl. after-hours/NXT (record only)
# Both wake the PC from sleep, and run as soon as possible after a missed start (PC off at that time).
# Usage (PowerShell, once):  powershell -ExecutionPolicy Bypass -File setup_task.ps1
$bat = Join-Path $PSScriptRoot 'run_daily.bat'
$days = 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
foreach ($t in @(@{Name = 'stockDB_close'; At = '15:40'; Arg = 'close'}, @{Name = 'stockDB_daily'; At = '20:15'; Arg = ''})) {
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"`"$bat`" $($t.Arg)`""
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $t.At
    Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
}
Get-ScheduledTask -TaskName 'stockDB_*' | Select-Object TaskName, State, @{n = 'At'; e = {$_.Triggers[0].StartBoundary}}
