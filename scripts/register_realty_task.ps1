<#
Register the realty update task. Interactive requires a logged-in user.
Password mode requests credentials and can run while the user is logged out.
No mode can run while the computer is powered off; StartWhenAvailable catches up.
Examples:
  .\scripts\register_realty_task.ps1
  .\scripts\register_realty_task.ps1 -LogonMode Password
  .\scripts\register_realty_task.ps1 -Unregister
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Interactive', 'Password')]
    [string]$LogonMode = 'Interactive',
    [ValidateSet('Limited', 'Highest')]
    [string]$RunLevel = 'Limited',
    [string]$TaskName = 'parser_etl_realty',
    [string]$At = '06:00',
    [switch]$Unregister
)
$ErrorActionPreference = 'Stop'
$projectDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runner = Join-Path $PSScriptRoot 'update_realty_scheduled.bat'
if ($Unregister) {
    if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled task')) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    return
}
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Runner missing: $runner"
}
$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($At, 'HH:mm', $null))
$action = New-ScheduledTaskAction -Execute $env:ComSpec `
    -Argument ('/d /s /c ""{0}""' -f $runner) -WorkingDirectory $projectDir
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12)
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($PSCmdlet.ShouldProcess("$TaskName ($LogonMode, daily $At)", 'Register scheduled task')) {
    if ($LogonMode -eq 'Password') {
        $credential = Get-Credential -UserName $userId -Message 'Account for unattended realty update'
        if ($null -eq $credential) { throw 'Credentials were not provided.' }
        $principal = New-ScheduledTaskPrincipal -UserId $credential.UserName -LogonType Password -RunLevel $RunLevel
        $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
        Register-ScheduledTask -TaskName $TaskName -InputObject $task -User $credential.UserName `
            -Password $credential.GetNetworkCredential().Password -Force | Out-Null
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel $RunLevel
        $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
        Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
    }
    Write-Output "Registered $TaskName; logon=$LogonMode; daily=$At; catch-up enabled; concurrent runs ignored."
}
