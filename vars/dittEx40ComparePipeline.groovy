def call(Map args = [:]) {
  def fixedSuite = args.suite?.toString()?.trim()
  if (!(fixedSuite in ['public', 'private'])) {
    error('dittEx40ComparePipeline requires suite public or private.')
  }

  def requireSha = { Object value, String name ->
    def text = value?.toString()?.trim()
    if (!(text ==~ /[0-9a-fA-F]{7,40}/)) {
      error(name + ' must be a 7 to 40 character hexadecimal commit SHA.')
    }
    return text.toLowerCase()
  }

  def requireNumber = { Object value, String name, int minValue, int maxValue ->
    def text = value?.toString()?.trim()
    if (!(text ==~ /[0-9]+/)) {
      error(name + ' must be numeric.')
    }
    def parsed = text as int
    if (parsed < minValue || parsed > maxValue) {
      error(name + ' is outside the allowed range.')
    }
    return parsed
  }

  def defaultPrefixForSuite = { String suite ->
    'ditt/compare/o1experimental-vs-o3/' + suite + '/latest/'
  }

  def suite = fixedSuite
  def mediaCommit = null
  def publicReferencesCommit = null
  def dittScenesCommit = null
  def privateReferencesCommit = null
  def storePrefix = null
  def shardCount = null
  def shardIndex = null
  def publish = true
  def sourceRepository = null
  def sourceBranch = null
  def sourceSha = null
  def sourceRunId = null
  def sourceRunAttempt = null
  def sourceWorkflow = null
  def sourceUrl = null
  def runnerTimeoutMinutes = null
  def runnerSummary = [:]
  def selectedSceneCount = null
  def totalSceneCount = null
  def compareFailureCount = null
  def compareTestCount = null
  def reportUrl = null
  def storePublishArtifact = null
  def buildStartedAt = System.currentTimeMillis()

  def updateBuildDescription = {
    def lines = []
    if (reportUrl) {
      lines << reportUrl
    }
    if (sourceUrl) {
      lines << ('source=' + sourceUrl)
    }
    if (sourceSha) {
      lines << ('sha=' + sourceSha.take(12))
    }
    lines << ('suite=' + suite)
    if (selectedSceneCount != null && totalSceneCount != null) {
      lines << ('scenes=' + selectedSceneCount + '/' + totalSceneCount)
    }
    if (compareFailureCount != null && compareTestCount != null) {
      lines << ('o1_vs_o3_failures=' + compareFailureCount + '/' + compareTestCount)
    }
    if (runnerSummary.vmid) {
      lines << ('vmid=' + runnerSummary.vmid)
    }
    if (runnerSummary.label) {
      lines << ('runner=' + runnerSummary.label)
    }
    if (runnerSummary.readyWallMs != null) {
      lines << ('runner_ready=' + runnerFormatDuration(runnerSummary.readyWallMs as long))
    }
    lines << ('elapsed=' + runnerFormatDuration(System.currentTimeMillis() - buildStartedAt))
    currentBuild.description = lines.findAll { it != null && it.toString().trim() }.join('<br/>')
  }

  timestamps {
    stage('Validate request') {
      runnerTimeoutMinutes = requireNumber(args.get('runnerTimeoutMinutes', '420'), 'runnerTimeoutMinutes', 10, 720)
      shardCount = requireNumber(params.SHARD_COUNT ?: args.get('shardCountDefault', '1'), 'SHARD_COUNT', 1, 64)
      shardIndex = requireNumber(params.SHARD_INDEX ?: args.get('shardIndexDefault', '0'), 'SHARD_INDEX', 0, 63)
      if (shardIndex >= shardCount) {
        error('SHARD_INDEX must be smaller than SHARD_COUNT.')
      }
      if (suite == 'public') {
        mediaCommit = requireSha(params.MEDIA_COMMIT ?: args.mediaCommitDefault, 'MEDIA_COMMIT')
        publicReferencesCommit = requireSha(params.PUBLIC_REFERENCES_COMMIT ?: args.publicReferencesCommitDefault, 'PUBLIC_REFERENCES_COMMIT')
      } else {
        dittScenesCommit = requireSha(params.DITT_SCENES_COMMIT ?: args.dittScenesCommitDefault, 'DITT_SCENES_COMMIT')
        privateReferencesCommit = requireSha(params.PRIVATE_REFERENCES_COMMIT ?: args.privateReferencesCommitDefault, 'PRIVATE_REFERENCES_COMMIT')
      }
      def rawPrefix = params.STORE_PREFIX?.trim() ?: args.defaultStorePrefix ?: defaultPrefixForSuite(suite)
      def allowedPrefixes = ['ditt/compare/o1experimental-vs-o3/' + suite + '/']
      storePrefix = storeNormalizePrefix(rawPrefix, allowedPrefixes)
      publish = params.PUBLISH == null ? (args.get('publishDefault', true) as boolean) : (params.PUBLISH as boolean)
      sourceRepository = params.SOURCE_REPOSITORY?.trim()
      sourceBranch = params.SOURCE_BRANCH?.trim()
      sourceSha = params.SOURCE_SHA?.trim()
      sourceRunId = params.SOURCE_RUN_ID?.trim()
      sourceRunAttempt = params.SOURCE_RUN_ATTEMPT?.trim()
      sourceWorkflow = params.SOURCE_WORKFLOW?.trim()
      if (sourceRepository && !(sourceRepository ==~ /[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+/)) {
        error('SOURCE_REPOSITORY must be owner/repository.')
      }
      if (sourceBranch && !(sourceBranch ==~ /[A-Za-z0-9_.\/-]+/)) {
        error('SOURCE_BRANCH contains unsupported characters.')
      }
      if (sourceSha && !(sourceSha ==~ /[0-9a-fA-F]{7,40}/)) {
        error('SOURCE_SHA must be a 7 to 40 character hexadecimal commit SHA.')
      }
      if (sourceRunId && !(sourceRunId ==~ /[0-9]+/)) {
        error('SOURCE_RUN_ID must be numeric.')
      }
      if (sourceRunAttempt && !(sourceRunAttempt ==~ /[0-9]+/)) {
        error('SOURCE_RUN_ATTEMPT must be numeric.')
      }
      if (sourceWorkflow && !(sourceWorkflow ==~ /[A-Za-z0-9_.\/ -]+/)) {
        error('SOURCE_WORKFLOW contains unsupported characters.')
      }
      if (sourceRepository && sourceRunId) {
        sourceUrl = 'https://github.com/' + sourceRepository + '/actions/runs/' + sourceRunId
        if (sourceRunAttempt) {
          sourceUrl += '/attempts/' + sourceRunAttempt
        }
        currentBuild.displayName = '#' + env.BUILD_NUMBER + ' ' + suite + ' compare ' + (sourceSha ?: sourceRunId).take(12)
        currentBuild.description = sourceUrl
        echo('Source Actions run: ' + sourceUrl)
      }
      echo('Compare suite: ' + suite + ', shard=' + shardIndex + '/' + shardCount + ', store_prefix=' + storePrefix + ', publish=' + publish)
      updateBuildDescription()
    }

    timeout(time: runnerTimeoutMinutes, unit: 'MINUTES') {
      withRunner(
        labels: args.get('labels', ['windows', 'gpu', 'nvidia', 'vulkan', 'runtime-only']),
        leaseTtlMinutes: args.get('leaseTtlMinutes', 480),
        maxReadySeconds: args.get('maxReadySeconds', 180)
      ) { runner ->
        runnerSummary = [
          label: runner.label,
          allocationMode: runner.allocation_mode,
          hostId: runner.host_id,
          node: runner.node,
          vmid: runner.vmid,
          readyWallMs: runner.ready_wall_ms,
          nodeEnterMs: runner.node_enter_ms
        ]
        updateBuildDescription()
        withFileParameter(name: 'EX40_RELEASE_PACKAGE_FILE', allowNoFile: false) {
          withFileParameter(name: 'EX40_O1_PACKAGE_FILE', allowNoFile: false) {
            withEnv([
              'SCENE_SUITE=' + suite,
              'SHARD_COUNT=' + shardCount.toString(),
              'SHARD_INDEX=' + shardIndex.toString()
            ]) {
              stage('Acquire packages') {
                writeFile file: 'acquire-compare-packages.ps1', text: '''
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
function Resolve-PackagePath {
  param([Parameter(Mandatory = $true)][string] $Base, [Parameter(Mandatory = $true)][string] $Relative, [Parameter(Mandatory = $true)][string] $Name)
  if ([System.IO.Path]::IsPathRooted($Relative)) { throw "Unsafe package manifest path: $Name" }
  $segments = $Relative.Replace([char]92, [char]47).Split([char]47)
  if ($segments -contains "..") { throw "Unsafe package manifest path: $Name" }
  $baseFull = [System.IO.Path]::GetFullPath($Base)
  $candidate = [System.IO.Path]::GetFullPath((Join-Path $baseFull $Relative))
  $prefix = $baseFull.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
  if ($candidate -ne $baseFull -and -not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Package manifest path escapes package root: $Name" }
  return $candidate
}
function Expand-EX40Package {
  param([Parameter(Mandatory = $true)][string] $Source, [Parameter(Mandatory = $true)][string] $Name)
  $zip = Join-Path $env:WORKSPACE ($Name + ".zip")
  $root = Join-Path $env:WORKSPACE ("package-" + $Name)
  Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
  Copy-Item -LiteralPath $Source -Destination $zip -Force
  $packageSize = (Get-Item -LiteralPath $zip).Length
  Write-Host ("{0} package size: {1} bytes" -f $Name, $packageSize)
  Expand-Archive -LiteralPath $zip -DestinationPath $root -Force
  $manifestFile = Get-ChildItem -LiteralPath $root -Recurse -File -Filter "EX40Runtime.json" | Sort-Object FullName | Select-Object -First 1
  if (-not $manifestFile) { throw "$Name package has no EX40Runtime.json manifest." }
  $manifest = Get-Content -LiteralPath $manifestFile.FullName -Raw | ConvertFrom-Json
  if ($manifest.schema -ne "devsh.nabla.example-runtime.v1" -or $manifest.component -ne "EX40Runtime") { throw "Unsupported EX40 runtime manifest in $Name package." }
  $manifestBase = $manifestFile.DirectoryName
  $exe = Get-Item -LiteralPath (Resolve-PackagePath -Base $manifestBase -Relative $manifest.executable -Name "executable")
  $runtimeDir = Get-Item -LiteralPath (Resolve-PackagePath -Base $manifestBase -Relative $manifest.nabla_runtime -Name "nabla_runtime")
  $dxcDir = Get-Item -LiteralPath (Resolve-PackagePath -Base $manifestBase -Relative $manifest.dxc_runtime -Name "dxc_runtime")
  $reportTemplate = Resolve-PackagePath -Base $manifestBase -Relative $manifest.report_template -Name "report_template"
  $runtimeDll = Get-ChildItem -LiteralPath $runtimeDir.FullName -File -Filter "Nabla*.dll" | Select-Object -First 1
  if (-not $runtimeDll) { throw "Nabla runtime DLL was not found in $Name package." }
  $dxcDll = Get-ChildItem -LiteralPath $dxcDir.FullName -File -Filter "dxcompiler.dll" | Select-Object -First 1
  if (-not $dxcDll) { throw "DXC runtime DLL was not found in $Name package." }
  if (-not (Test-Path -LiteralPath $reportTemplate)) { throw "Report template directory was not found in $Name package." }
  return [pscustomobject]@{ name = $Name; exe = $exe.FullName; bin = $exe.DirectoryName; runtime = $runtimeDir.FullName; dxc = $dxcDir.FullName; reportTemplate = $reportTemplate; packageSize = $packageSize; manifest = $manifestFile.FullName; buildConfig = $manifest.build_config }
}
$release = Expand-EX40Package -Source $env:EX40_RELEASE_PACKAGE_FILE -Name "release"
$o1 = Expand-EX40Package -Source $env:EX40_O1_PACKAGE_FILE -Name "o1experimental"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $env:WORKSPACE "package-release.json"), ($release | ConvertTo-Json -Depth 4), $utf8NoBom)
[System.IO.File]::WriteAllText((Join-Path $env:WORKSPACE "package-o1experimental.json"), ($o1 | ConvertTo-Json -Depth 4), $utf8NoBom)
Write-Host ("Release executable: {0}" -f $release.exe)
Write-Host ("O1experimental executable: {0}" -f $o1.exe)
'''
                powershell './acquire-compare-packages.ps1'
              }

              stage('Materialize scenes') {
                dittMaterializeSceneData(
                  suite: suite,
                  gitObjectCache: runner.git_object_cache,
                  packageInfoFile: 'package-release.json',
                  mediaCommit: mediaCommit,
                  publicReferencesCommit: publicReferencesCommit,
                  dittScenesCommit: dittScenesCommit,
                  privateReferencesCommit: privateReferencesCommit
                )
              }

              stage('Select shard') {
                writeFile file: 'select-scenes.ps1', text: '''
$ErrorActionPreference = "Stop"
$info = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "scene-cache-info.json") | ConvertFrom-Json
$commands = @()
foreach ($line in Get-Content -LiteralPath $info.sceneList) {
  $trimmed = $line.Trim()
  if ($trimmed.Length -eq 0) { continue }
  if ($trimmed.StartsWith(";") -or $trimmed.StartsWith("#")) { continue }
  $commands += $line
}
if ($commands.Count -eq 0) { throw "Scene list contains no runnable scenes." }
$shardCount = [int]$env:SHARD_COUNT
$shardIndex = [int]$env:SHARD_INDEX
$rootForList = $info.root.Replace([char]92, [char]47)
$sceneRootToken = [string][char]36 + "{SCENE_ROOT}"
$selected = @()
for ($i = 0; $i -lt $commands.Count; $i++) {
  if (($i % $shardCount) -eq $shardIndex) {
    $selected += $commands[$i].Replace($sceneRootToken, $rootForList)
  }
}
if ($selected.Count -eq 0) { throw "Shard selected no scenes." }
$selectedPath = Join-Path $env:WORKSPACE "selected-scenes.txt"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines($selectedPath, [string[]]$selected, $utf8NoBom)
$resolved = [pscustomObject]@{ suite = $info.suite; commit = $info.commit; shardIndex = $shardIndex; shardCount = $shardCount; selectedSceneCount = $selected.Count; totalSceneCount = $commands.Count; sceneList = $selectedPath; referenceDir = $info.referenceDir }
[System.IO.File]::WriteAllText((Join-Path $env:WORKSPACE "resolved-scenes.json"), ($resolved | ConvertTo-Json -Depth 4), $utf8NoBom)
Write-Host ("Selected {0}/{1} scenes for shard {2}/{3}." -f $selected.Count, $commands.Count, $shardIndex, $shardCount)
'''
                powershell './select-scenes.ps1'
                def resolved = readJSON(file: 'resolved-scenes.json', returnPojo: true)
                selectedSceneCount = (resolved.selectedSceneCount ?: 0) as int
                totalSceneCount = (resolved.totalSceneCount ?: 0) as int
                updateBuildDescription()
              }

              writeFile file: 'run-compare-variant.ps1', text: '''
param(
  [Parameter(Mandatory = $true)][string] $PackageInfoPath,
  [Parameter(Mandatory = $true)][string] $VariantName,
  [Parameter(Mandatory = $true)][string] $ReferenceDir,
  [Parameter(Mandatory = $true)][string] $OutputRelative
)
$ErrorActionPreference = "Stop"
$package = Get-Content -LiteralPath $PackageInfoPath | ConvertFrom-Json
$scenes = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "resolved-scenes.json") | ConvertFrom-Json
$publishRoot = Join-Path $env:WORKSPACE $OutputRelative
$renders = Join-Path $publishRoot "renders"
$sharedTmp = Join-Path $package.bin "../../tmp"
if (Test-Path -LiteralPath $publishRoot) { Remove-Item -LiteralPath $publishRoot -Recurse -Force }
New-Item -ItemType Directory -Path $renders -Force | Out-Null
New-Item -ItemType Directory -Path $sharedTmp -Force | Out-Null
Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $publishRoot -Recurse -Force
$env:PATH = $package.runtime + ";" + $package.dxc + ";" + $env:PATH
$log = Join-Path $env:WORKSPACE ("ex40-" + $VariantName + ".log")
Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
$runArgs = @("--scene-list", $scenes.sceneList, "--process-sensors", "RenderAllThenTerminate", "--headless", "--output-dir", $renders, "--report-dir", $publishRoot)
if ($ReferenceDir) { $runArgs += @("--reference-dir", $ReferenceDir) }
Write-Host ("Running {0}: {1}" -f $VariantName, ($runArgs -join " "))
$exitCode = 0
Push-Location -LiteralPath $package.bin
try {
  & $package.exe @runArgs 2>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { Write-Host $_ }
  $exitCode = $LASTEXITCODE
} finally {
  Pop-Location
}
$summaryPath = Join-Path $publishRoot "summary.json"
if ($exitCode -ne 0) {
  if (-not (Test-Path -LiteralPath $summaryPath)) { throw "$VariantName failed with exit code $exitCode and did not write summary.json." }
  Write-Warning ("{0} exited with code {1}; continuing because summary.json exists." -f $VariantName, $exitCode)
}
if (-not (Test-Path -LiteralPath $summaryPath)) { throw "$VariantName did not write summary.json." }
'''

              stage('Run Release O3 vs reference') {
                powershell '''
$resolved = Get-Content -LiteralPath resolved-scenes.json | ConvertFrom-Json
./run-compare-variant.ps1 -PackageInfoPath package-release.json -VariantName release-o3-vs-reference -ReferenceDir $resolved.referenceDir -OutputRelative scratch/release-o3
'''
              }

              stage('Run O1experimental vs reference') {
                powershell '''
$resolved = Get-Content -LiteralPath resolved-scenes.json | ConvertFrom-Json
./run-compare-variant.ps1 -PackageInfoPath package-o1experimental.json -VariantName o1experimental-vs-reference -ReferenceDir $resolved.referenceDir -OutputRelative scratch/o1experimental
'''
              }

              stage('Run O1experimental vs O3') {
                powershell '''
$releaseRenders = Join-Path $env:WORKSPACE "scratch/release-o3/renders"
./run-compare-variant.ps1 -PackageInfoPath package-o1experimental.json -VariantName o1experimental-vs-o3 -ReferenceDir $releaseRenders -OutputRelative publish/o1experimental-vs-o3
'''
              }

              stage('Build comparison index') {
                writeFile file: 'build-compare-index.ps1', text: '''
$ErrorActionPreference = "Stop"
$publishRoot = Join-Path $env:WORKSPACE "publish"
$scratchRoot = Join-Path $env:WORKSPACE "scratch"
$release = Get-Content -LiteralPath (Join-Path $scratchRoot "release-o3/summary.json") | ConvertFrom-Json
$o1 = Get-Content -LiteralPath (Join-Path $scratchRoot "o1experimental/summary.json") | ConvertFrom-Json
$compare = Get-Content -LiteralPath (Join-Path $publishRoot "o1experimental-vs-o3/summary.json") | ConvertFrom-Json
function Entry($Name, $Path, $Summary) {
  [pscustomobject]@{ name = $Name; path = $Path; status = $Summary.pass_status; tests = [int]$Summary.num_of_tests; failures = [int]$Summary.failure_count; buildConfig = $Summary.buildConfig }
}
$entries = @(
  (Entry "Release O3 vs reference" "release-o3-summary.json" $release),
  (Entry "O1experimental vs reference" "o1experimental-summary.json" $o1),
  (Entry "O1experimental vs O3" "o1experimental-vs-o3/" $compare)
)
$verdict = if ([int]$compare.failure_count -eq 0) { "same-within-threshold" } else { "different" }
$summary = [pscustomobject]@{ schema = "devsh.ditt.pathtracer-compare.v1"; title = "O1experimental vs O3"; suite = $env:SCENE_SUITE; verdict = $verdict; entries = $entries }
$rows = ($entries | ForEach-Object {
  "<tr><td><a href=""$($_.path)"">$($_.name)</a></td><td>$($_.status)</td><td>$($_.tests)</td><td>$($_.failures)</td><td>$($_.buildConfig)</td></tr>"
}) -join "`n"
$html = @"
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>O1experimental vs O3</title>
  <style>
    body { margin: 0; font-family: system-ui, sans-serif; color: #e8f3e8; background: #10170f; }
    main { max-width: 1180px; margin: 0 auto; padding: 40px 24px; }
    h1 { margin: 0 0 8px; font-size: 40px; }
    .meta { color: #b7c9b7; margin-bottom: 28px; }
    .badge { display: inline-block; padding: 6px 10px; border-radius: 999px; background: #263a24; color: #a6ff8a; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; background: #142012; border: 1px solid #2d3c2a; }
    th, td { padding: 14px 16px; border-bottom: 1px solid #2d3c2a; text-align: left; }
    th { color: #b7c9b7; font-size: 13px; text-transform: uppercase; letter-spacing: .04em; }
    a { color: #a6ff8a; }
  </style>
</head>
<body>
  <main>
    <h1>O1experimental vs O3</h1>
    <p class="meta">Suite: $env:SCENE_SUITE - Verdict: <span class="badge">$verdict</span></p>
    <table>
      <thead><tr><th>Report</th><th>Status</th><th>Tests</th><th>Failures</th><th>Build config</th></tr></thead>
      <tbody>$rows</tbody>
    </table>
  </main>
</body>
</html>
"@
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $publishRoot "summary.json"), ($summary | ConvertTo-Json -Depth 8), $utf8NoBom)
[System.IO.File]::WriteAllText((Join-Path $publishRoot "index.html"), $html, $utf8NoBom)
[System.IO.File]::WriteAllText((Join-Path $publishRoot "release-o3-summary.json"), ($release | ConvertTo-Json -Depth 100), $utf8NoBom)
[System.IO.File]::WriteAllText((Join-Path $publishRoot "o1experimental-summary.json"), ($o1 | ConvertTo-Json -Depth 100), $utf8NoBom)
Write-Host ("Comparison verdict: {0}; O1experimental vs O3 failures={1}/{2}." -f $verdict, $compare.failure_count, $compare.num_of_tests)
'''
                powershell './build-compare-index.ps1'
              }

              stage('Validate comparison') {
                def summary = readJSON(file: 'publish/summary.json', returnPojo: true)
                def compareEntry = summary.entries.find { it.name == 'O1experimental vs O3' }
                compareFailureCount = (compareEntry.failures ?: 0) as int
                compareTestCount = (compareEntry.tests ?: 0) as int
                updateBuildDescription()
                if (compareFailureCount > 0) {
                  unstable("O1experimental vs O3 contains ${compareFailureCount} failed comparison(s).")
                }
              }

              stage('Prepare publish') {
                if (publish) {
                  writeFile file: 'prepare-publish-zip.ps1', text: '''
$ErrorActionPreference = "Stop"
$publishRoot = Join-Path $env:WORKSPACE "publish"
$zipPath = Join-Path $env:WORKSPACE "publish.zip"
if (-not (Test-Path -LiteralPath $publishRoot)) { throw "Publish directory does not exist." }
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
$files = Get-ChildItem -LiteralPath $publishRoot -Recurse -File
if (-not $files) { throw "Publish directory is empty." }
Compress-Archive -Path (Join-Path $publishRoot "*") -DestinationPath $zipPath -Force
$zipSize = (Get-Item -LiteralPath $zipPath).Length
Write-Host ("Prepared publish.zip with {0} files, {1} bytes." -f @($files).Count, $zipSize)
'''
                  powershell './prepare-publish-zip.ps1'
                  storePublishArtifact = 'publish.zip'
                } else {
                  echo 'Publishing disabled by PUBLISH=false.'
                }
              }

              stage('Artifacts') {
                archiveArtifacts artifacts: 'package-release.json,package-o1experimental.json,scene-cache-info.json,scene-git-request.json,git-object-cache.json,resolved-scenes.json,selected-scenes.txt,ex40-release-o3-vs-reference.log,ex40-o1experimental-vs-reference.log,ex40-o1experimental-vs-o3.log,publish.zip,publish/index.html,publish/summary.json,publish/release-o3-summary.json,publish/o1experimental-summary.json,publish/o1experimental-vs-o3/summary.json', allowEmptyArchive: true, fingerprint: false
              }
            }
          }
        }
      }
    }

    stage('Publish comparison') {
      if (publish) {
        if (!storePublishArtifact) {
          error 'Store publish artifact was not prepared.'
        }
        def result = storePublishReportArtifact(storePrefix, storePublishArtifact, [
          jobs: args.get('publishJobs', 8),
          pruneAfterPublish: args.get('pruneAfterPublish', true),
          deleteAfterPublish: args.get('deletePublishArtifactAfterPublish', true)
        ])
        reportUrl = result.url
        updateBuildDescription()
      } else {
        echo 'Publishing disabled by PUBLISH=false.'
        updateBuildDescription()
      }
    }
  }
}
