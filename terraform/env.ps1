$envFile = Join-Path $PSScriptRoot ".env"
Get-Content $envFile |
  Where-Object { $_ -and $_ -notmatch '^\s*#' } |
  ForEach-Object {
    $parts = $_ -split '=', 2
    if ($parts.Count -eq 2) {
      $name  = $parts[0].Trim()
      $value = $parts[1].Trim()
      if ($name) { Set-Item -Path "Env:$name" -Value $value }
    }
  }

Write-Host "Environment variables loaded from .env"

if (-not $env:TF_VAR_owner_access_key -and $env:SCW_ACCESS_KEY) {
  $env:TF_VAR_owner_access_key = $env:SCW_ACCESS_KEY
}
