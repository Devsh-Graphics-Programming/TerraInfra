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
  def isolateScenes = false
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
  def compareWarningCount = null
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
    if (compareWarningCount != null && compareWarningCount > 0) {
      lines << ('o1_vs_o3_warnings=' + compareWarningCount)
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
      isolateScenes = params.ISOLATE_SCENES == null ? (args.get('isolateScenesDefault', suite == 'private') as boolean) : (params.ISOLATE_SCENES as boolean)
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
      echo('Compare suite: ' + suite + ', shard=' + shardIndex + '/' + shardCount + ', store_prefix=' + storePrefix + ', isolate_scenes=' + isolateScenes + ', publish=' + publish)
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

              stage('Link O1experimental media') {
                writeFile file: 'link-o1experimental-media.ps1', text: '''
$ErrorActionPreference = "Stop"
$release = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-release.json") | ConvertFrom-Json
$o1 = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-o1experimental.json") | ConvertFrom-Json
$releaseExamplesRoot = Resolve-Path -LiteralPath (Join-Path $release.bin "..\\..")
$o1ExamplesRoot = Resolve-Path -LiteralPath (Join-Path $o1.bin "..\\..")
$releaseMedia = Join-Path $releaseExamplesRoot "media"
$o1Media = Join-Path $o1ExamplesRoot "media"
if (-not (Test-Path -LiteralPath $releaseMedia)) { throw "Release media directory was not materialized." }
if (Test-Path -LiteralPath $o1Media) {
  $existing = Get-Item -LiteralPath $o1Media -Force
  if (($existing.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    Remove-Item -LiteralPath $o1Media -Force
  } else {
    Remove-Item -LiteralPath $o1Media -Recurse -Force
  }
}
New-Item -ItemType Junction -Path $o1Media -Target $releaseMedia | Out-Null
Write-Host ("Linked O1experimental media: {0} -> {1}" -f $o1Media, $releaseMedia)
'''
                powershell './link-o1experimental-media.ps1'
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
  [Parameter(Mandatory = $true)][string] $OutputRelative,
  [string] $SceneListPath = ""
)
$ErrorActionPreference = "Stop"
$package = Get-Content -LiteralPath $PackageInfoPath | ConvertFrom-Json
$scenes = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "resolved-scenes.json") | ConvertFrom-Json
$publishRoot = Join-Path $env:WORKSPACE $OutputRelative
$renders = Join-Path $publishRoot "renders"
$sharedTmp = Join-Path $package.bin "../../tmp"
$sceneList = if ($SceneListPath) { (Resolve-Path -LiteralPath $SceneListPath).Path } else { $scenes.sceneList }
if (Test-Path -LiteralPath $publishRoot) { Remove-Item -LiteralPath $publishRoot -Recurse -Force }
New-Item -ItemType Directory -Path $renders -Force | Out-Null
New-Item -ItemType Directory -Path $sharedTmp -Force | Out-Null
Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $publishRoot -Recurse -Force
$log = Join-Path $env:WORKSPACE ("ex40-" + $VariantName + ".log")
Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
$runArgs = @("--scene-list", $sceneList, "--process-sensors", "RenderAllThenTerminate", "--headless", "--output-dir", $renders, "--report-dir", $publishRoot)
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
exit 0
'''

              writeFile file: 'run-report-comparison.ps1', text: '''
