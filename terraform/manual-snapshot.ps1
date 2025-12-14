param(
  [Parameter(Mandatory = $false)][string]$Name = "",
  [Parameter(Mandatory = $false)][int]$TtlHours = 24
)

$createdAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
if (-not $Name) {
  $Name = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
}

$path = Join-Path $PSScriptRoot "manual-snapshots.auto.tfvars.json"

if (Test-Path $path) {
  $data = Get-Content $path -Raw | ConvertFrom-Json
} else {
  $data = New-Object psobject
}

if (-not ($data.PSObject.Properties.Name -contains "manual_snapshots")) {
  $data | Add-Member -MemberType NoteProperty -Name manual_snapshots -Value (New-Object psobject) -Force
}

$manualSnapshots = $data.manual_snapshots
if (-not $manualSnapshots) {
  $manualSnapshots = New-Object psobject
  $data.manual_snapshots = $manualSnapshots
}

$entry = New-Object psobject
$entry | Add-Member -MemberType NoteProperty -Name created_at -Value $createdAt -Force
$entry | Add-Member -MemberType NoteProperty -Name ttl_hours -Value $TtlHours -Force
$manualSnapshots | Add-Member -MemberType NoteProperty -Name $Name -Value $entry -Force

$data | ConvertTo-Json -Depth 10 | Set-Content -Path $path -Encoding UTF8

Write-Host "Added manual snapshot request: $Name (ttl_hours=$TtlHours, created_at=$createdAt)"
Write-Host "Run from terraform/: terraform apply -var-file=manual-snapshots.auto.tfvars.json"
