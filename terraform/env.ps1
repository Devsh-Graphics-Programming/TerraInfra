Get-Content ".\.env" |
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