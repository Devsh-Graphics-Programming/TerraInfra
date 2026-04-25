def call(Map args = [:]) {
  def suite = args.suite?.toString()?.trim()
  if (!(suite in ['public', 'private'])) {
    error('dittMaterializeSceneData supports only public or private suites.')
  }

  def gitObjectCache = args.gitObjectCache
  if (!(gitObjectCache instanceof Map) || !gitObjectCache.api_url || !(gitObjectCache.stores instanceof List)) {
    error('Runner lease did not include a git object cache description.')
  }

  def requireSha = { Object value, String name ->
    def text = value?.toString()?.trim()
    if (!(text ==~ /[0-9a-fA-F]{7,40}/)) {
      error(name + ' must be a 7 to 40 character hexadecimal commit SHA.')
    }
    text.toLowerCase()
  }

  def repositories = []
  if (suite == 'public') {
    repositories << [id: 'nabla-media-public', commit: requireSha(args.mediaCommit, 'MEDIA_COMMIT')]
    repositories << [id: 'nabla-ci-public', commit: requireSha(args.publicReferencesCommit, 'PUBLIC_REFERENCES_COMMIT')]
  } else {
    repositories << [id: 'ditt-reference-scenes', commit: requireSha(args.dittScenesCommit, 'DITT_SCENES_COMMIT')]
    repositories << [id: 'ditt-reference-renders', commit: requireSha(args.privateReferencesCommit, 'PRIVATE_REFERENCES_COMMIT')]
  }

  writeJSON file: 'git-object-cache.json', json: gitObjectCache
  writeJSON file: 'scene-git-request.json', json: [
    suite: suite,
    repositories: repositories
  ]

  writeFile file: 'materialize-scenes-from-git.ps1', text: [
    '$ErrorActionPreference = "Stop"',
    '$ProgressPreference = "SilentlyContinue"',
    '$workspace = $env:WORKSPACE',
    '$cache = Get-Content -LiteralPath (Join-Path $workspace "git-object-cache.json") | ConvertFrom-Json',
    '$request = Get-Content -LiteralPath (Join-Path $workspace "scene-git-request.json") | ConvertFrom-Json',
    '$package = Get-Content -LiteralPath (Join-Path $workspace "package-info.json") | ConvertFrom-Json',
    '$toolsRoot = Join-Path $workspace "tools"',
    'function Resolve-GitExe {',
    '  $command = Get-Command git.exe -ErrorAction SilentlyContinue',
    '  if ($command) { return $command.Source }',
    '  $url = [string]$cache.git_client_url',
    '  if ([string]::IsNullOrWhiteSpace($url)) { throw "git.exe is missing and git_object_cache.git_client_url is not configured." }',
    '  $uri = [Uri]$url',
    '  if (($uri.Scheme -ne "http") -and ($uri.Scheme -ne "https")) { throw "git_object_cache.git_client_url must be an HTTP(S) URL." }',
    '  $zipPath = Join-Path $toolsRoot "MinGit.zip"',
    '  $gitRoot = Join-Path $toolsRoot "Git"',
    '  New-Item -ItemType Directory -Path $toolsRoot -Force | Out-Null',
    '  if (Test-Path -LiteralPath $gitRoot) { Remove-Item -LiteralPath $gitRoot -Recurse -Force }',
    '  New-Item -ItemType Directory -Path $gitRoot -Force | Out-Null',
    '  Write-Host ("git.exe not found in image; downloading Git client from {0}." -f $url)',
    '  Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing',
    '  $expectedSha256 = [string]$cache.git_client_sha256',
    '  if (-not [string]::IsNullOrWhiteSpace($expectedSha256)) {',
    '    $actualSha256 = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()',
    '    if ($actualSha256 -ne $expectedSha256.ToLowerInvariant()) { throw "Downloaded Git client SHA-256 mismatch." }',
    '  }',
    '  Expand-Archive -LiteralPath $zipPath -DestinationPath $gitRoot -Force',
    '  $candidates = Get-ChildItem -LiteralPath $gitRoot -Filter git.exe -Recurse -ErrorAction SilentlyContinue',
    '  $gitExe = $candidates | Where-Object { (Split-Path -Leaf (Split-Path -Parent $_.FullName)) -ieq "cmd" } | Select-Object -First 1',
    '  if (-not $gitExe) { $gitExe = $candidates | Select-Object -First 1 }',
    '  if (-not $gitExe) { throw "Downloaded Git client did not contain git.exe." }',
    '  $env:PATH = (Split-Path -Parent $gitExe.FullName) + ";" + $env:PATH',
    '  return $gitExe.FullName',
    '}',
    '$gitExe = Resolve-GitExe',
    '& $gitExe --version',
    'if ($LASTEXITCODE -ne 0) { throw "git.exe did not run successfully." }',
    '$apiUrl = ([string]$cache.api_url).TrimEnd("/") + "/api/v1/fetch"',
    '$fetchBody = @{ repositories = @($request.repositories | ForEach-Object { @{ id = $_.id; commit = $_.commit } }) } | ConvertTo-Json -Depth 6',
    'Write-Host ("Requesting local Git object cache for {0} repositories." -f @($request.repositories).Count)',
    '$fetch = Invoke-RestMethod -Uri $apiUrl -Method Post -ContentType "application/json" -Body $fetchBody',
    'if ($fetch.status -ne "ok") { throw "Git object cache fetch failed." }',
    '$fetchedById = @{}',
    'foreach ($repo in $fetch.repositories) { $fetchedById[$repo.id] = $repo }',
    'function Get-FetchedRepo {',
    '  param([Parameter(Mandatory = $true)][string] $Id)',
    '  if (-not $fetchedById.ContainsKey($Id)) { throw "Git cache response did not contain repository: $Id" }',
    '  return $fetchedById[$Id]',
    '}',
    'function Sync-GitWorktree {',
    '  param(',
    '    [Parameter(Mandatory = $true)][string] $Id,',
    '    [Parameter(Mandatory = $true)][string] $Target',
    '  )',
    '  $repo = Get-FetchedRepo -Id $Id',
    '  if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }',
    '  New-Item -ItemType Directory -Path $Target -Force | Out-Null',
    '  & $gitExe init --initial-branch=main $Target | Write-Host',
    '  if ($LASTEXITCODE -ne 0) { throw "git init failed for $Id." }',
    '  & $gitExe -C $Target config core.longpaths true',
    '  & $gitExe -C $Target config core.autocrlf false',
    '  & $gitExe -C $Target config advice.detachedHead false',
    '  & $gitExe -C $Target remote add origin $repo.git_url',
    '  Write-Host ("Fetching {0} from local cache ref {1}." -f $Id, $repo.ref)',
    '  & $gitExe -C $Target fetch --depth=1 origin $repo.ref',
    '  if ($LASTEXITCODE -ne 0) { throw "git fetch failed for $Id." }',
    '  & $gitExe -C $Target checkout --force FETCH_HEAD',
    '  if ($LASTEXITCODE -ne 0) { throw "git checkout failed for $Id." }',
    '  & $gitExe -C $Target clean -fdx',
    '  if ($LASTEXITCODE -ne 0) { throw "git clean failed for $Id." }',
    '  return [pscustomobject]@{ id = $Id; target = $Target; commit = $repo.commit; gitUrl = $repo.git_url }',
    '}',
    '$examplesRoot = Resolve-Path -LiteralPath (Join-Path $package.bin "..\\..")',
    '$mediaRoot = Join-Path $examplesRoot "media"',
    '$sourcesRoot = Join-Path $workspace "scene-sources"',
    'if (Test-Path -LiteralPath $sourcesRoot) { Remove-Item -LiteralPath $sourcesRoot -Recurse -Force }',
    'New-Item -ItemType Directory -Path $sourcesRoot -Force | Out-Null',
    '$materialized = @()',
    '$suite = [string]$request.suite',
    'if ($suite -eq "public") {',
    '  $materialized += Sync-GitWorktree -Id "nabla-media-public" -Target $mediaRoot',
    '  $ciRoot = Join-Path $sourcesRoot "nabla-ci-public"',
    '  $materialized += Sync-GitWorktree -Id "nabla-ci-public" -Target $ciRoot',
    '  $sceneList = Join-Path $mediaRoot "mitsuba\\public_test_scenes.txt"',
    '  $referenceDir = Join-Path $ciRoot "22.RaytracedAO\\references\\public"',
    '} elseif ($suite -eq "private") {',
    '  if (Test-Path -LiteralPath $mediaRoot) { Remove-Item -LiteralPath $mediaRoot -Recurse -Force }',
    '  New-Item -ItemType Directory -Path $mediaRoot -Force | Out-Null',
    '  $dittScenesRoot = Join-Path $mediaRoot "Ditt-Reference-Scenes"',
    '  $materialized += Sync-GitWorktree -Id "ditt-reference-scenes" -Target $dittScenesRoot',
    '  $referenceDir = Join-Path $sourcesRoot "ditt-reference-renders"',
    '  $materialized += Sync-GitWorktree -Id "ditt-reference-renders" -Target $referenceDir',
    '  $sceneList = Join-Path $dittScenesRoot "private_test_scenes.txt"',
    '} else {',
    '  throw "Unsupported suite: $suite"',
    '}',
    'if (-not (Test-Path -LiteralPath $sceneList)) { throw "Scene list was not materialized: $sceneList" }',
    'if (-not (Test-Path -LiteralPath $referenceDir)) { throw "Reference directory was not materialized: $referenceDir" }',
    '$info = [pscustomobject]@{',
    '  suite = $suite',
    '  commit = (@($materialized | ForEach-Object { $_.commit }) -join ",")',
    '  root = $mediaRoot',
    '  sceneList = $sceneList',
    '  manifest = ""',
    '  referenceDir = $referenceDir',
    '  repositories = $materialized',
    '  cacheApiUrl = $cache.api_url',
    '}',
    '$info | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $workspace "scene-cache-info.json") -Encoding UTF8',
    'Write-Host ("Materialized {0} scene data. sceneList={1}, referenceDir={2}" -f $suite, $sceneList, $referenceDir)'
  ].join('\n')

  powershell './materialize-scenes-from-git.ps1'
}