param(
  [Parameter(Mandatory = $true)][string] $BaselineReportRelative,
  [Parameter(Mandatory = $true)][string] $CandidateReportRelative,
  [Parameter(Mandatory = $true)][string] $OutputRelative
)
$ErrorActionPreference = "Stop"
$package = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-o1experimental.json") | ConvertFrom-Json
$baselineReport = [System.IO.Path]::GetFullPath((Join-Path $env:WORKSPACE $BaselineReportRelative))
$candidateReport = [System.IO.Path]::GetFullPath((Join-Path $env:WORKSPACE $CandidateReportRelative))
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $env:WORKSPACE $OutputRelative))
foreach ($path in @($baselineReport, $candidateReport)) {
  if (-not (Test-Path -LiteralPath (Join-Path $path "summary.json"))) { throw "Report summary is missing: $path" }
}
if (Test-Path -LiteralPath $outputRoot) { Remove-Item -LiteralPath $outputRoot -Recurse -Force }
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $outputRoot -Recurse -Force
$log = Join-Path $env:WORKSPACE "ex40-o1experimental-vs-o3.log"
Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
$runArgs = @(
  "--compare-reports",
  "--baseline-report", $baselineReport,
  "--candidate-report", $candidateReport,
  "--report-dir", $outputRoot,
  "--baseline-name", "Release O3",
  "--candidate-name", "O1experimental"
)
Write-Host ("Running report comparison: {0}" -f ($runArgs -join " "))
$exitCode = 0
Push-Location -LiteralPath $package.bin
try {
  & $package.exe @runArgs 2>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { Write-Host $_ }
  $exitCode = $LASTEXITCODE
} finally {
  Pop-Location
}
$summaryPath = Join-Path $outputRoot "summary.json"
if ($exitCode -ne 0) {
  if (-not (Test-Path -LiteralPath $summaryPath)) { throw "Report comparison failed with exit code $exitCode and did not write summary.json." }
  Write-Warning ("Report comparison exited with code {0}; continuing because summary.json exists." -f $exitCode)
}
if (-not (Test-Path -LiteralPath $summaryPath)) { throw "Report comparison did not write summary.json." }
exit 0
'''

              if (isolateScenes) {
                def sceneTimeoutSeconds = args.get('sceneTimeoutSeconds', 900) as int
                if (sceneTimeoutSeconds < 60 || sceneTimeoutSeconds > 7200) {
                  error('sceneTimeoutSeconds is outside the allowed range.')
                }

                stage('Prepare isolated compare') {
                  writeFile file: 'prepare-isolated-compare.ps1', text: '''
$ErrorActionPreference = "Stop"
$scenes = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "resolved-scenes.json") | ConvertFrom-Json
$sceneLines = @(Get-Content -LiteralPath $scenes.sceneList | Where-Object { $_.Trim().Length -gt 0 })
if ($sceneLines.Count -eq 0) { throw "Selected scene list is empty." }
$statusRoot = Join-Path $env:WORKSPACE "isolated-summaries"
if (Test-Path -LiteralPath $statusRoot) { Remove-Item -LiteralPath $statusRoot -Recurse -Force }
New-Item -ItemType Directory -Path $statusRoot -Force | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
for ($i = 0; $i -lt $sceneLines.Count; $i++) {
  $sceneNumber = $i + 1
  $oneSceneList = Join-Path $env:WORKSPACE ("selected-scene-{0:D4}.txt" -f $sceneNumber)
  [System.IO.File]::WriteAllLines($oneSceneList, [string[]]@($sceneLines[$i]), $utf8NoBom)
}
[System.IO.File]::WriteAllText((Join-Path $env:WORKSPACE "isolated-scene-count.txt"), [string]$sceneLines.Count, $utf8NoBom)
Write-Host ("Prepared {0} isolated compare scene invocations." -f $sceneLines.Count)
'''
                  powershell './prepare-isolated-compare.ps1'

                  writeFile file: 'kill-pathtracer.ps1', text: '''
