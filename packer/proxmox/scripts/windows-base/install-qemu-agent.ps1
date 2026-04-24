$ErrorActionPreference = 'Stop'

$firstLogonMarkerPath = 'C:\Windows\Temp\packer-firstlogon.done'
$firstLogonLogPath = 'C:\Windows\Temp\packer-firstlogon.log'
$firstLogonDeadline = (Get-Date).AddMinutes(10)
while (-not (Test-Path $firstLogonMarkerPath) -and (Get-Date) -lt $firstLogonDeadline) {
  Start-Sleep -Seconds 5
}

if (-not (Test-Path $firstLogonMarkerPath)) {
  if (Test-Path $firstLogonLogPath) {
    Get-Content -Path $firstLogonLogPath | Write-Output
  }
  throw 'FirstLogon bootstrap did not complete before the QEMU agent provisioner timeout.'
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

$vioserialInf = Find-FirstExistingPath @(
  'vioserial\2k25\amd64\vioser.inf',
  'vioserial\w11\amd64\vioser.inf'
)
if (-not $vioserialInf) {
  throw 'vioser.inf was not found on any mounted drive.'
}

& pnputil.exe /add-driver $vioserialInf /install | Out-Host
$pnpExitCode = $LASTEXITCODE
if (@(0, 259) -notcontains $pnpExitCode) {
  throw "VirtIO serial driver install failed with exit code $pnpExitCode."
}
$global:LASTEXITCODE = 0

if (Get-Service -Name QEMU-GA -ErrorAction SilentlyContinue) {
  Set-Service -Name QEMU-GA -StartupType Automatic
  Start-Service -Name QEMU-GA
  return
}

$guestAgentMsi = Find-FirstExistingPath @('guest-agent\qemu-ga-x86_64.msi')

if (-not $guestAgentMsi) {
  throw 'qemu-ga-x86_64.msi was not found on any mounted drive.'
}

$process = Start-Process msiexec.exe -ArgumentList @('/i', $guestAgentMsi, '/qn', '/norestart') -Wait -PassThru
if ($process.ExitCode -ne 0) {
  throw "QEMU guest agent install failed with exit code $($process.ExitCode)."
}

Set-Service -Name QEMU-GA -StartupType Automatic
Start-Service -Name QEMU-GA
