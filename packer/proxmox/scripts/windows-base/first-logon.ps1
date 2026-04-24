$ErrorActionPreference = 'Stop'

$tempDir = 'C:\Windows\Temp'
$logPath = Join-Path $tempDir 'packer-firstlogon.log'
$transcriptPath = Join-Path $tempDir 'packer-firstlogon-transcript.log'
$ipconfigPath = Join-Path $tempDir 'packer-ipconfig.txt'
$netPath = Join-Path $tempDir 'packer-net.txt'
$markerPath = Join-Path $tempDir 'packer-firstlogon.done'

New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
Start-Transcript -Path $transcriptPath -Append -ErrorAction SilentlyContinue | Out-Null

function Write-Log {
  param([string]$Message)
  Add-Content -Path $logPath -Value ("[{0}] {1}" -f (Get-Date -Format s), $Message)
}

function Find-FirstExistingPath {
  param([string[]]$RelativePaths)

  foreach ($drive in Get-PSDrive -PSProvider FileSystem) {
    foreach ($relativePath in $RelativePaths) {
      $candidate = Join-Path $drive.Root $relativePath
      if (Test-Path $candidate) {
        return $candidate
      }
    }
  }

  return $null
}

try {
  Write-Log 'Starting first-logon bootstrap.'

  New-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\ServerManager' -Name DoNotOpenServerManagerAtLogon -Value 1 -PropertyType DWord -Force | Out-Null

  $vioserialInf = Find-FirstExistingPath @(
    'vioserial\2k25\amd64\vioser.inf',
    'vioserial\w11\amd64\vioser.inf'
  )
  if (-not $vioserialInf) {
    throw 'vioser.inf was not found on any mounted drive during first-logon bootstrap.'
  }

  Write-Log "Installing VirtIO serial driver from $vioserialInf"
  & pnputil.exe /add-driver $vioserialInf /install | ForEach-Object { Write-Log $_ }
  $pnpExitCode = $LASTEXITCODE
  if (@(0, 259) -notcontains $pnpExitCode) {
    throw "VirtIO serial driver install failed with exit code $pnpExitCode."
  }
  Write-Log "VirtIO serial driver install exited with code $pnpExitCode."
  $global:LASTEXITCODE = 0

  $guestAgentMsi = Find-FirstExistingPath @('guest-agent\qemu-ga-x86_64.msi')

  if (-not $guestAgentMsi) {
    throw 'qemu-ga-x86_64.msi was not found on any mounted drive during first-logon bootstrap.'
  }

  Write-Log "Installing QEMU guest agent from $guestAgentMsi"
  $process = Start-Process msiexec.exe -ArgumentList @('/i', $guestAgentMsi, '/qn', '/norestart') -Wait -PassThru
  if ($process.ExitCode -ne 0) {
    throw "QEMU guest agent install failed with exit code $($process.ExitCode)."
  }

  Set-Service -Name QEMU-GA -StartupType Automatic
  Start-Service -Name QEMU-GA
  Write-Log 'QEMU guest agent is installed and running.'

  $nonDomainProfiles = Get-NetConnectionProfile -ErrorAction SilentlyContinue |
    Where-Object { $_.NetworkCategory -ne 'DomainAuthenticated' }
  foreach ($profile in $nonDomainProfiles) {
    Set-NetConnectionProfile -InterfaceIndex $profile.InterfaceIndex -NetworkCategory Private -ErrorAction SilentlyContinue
  }

  Enable-PSRemoting -SkipNetworkProfileCheck -Force
  winrm quickconfig -q | Out-Null
  Set-Service -Name WinRM -StartupType Automatic
  Start-Service -Name WinRM
  Set-Item -Path WSMan:\localhost\Service\AllowUnencrypted -Value $true
  Set-Item -Path WSMan:\localhost\Service\Auth\Basic -Value $true
  net accounts /lockoutthreshold:0 | Out-Null
  netsh advfirewall firewall set rule group="Windows Remote Management" new enable=Yes | Out-Null
  Write-Log 'WinRM is configured.'

  ipconfig /all | Out-File -FilePath $ipconfigPath -Encoding ascii -Force
  Get-NetAdapter | Format-List * | Out-File -FilePath $netPath -Encoding ascii -Force
  New-Item -ItemType File -Path $markerPath -Force | Out-Null
  Write-Log 'First-logon bootstrap completed.'
}
catch {
  Write-Log ("First-logon bootstrap failed: {0}" -f $_.Exception.Message)
  throw
}
finally {
  Stop-Transcript | Out-Null
}