$ErrorActionPreference = "Continue"
foreach ($infoFile in @("package-release.json", "package-o1experimental.json")) {
  if (-not (Test-Path -LiteralPath $infoFile)) { continue }
  $package = Get-Content -LiteralPath $infoFile | ConvertFrom-Json
  $name = [System.IO.Path]::GetFileNameWithoutExtension($package.exe)
  Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
'''

                  writeFile file: 'run-isolated-compare-scene.ps1', text: '''
param([Parameter(Mandatory = $true)][int] $SceneNumber)
$ErrorActionPreference = "Stop"
$sceneTag = "{0:D4}" -f $SceneNumber
$sceneList = Join-Path $env:WORKSPACE ("selected-scene-{0}.txt" -f $sceneTag)
$statusRoot = Join-Path $env:WORKSPACE "isolated-summaries"
$resolved = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "resolved-scenes.json") | ConvertFrom-Json
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-VariantStatus {
  param([Parameter(Mandatory = $true)][string] $Variant, [Parameter(Mandatory = $true)][bool] $Ok, [Parameter(Mandatory = $true)][string] $Message, [Parameter(Mandatory = $true)][string] $OutputRelative)
  $variantDir = Join-Path $statusRoot $Variant
  New-Item -ItemType Directory -Path $variantDir -Force | Out-Null
  $summaryPath = Join-Path (Join-Path $env:WORKSPACE $OutputRelative) "summary.json"
  if (Test-Path -LiteralPath $summaryPath) {
    Copy-Item -LiteralPath $summaryPath -Destination (Join-Path $variantDir ("summary-{0}.json" -f $sceneTag)) -Force
  }
  $status = [pscustomobject]@{ scene = $SceneNumber; variant = $Variant; ok = $Ok; message = $Message; output = $OutputRelative; summary = (Test-Path -LiteralPath $summaryPath) }
  [System.IO.File]::WriteAllText((Join-Path $variantDir ("status-{0}.json" -f $sceneTag)), ($status | ConvertTo-Json -Depth 4), $utf8NoBom)
}
function Invoke-Variant {
  param(
    [Parameter(Mandatory = $true)][string] $Variant,
    [Parameter(Mandatory = $true)][string] $PackageInfoPath,
    [Parameter(Mandatory = $true)][string] $ReferenceDir,
    [Parameter(Mandatory = $true)][string] $OutputRelative
  )
  $ok = $true
  $message = "ok"
  try {
    & ./run-compare-variant.ps1 -PackageInfoPath $PackageInfoPath -VariantName ($Variant + "-scene-" + $sceneTag) -ReferenceDir $ReferenceDir -OutputRelative $OutputRelative -SceneListPath $sceneList
    if ($LASTEXITCODE -ne 0) {
      $ok = $false
      $message = "exit " + $LASTEXITCODE
    }
  } catch {
    $ok = $false
    $message = $_.Exception.Message
    Write-Warning ("{0} scene {1} failed: {2}" -f $Variant, $sceneTag, $message)
  }
  Write-VariantStatus -Variant $Variant -Ok $ok -Message $message -OutputRelative $OutputRelative
}
$releaseOutput = "scratch/release-o3-scenes/scene-" + $sceneTag
$o1Output = "scratch/o1experimental-scenes/scene-" + $sceneTag
Invoke-Variant -Variant "release-o3" -PackageInfoPath "package-release.json" -ReferenceDir $resolved.referenceDir -OutputRelative $releaseOutput
Invoke-Variant -Variant "o1experimental" -PackageInfoPath "package-o1experimental.json" -ReferenceDir $resolved.referenceDir -OutputRelative $o1Output
'''

                  writeFile file: 'merge-isolated-compare.ps1', text: '''
