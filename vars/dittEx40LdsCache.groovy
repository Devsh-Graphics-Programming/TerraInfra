def call(Map args = [:]) {
  def cacheApiUrl = args.cacheApiUrl?.toString()?.trim()
  if (!cacheApiUrl && args.gitObjectCache instanceof Map) {
    cacheApiUrl = args.gitObjectCache.api_url?.toString()?.trim()
  }
  if (!cacheApiUrl) {
    error('Runner lease did not include a cache API URL.')
  }

  def cacheKey = args.get('cacheKey', 'ditt/ex40/lds/v1/owen_sampler_buffer.bin').toString().trim().replace('\\', '/')
  if (!(cacheKey ==~ /[A-Za-z0-9][A-Za-z0-9._\/-]*/) || cacheKey.contains('..') || cacheKey.startsWith('/') || cacheKey.endsWith('/')) {
    error('LDS cache key is invalid.')
  }

  writeJSON file: 'ex40-lds-cache.json', json: [
    apiUrl: cacheApiUrl,
    key: cacheKey
  ]

  writeFile file: 'ex40-lds-cache.ps1', text: '''
param(
  [Parameter(Mandatory = $true)][ValidateSet("Restore", "Save", "Status")][string] $Mode,
  [Parameter(Mandatory = $true)][string[]] $PackageInfoPath
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$config = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "ex40-lds-cache.json") | ConvertFrom-Json
$cacheKey = [string]$config.key
if (($cacheKey -notmatch "^[A-Za-z0-9][A-Za-z0-9._/-]*$") -or $cacheKey.Contains("..") -or $cacheKey.StartsWith("/") -or $cacheKey.EndsWith("/")) {
  throw "LDS cache key is invalid."
}
$blobUrl = ([string]$config.apiUrl).TrimEnd("/") + "/api/v1/blob/" + $cacheKey

function Get-LdsCachePath {
  param([Parameter(Mandatory = $true)][string] $InfoPath)
  $package = Get-Content -LiteralPath $InfoPath | ConvertFrom-Json
  $sharedTmp = [System.IO.Path]::GetFullPath((Join-Path $package.bin "../../tmp"))
  return (Join-Path $sharedTmp "owen_sampler_buffer.bin")
}

function Get-LocalCacheInfo {
  param([Parameter(Mandatory = $true)][string] $Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    return [pscustomobject]@{ exists = $false; path = $Path; size = 0; sha256 = "" }
  }
  $item = Get-Item -LiteralPath $Path
  $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
  return [pscustomobject]@{ exists = $true; path = $Path; size = $item.Length; sha256 = $hash }
}

function Write-LocalCacheInfo {
  param([Parameter(Mandatory = $true)][string] $Label, [Parameter(Mandatory = $true)][string] $Path)
  $info = Get-LocalCacheInfo -Path $Path
  if ($info.exists) {
    Write-Host ("LDS cache {0}: exists=true, size={1}, sha256={2}, path={3}" -f $Label, $info.size, $info.sha256, $info.path)
  } else {
    Write-Host ("LDS cache {0}: exists=false, path={1}" -f $Label, $info.path)
  }
  return $info
}

function Get-RemoteCacheInfo {
  try {
    $response = Invoke-WebRequest -Uri $blobUrl -Method Head -UseBasicParsing -ErrorAction Stop
    $shaValues = @($response.Headers["X-Content-SHA256"])
    $lengthValues = @($response.Headers["Content-Length"])
    $sha = if ($shaValues.Count -gt 0) { [string]$shaValues[0] } else { "" }
    $length = if ($lengthValues.Count -gt 0) { [string]$lengthValues[0] } else { "" }
    return [pscustomobject]@{ available = $true; exists = $true; size = $length; sha256 = $sha.ToLowerInvariant() }
  } catch {
    $statusCode = 0
    if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
      $statusCode = [int]$_.Exception.Response.StatusCode
    }
    if ($statusCode -eq 404) {
      return [pscustomobject]@{ available = $true; exists = $false; size = 0; sha256 = "" }
    }
    Write-Warning ("LDS cache remote metadata unavailable: {0}" -f $_.Exception.Message)
    return [pscustomobject]@{ available = $false; exists = $false; size = 0; sha256 = "" }
  }
}

function Restore-LdsCache {
  foreach ($infoPath in $PackageInfoPath) {
    $path = Get-LdsCachePath -InfoPath $infoPath
    $before = Write-LocalCacheInfo -Label ("before restore " + $infoPath) -Path $path
    if ($before.exists) {
      continue
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
    try {
      Invoke-WebRequest -Uri $blobUrl -OutFile $path -UseBasicParsing -ErrorAction Stop
      Write-Host ("LDS cache restored from runner cache: {0}" -f $cacheKey)
    } catch {
      $statusCode = 0
      if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
        $statusCode = [int]$_.Exception.Response.StatusCode
      }
      if ($statusCode -eq 404) {
        Write-Host ("LDS cache remote miss: {0}" -f $cacheKey)
      } else {
        Write-Warning ("LDS cache restore skipped: {0}" -f $_.Exception.Message)
      }
    }
    Write-LocalCacheInfo -Label ("after restore " + $infoPath) -Path $path | Out-Null
  }
}

function Save-LdsCache {
  $candidate = $null
  foreach ($infoPath in $PackageInfoPath) {
    $path = Get-LdsCachePath -InfoPath $infoPath
    $info = Write-LocalCacheInfo -Label ("before save " + $infoPath) -Path $path
    if ($info.exists -and -not $candidate) {
      $candidate = $info
    }
  }
  if (-not $candidate) {
    Write-Host "LDS cache save skipped because no local cache file exists."
    return
  }
  $remote = Get-RemoteCacheInfo
  if ($remote.available -and $remote.exists -and $remote.sha256 -and ($remote.sha256 -eq $candidate.sha256)) {
    Write-Host ("LDS cache save skipped because remote already has sha256={0}." -f $candidate.sha256)
    return
  }
  try {
    $response = Invoke-RestMethod -Uri $blobUrl -Method Put -InFile $candidate.path -ContentType "application/octet-stream"
    if ($response.status -ne "ok") {
      Write-Warning "LDS cache save returned a non-ok response."
      return
    }
    Write-Host ("LDS cache saved to runner cache: size={0}, sha256={1}, key={2}" -f $response.size, $response.sha256, $response.key)
  } catch {
    Write-Warning ("LDS cache save skipped: {0}" -f $_.Exception.Message)
  }
}

if ($Mode -eq "Restore") {
  Restore-LdsCache
} elseif ($Mode -eq "Save") {
  Save-LdsCache
} else {
  foreach ($infoPath in $PackageInfoPath) {
    Write-LocalCacheInfo -Label ("status " + $infoPath) -Path (Get-LdsCachePath -InfoPath $infoPath) | Out-Null
  }
}
'''
}
