$ErrorActionPreference = 'Stop'

$sysprepExe = Join-Path $env:SystemRoot 'System32\Sysprep\Sysprep.exe'
if (-not (Test-Path $sysprepExe)) {
  throw "Sysprep executable was not found at $sysprepExe."
}

$administratorPassword = $env:PACKER_WINRM_PASSWORD
if (-not $administratorPassword) {
  throw 'PACKER_WINRM_PASSWORD is required for sysprep unattend generation.'
}

function ConvertTo-PowerShellSingleQuotedLiteral {
  param([Parameter(Mandatory = $true)][string]$Value)
  return "'$($Value.Replace("'", "''"))'"
}

$setupScriptsDir = Join-Path $env:WINDIR 'Setup\Scripts'
$setupCompleteCmd = Join-Path $setupScriptsDir 'SetupComplete.cmd'
$setupCompletePs1 = Join-Path $setupScriptsDir 'SetupComplete.ps1'
$administratorPasswordLiteral = ConvertTo-PowerShellSingleQuotedLiteral -Value $administratorPassword
New-Item -Path $setupScriptsDir -ItemType Directory -Force | Out-Null

$setupCompleteScript = @"
`$ErrorActionPreference = 'Stop'
`$administratorPassword = ConvertTo-SecureString $administratorPasswordLiteral -AsPlainText -Force
Enable-LocalUser -Name 'Administrator'
Set-LocalUser -Name 'Administrator' -Password `$administratorPassword
net accounts /lockoutthreshold:0 | Out-Null
Set-Service -Name WinRM -StartupType Automatic
Start-Service -Name WinRM
Set-Item -Path WSMan:\localhost\Service\Auth\Basic -Value `$true
Set-Item -Path WSMan:\localhost\Service\AllowUnencrypted -Value `$true
Set-NetFirewallRule -DisplayGroup 'Windows Remote Management' -Enabled True -Profile Any -Action Allow
if (-not (Get-NetFirewallRule -Name 'runnerctl-winrm-http' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name 'runnerctl-winrm-http' -DisplayName 'RunnerCtl WinRM HTTP' -Direction Inbound -Protocol TCP -LocalPort 5985 -Action Allow -Profile Any | Out-Null
}
Remove-Item -LiteralPath (Join-Path `$env:WINDIR 'Temp\packer-sysprep-unattend.xml') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath `$PSCommandPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path `$PSScriptRoot 'SetupComplete.cmd') -Force -ErrorAction SilentlyContinue
"@

$setupCompleteCommand = @'
@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%WINDIR%\Setup\Scripts\SetupComplete.ps1" >> "%WINDIR%\Temp\packer-setupcomplete.log" 2>>&1
exit /b 0
'@

Set-Content -Path $setupCompletePs1 -Value $setupCompleteScript -Encoding UTF8 -Force
Set-Content -Path $setupCompleteCmd -Value $setupCompleteCommand -Encoding ASCII -Force

$escapedAdministratorPassword = [System.Security.SecurityElement]::Escape($administratorPassword)
$unattendPath = Join-Path $env:WINDIR 'Temp\packer-sysprep-unattend.xml'
$unattend = @"
<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="generalize">
    <component name="Microsoft-Windows-Security-SPP" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <SkipRearm>1</SkipRearm>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Deployment" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <RunSynchronous>
        <RunSynchronousCommand wcm:action="add">
          <Order>1</Order>
          <Description>Finalize runner bootstrap</Description>
          <Path>cmd.exe /c powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%WINDIR%\Setup\Scripts\SetupComplete.ps1"</Path>
        </RunSynchronousCommand>
      </RunSynchronous>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-International-Core" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <InputLocale>0409:00000409</InputLocale>
      <SystemLocale>en-US</SystemLocale>
      <UILanguage>en-US</UILanguage>
      <UserLocale>en-US</UserLocale>
    </component>
    <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <AutoLogon>
        <Enabled>true</Enabled>
        <Username>Administrator</Username>
        <Password>
          <Value>$escapedAdministratorPassword</Value>
          <PlainText>true</PlainText>
        </Password>
        <LogonCount>1</LogonCount>
      </AutoLogon>
      <OOBE>
        <HideEULAPage>true</HideEULAPage>
        <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
        <NetworkLocation>Work</NetworkLocation>
        <ProtectYourPC>3</ProtectYourPC>
        <SkipMachineOOBE>true</SkipMachineOOBE>
        <SkipUserOOBE>true</SkipUserOOBE>
      </OOBE>
      <UserAccounts>
        <AdministratorPassword>
          <Value>$escapedAdministratorPassword</Value>
          <PlainText>true</PlainText>
        </AdministratorPassword>
      </UserAccounts>
      <FirstLogonCommands>
        <SynchronousCommand wcm:action="add">
          <Order>1</Order>
          <Description>Finalize runner bootstrap</Description>
          <CommandLine>cmd.exe /c if exist "%WINDIR%\Setup\Scripts\SetupComplete.ps1" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%WINDIR%\Setup\Scripts\SetupComplete.ps1"</CommandLine>
          <RequiresUserInput>false</RequiresUserInput>
        </SynchronousCommand>
      </FirstLogonCommands>
    </component>
  </settings>
</unattend>
"@

Set-Content -Path $unattendPath -Value $unattend -Encoding UTF8 -Force
net accounts /lockoutthreshold:0 | Out-Null
$sysprep = Start-Process -FilePath $sysprepExe -ArgumentList @('/oobe', '/generalize', '/shutdown', '/mode:vm', '/quiet', "/unattend:$unattendPath") -PassThru
if (-not $sysprep.WaitForExit(120000)) {
  throw 'Sysprep did not exit within 120 seconds.'
}
$acceptedSysprepExitCodes = @(0, 267014)
if ($sysprep.ExitCode -eq 16001) {
  Write-Host "Sysprep returned exit code $($sysprep.ExitCode) after accepting the shutdown request."
  exit 0
}
if ($sysprep.ExitCode -notin $acceptedSysprepExitCodes) {
  foreach ($logPath in @(
    (Join-Path $env:WINDIR 'System32\Sysprep\Panther\setuperr.log'),
    (Join-Path $env:WINDIR 'System32\Sysprep\Panther\setupact.log')
  )) {
    if (Test-Path $logPath) {
      Write-Host "===== $logPath ====="
      Get-Content -Path $logPath -Tail 120
    }
  }
  throw "Sysprep failed with exit code $($sysprep.ExitCode)."
}
Start-Sleep -Seconds 15