$ErrorActionPreference = "Stop"
$statusRoot = Join-Path $env:WORKSPACE "isolated-summaries"
$sceneLines = @(Get-Content -LiteralPath (Join-Path $env:WORKSPACE "selected-scenes.txt") | Where-Object { $_.Trim().Length -gt 0 })
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Get-SceneDisplayName {
  param([Parameter(Mandatory = $true)][string] $Line, [Parameter(Mandatory = $true)][int] $SceneNumber)
  $scenePath = ""
  if ($Line -match "^\\s*`"([^`"]+)`"") { $scenePath = $Matches[1] } else { $scenePath = ($Line -split "\\s+", 2)[0].Trim([char]34) }
  if ($scenePath) { return [System.IO.Path]::GetFileNameWithoutExtension($scenePath) }
  return "scene_" + ("{0:D2}" -f $SceneNumber)
}
function New-SyntheticResult {
  param([Parameter(Mandatory = $true)][int] $SceneNumber, [Parameter(Mandatory = $true)][string] $Line, [Parameter(Mandatory = $true)][string] $Reason)
  $display = Get-SceneDisplayName -Line $Line -SceneNumber $SceneNumber
  $runtimeImage = [pscustomobject]@{ identifier = "runtime"; title = "Runtime"; status = "error"; status_color = "red"; details = $Reason; filename = $display }
  return [pscustomobject]@{ array = @($runtimeImage); compare = $null; details = ("Command: {0}" -f $Line); display_name = $display; index = $SceneNumber; scene_name = ("{0:D2}_{1}" -f $SceneNumber, $display); scene_path = $Line; sensor = 0; status = "failed"; status_color = "red" }
}
function Merge-VariantSummary {
  param(
    [Parameter(Mandatory = $true)][string] $Variant,
    [Parameter(Mandatory = $true)][string] $DestinationRelative,
    [string] $TemplatePackageInfoPath = "",
    [string] $ReportSourceRoot = ""
  )
  $destination = Join-Path $env:WORKSPACE $DestinationRelative
  if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
  New-Item -ItemType Directory -Path $destination -Force | Out-Null
  if ($TemplatePackageInfoPath) {
    $package = Get-Content -LiteralPath $TemplatePackageInfoPath | ConvertFrom-Json
    Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $destination -Recurse -Force
    New-Item -ItemType Directory -Path (Join-Path $destination "renders") -Force | Out-Null
  }
  foreach ($artifactRoot in @("renders", "references", "diff_images")) {
    New-Item -ItemType Directory -Path (Join-Path $destination $artifactRoot) -Force | Out-Null
  }
  $results = New-Object System.Collections.ArrayList
  $failureCount = 0
  $testCount = 0
  $buildConfig = ""
  $firstSummary = $null
  for ($i = 0; $i -lt $sceneLines.Count; $i++) {
    $sceneNumber = $i + 1
    $sceneTag = "{0:D4}" -f $sceneNumber
    $summaryPath = Join-Path (Join-Path $statusRoot $Variant) ("summary-{0}.json" -f $sceneTag)
    if (Test-Path -LiteralPath $summaryPath) {
      $summary = Get-Content -LiteralPath $summaryPath | ConvertFrom-Json
      if (-not $firstSummary) { $firstSummary = $summary }
      if (-not $buildConfig -and $summary.buildConfig) { $buildConfig = $summary.buildConfig }
      foreach ($item in @($summary.results)) {
        if ($item.PSObject.Properties.Name -contains "index") { $item.index = $sceneNumber }
        [void]$results.Add($item)
      }
      $failureCount += [int]$summary.failure_count
      $testCount += [int]$summary.num_of_tests
    } else {
      $statusPath = Join-Path (Join-Path $statusRoot $Variant) ("status-{0}.json" -f $sceneTag)
      $reason = if (Test-Path -LiteralPath $statusPath) { (Get-Content -LiteralPath $statusPath | ConvertFrom-Json).message } else { "variant did not write summary.json" }
      [void]$results.Add((New-SyntheticResult -SceneNumber $sceneNumber -Line $sceneLines[$i] -Reason $reason))
      $failureCount++
      $testCount++
    }
    if ($ReportSourceRoot) {
      $sourceReport = Join-Path $env:WORKSPACE ($ReportSourceRoot + "/scene-" + $sceneTag)
      foreach ($artifactRoot in @("renders", "references", "diff_images")) {
        $sourceArtifacts = Join-Path $sourceReport $artifactRoot
        if (Test-Path -LiteralPath $sourceArtifacts) {
          Copy-Item -Path (Join-Path $sourceArtifacts "*") -Destination (Join-Path $destination $artifactRoot) -Recurse -Force -ErrorAction SilentlyContinue
        }
      }
    }
  }
  $passStatus = if ($failureCount -gt 0) { "failed" } else { "passed" }
  $merged = [ordered]@{
    identifier = if ($firstSummary -and $firstSummary.identifier) { $firstSummary.identifier } else { "40_PathTracer" }
    machine = if ($firstSummary -and $firstSummary.machine) { $firstSummary.machine } else { @{} }
    build = if ($firstSummary -and $firstSummary.build) { $firstSummary.build } else { @{} }
    buildConfig = $buildConfig
    datetime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")
    compare = if ($firstSummary -and $firstSummary.compare) { $firstSummary.compare } else { @{} }
    postprocess = if ($firstSummary -and $firstSummary.postprocess) { $firstSummary.postprocess } else { @{} }
    lowDiscrepancySequenceCache = if ($firstSummary -and $firstSummary.lowDiscrepancySequenceCache) { $firstSummary.lowDiscrepancySequenceCache } else { @{ status = "not-configured" } }
    referenceDir = if ($firstSummary -and $firstSummary.referenceDir) { $firstSummary.referenceDir } else { "references" }
    failure_count = $failureCount
    num_of_tests = $testCount
    pass_status = $passStatus
    results = @($results)
  }
  [System.IO.File]::WriteAllText((Join-Path $destination "summary.json"), ($merged | ConvertTo-Json -Depth 100), $utf8NoBom)
  Write-Host ("Merged {0}: tests={1}, failures={2}." -f $Variant, $testCount, $failureCount)
}
Merge-VariantSummary -Variant "release-o3" -DestinationRelative "publish/release-o3" -TemplatePackageInfoPath "package-release.json" -ReportSourceRoot "scratch/release-o3-scenes"
Merge-VariantSummary -Variant "o1experimental" -DestinationRelative "publish/o1experimental" -TemplatePackageInfoPath "package-o1experimental.json" -ReportSourceRoot "scratch/o1experimental-scenes"
'''
                }

                def selectedScenes = readFile('selected-scenes.txt').readLines().findAll { it.trim() }
                for (int sceneOffset = 0; sceneOffset < selectedScenes.size(); sceneOffset++) {
                  def sceneNumber = sceneOffset + 1
                  stage('Scene ' + sceneNumber) {
                    try {
                      timeout(time: sceneTimeoutSeconds, unit: 'SECONDS') {
                        powershell script: './run-isolated-compare-scene.ps1 -SceneNumber ' + sceneNumber
                      }
                    } catch (err) {
                      timeout(time: 1, unit: 'MINUTES') {
                        powershell script: './kill-pathtracer.ps1'
                      }
                      echo('Scene ' + sceneNumber + ' timed out or failed unexpectedly; merge will mark missing variant summaries as failures.')
                    }
                  }
                }

                stage('Merge isolated comparison') {
                  powershell './merge-isolated-compare.ps1'
                }

                stage('Compare O1experimental vs O3') {
                  powershell '''
./run-report-comparison.ps1 -BaselineReportRelative publish/release-o3 -CandidateReportRelative publish/o1experimental -OutputRelative publish/o1experimental-vs-o3
'''
                }
              } else {
                stage('Run Release O3 vs reference') {
                  powershell '''
$resolved = Get-Content -LiteralPath resolved-scenes.json | ConvertFrom-Json
./run-compare-variant.ps1 -PackageInfoPath package-release.json -VariantName release-o3-vs-reference -ReferenceDir $resolved.referenceDir -OutputRelative publish/release-o3
'''
                }

                stage('Run O1experimental vs reference') {
                  powershell '''
$resolved = Get-Content -LiteralPath resolved-scenes.json | ConvertFrom-Json
./run-compare-variant.ps1 -PackageInfoPath package-o1experimental.json -VariantName o1experimental-vs-reference -ReferenceDir $resolved.referenceDir -OutputRelative publish/o1experimental
'''
                }

                stage('Compare O1experimental vs O3') {
                  powershell '''
./run-report-comparison.ps1 -BaselineReportRelative publish/release-o3 -CandidateReportRelative publish/o1experimental -OutputRelative publish/o1experimental-vs-o3
'''
                }
              }

              stage('Build comparison index') {
                writeFile file: 'build-compare-index.ps1', text: '''
$ErrorActionPreference = "Stop"
$publishRoot = Join-Path $env:WORKSPACE "publish"
$release = Get-Content -LiteralPath (Join-Path $publishRoot "release-o3/summary.json") | ConvertFrom-Json
$o1 = Get-Content -LiteralPath (Join-Path $publishRoot "o1experimental/summary.json") | ConvertFrom-Json
$compare = Get-Content -LiteralPath (Join-Path $publishRoot "o1experimental-vs-o3/summary.json") | ConvertFrom-Json
function Entry($Name, $Path, $Summary) {
  $warnings = if ($Summary.PSObject.Properties.Name -contains "warning_count") { [int]$Summary.warning_count } else { 0 }
  $referenceMismatches = if ($Summary.PSObject.Properties.Name -contains "reference_mismatch_count") { [int]$Summary.reference_mismatch_count } else { 0 }
  [pscustomobject]@{ name = $Name; path = $Path; status = $Summary.pass_status; tests = [int]$Summary.num_of_tests; failures = [int]$Summary.failure_count; warnings = $warnings; referenceMismatches = $referenceMismatches; buildConfig = $Summary.buildConfig }
}
$entries = @(
  (Entry "Release O3 vs reference" "release-o3/" $release),
  (Entry "O1experimental vs reference" "o1experimental/" $o1),
  (Entry "O1experimental vs O3" "o1experimental-vs-o3/" $compare)
)
$compareWarnings = if ($compare.PSObject.Properties.Name -contains "warning_count") { [int]$compare.warning_count } else { 0 }
$verdict = if ([int]$compare.failure_count -gt 0) { "different" } elseif ($compareWarnings -gt 0) { "same-with-warnings" } else { "same-within-threshold" }
$summary = [pscustomobject]@{ schema = "devsh.ditt.pathtracer-compare-index.v1"; title = "O1experimental vs O3"; suite = $env:SCENE_SUITE; verdict = $verdict; entries = $entries }
$rows = ($entries | ForEach-Object {
  "<tr><td><a href=""$($_.path)"">$($_.name)</a></td><td>$($_.status)</td><td>$($_.tests)</td><td>$($_.failures)</td><td>$($_.warnings)</td><td>$($_.referenceMismatches)</td><td>$($_.buildConfig)</td></tr>"
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
      <thead><tr><th>Report</th><th>Status</th><th>Tests</th><th>Failures</th><th>Warnings</th><th>Reference mismatches</th><th>Build config</th></tr></thead>
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
$publisher = Join-Path $publishRoot "o1experimental-vs-o3/publishS3.py"
if (-not (Test-Path -LiteralPath $publisher)) { $publisher = Join-Path $publishRoot "o1experimental/publishS3.py" }
if (-not (Test-Path -LiteralPath $publisher)) { $publisher = Join-Path $publishRoot "release-o3/publishS3.py" }
if (-not (Test-Path -LiteralPath $publisher)) { throw "publishS3.py was not found in any report bundle." }
Copy-Item -LiteralPath $publisher -Destination (Join-Path $publishRoot "publishS3.py") -Force
Write-Host ("Comparison verdict: {0}; O1experimental vs O3 failures={1}/{2}; warnings={3}." -f $verdict, $compare.failure_count, $compare.num_of_tests, $compareWarnings)
'''
                powershell './build-compare-index.ps1'
              }

              stage('Validate comparison') {
                def summary = readJSON(file: 'publish/summary.json', returnPojo: true)
                def compareEntry = summary.entries.find { it.name == 'O1experimental vs O3' }
                compareFailureCount = (compareEntry.failures ?: 0) as int
                compareTestCount = (compareEntry.tests ?: 0) as int
                compareWarningCount = (compareEntry.warnings ?: 0) as int
                updateBuildDescription()
                if (compareFailureCount > 0) {
                  unstable("O1experimental vs O3 contains ${compareFailureCount} failed comparison(s).")
                } else if (compareWarningCount > 0) {
                  unstable("O1experimental vs O3 contains ${compareWarningCount} comparison warning(s).")
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
                archiveArtifacts artifacts: 'package-release.json,package-o1experimental.json,scene-cache-info.json,scene-git-request.json,git-object-cache.json,resolved-scenes.json,selected-scenes.txt,isolated-summaries/**/*.json,ex40-*.log,publish.zip,publish/index.html,publish/summary.json,publish/publishS3.py,publish/release-o3-summary.json,publish/o1experimental-summary.json,publish/release-o3/summary.json,publish/o1experimental/summary.json,publish/o1experimental-vs-o3/summary.json', allowEmptyArchive: true, fingerprint: false
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
