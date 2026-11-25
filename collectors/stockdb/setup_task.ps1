# Registers the PC daily run (run_daily.bat) as a Windows scheduled task.
#   - weekdays 20:15 (Kiwoom NXT-included bars are final after 20:10)
#   - wakes the PC from sleep to run, and runs as soon as possible after a missed start (PC off at 20:15)
# Usage (PowerShell, once):  powershell -ExecutionPolicy Bypass -File setup_task.ps1
$bat = Join-Path $PSScriptRoot 'run_daily.bat'
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c `"$bat`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At '20:15'
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName 'stockDB_daily' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Get-ScheduledTask -TaskName 'stockDB_daily' | Select-Object TaskName, State
