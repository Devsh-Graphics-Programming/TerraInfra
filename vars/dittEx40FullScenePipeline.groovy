def call(Map args = [:]) {
  def fixedSuite = args.suite?.toString()?.trim()
  if (!(fixedSuite in ['public', 'private'])) {
    error('dittEx40FullScenePipeline requires suite public or private.')
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
    suite == 'public' ? 'ditt/public/latest/' : 'ditt/private/latest/'
  }

  def packageUrl = params.EX40_PACKAGE_URL?.trim()
  def suite = fixedSuite
  def mediaCommit = null
  def publicReferencesCommit = null
  def dittScenesCommit = null
  def privateReferencesCommit = null
  def storePrefix = null
  def shardCount = null
  def shardIndex = null
  def failOnRenderFailure = false
  def isolateScenes = false
  def publish = true
  def storePublishArtifact = null
  def sourceRepository = null
  def sourceBranch = null
  def sourceSha = null
  def sourceRunId = null
  def sourceRunAttempt = null
  def sourceWorkflow = null
  def sourceUrl = null
  def runnerTimeoutMinutes = null
  def runnerSummary = [:]
  def packageSummary = [:]
  def scratchId = null
  def scratchVariant = null
  def scratchInfo = null
  def selectedSceneCount = null
  def totalSceneCount = null
  def reportFailureCount = null
  def reportTestCount = null
  def reportUrl = null
  def runCompareSmoke = false
  def compareSmokeStorePrefix = null
  def compareSmokeUrl = null
  def compareSmokeFailureCount = null
  def compareSmokeTestCount = null
  def buildStartedAt = System.currentTimeMillis()

  def updateBuildDescription = {
    def lines = []
    if (reportUrl) {
      lines << reportUrl
    }
    if (compareSmokeUrl) {
      lines << ('smoke=' + compareSmokeUrl)
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
    if (reportFailureCount != null && reportTestCount != null) {
      lines << ('report_failures=' + reportFailureCount + '/' + reportTestCount)
    }
    if (compareSmokeFailureCount != null && compareSmokeTestCount != null) {
      lines << ('smoke_failures=' + compareSmokeFailureCount + '/' + compareSmokeTestCount)
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
    if (packageSummary.manifestUsed != null) {
      lines << ('package_manifest=' + packageSummary.manifestUsed)
    }
    if (scratchId) {
      lines << ('scratch=' + scratchId + '/' + scratchVariant)
    }
    lines << ('elapsed=' + runnerFormatDuration(System.currentTimeMillis() - buildStartedAt))
    currentBuild.description = lines.findAll { it != null && it.toString().trim() }.join('<br/>')
  }

  def writePublishRootHelper = {
    writeFile file: 'publish-root.ps1', text: '''
function Initialize-PublishRoot {
  param([switch] $Clean)
  if (-not [string]::IsNullOrWhiteSpace($env:SCRATCH_UNC_PATH)) {
    if ([string]::IsNullOrWhiteSpace($env:SCRATCH_ID)) { throw "SCRATCH_ID is required when SCRATCH_UNC_PATH is set." }
    if ([string]::IsNullOrWhiteSpace($env:SCRATCH_VARIANT)) { throw "SCRATCH_VARIANT is required when SCRATCH_UNC_PATH is set." }
    if ([string]::IsNullOrWhiteSpace($env:SCRATCH_SMB_USERNAME) -or [string]::IsNullOrWhiteSpace($env:SCRATCH_SMB_CREDENTIAL)) { throw "Scratch SMB credentials are required." }
    $scratchMapTarget = if ([string]::IsNullOrWhiteSpace($env:SCRATCH_UNC_ROOT)) { $env:SCRATCH_UNC_PATH } else { $env:SCRATCH_UNC_ROOT }
    & cmd.exe /d /c "net use R: /delete /y >nul 2>nul"
    & net.exe use R: $scratchMapTarget $env:SCRATCH_SMB_CREDENTIAL /user:$env:SCRATCH_SMB_USERNAME /persistent:no | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Could not map runner scratch share." }
    $root = Join-Path (Join-Path "R:\\" $env:SCRATCH_ID) $env:SCRATCH_VARIANT
  } else {
    $root = Join-Path $env:WORKSPACE "publish"
  }
  if ($Clean -and (Test-Path -LiteralPath $root)) { Remove-Item -LiteralPath $root -Recurse -Force }
  New-Item -ItemType Directory -Path $root -Force | Out-Null
  return [System.IO.Path]::GetFullPath($root)
}

function Sync-PublishSummaryToWorkspace {
  param([Parameter(Mandatory = $true)][string] $PublishRoot)
  $workspacePublish = [System.IO.Path]::GetFullPath((Join-Path $env:WORKSPACE "publish"))
  $publishFull = [System.IO.Path]::GetFullPath($PublishRoot)
  if ($publishFull.Equals($workspacePublish, [System.StringComparison]::OrdinalIgnoreCase)) { return }
  if (Test-Path -LiteralPath $workspacePublish) { Remove-Item -LiteralPath $workspacePublish -Recurse -Force }
  New-Item -ItemType Directory -Path $workspacePublish -Force | Out-Null
  foreach ($relative in @("index.html", "summary.json")) {
    $source = Join-Path $publishFull $relative
    if (Test-Path -LiteralPath $source) {
      Copy-Item -LiteralPath $source -Destination (Join-Path $workspacePublish $relative) -Force
    }
  }
}
'''
  }

  timestamps {
    stage('Validate request') {
      runnerTimeoutMinutes = requireNumber(args.get('runnerTimeoutMinutes', '330'), 'runnerTimeoutMinutes', 10, 720)
      shardCount = requireNumber(params.SHARD_COUNT ?: args.get('shardCountDefault', '1'), 'SHARD_COUNT', 1, 64)
      shardIndex = requireNumber(params.SHARD_INDEX ?: args.get('shardIndexDefault', '0'), 'SHARD_INDEX', 0, 63)
      if (shardIndex >= shardCount) {
        error('SHARD_INDEX must be smaller than SHARD_COUNT.')
      }
      if (packageUrl && !packageUrl.startsWith('https://')) {
        error('EX40_PACKAGE_URL must be HTTPS.')
      }
      if (suite == 'public') {
        mediaCommit = requireSha(params.MEDIA_COMMIT ?: args.mediaCommitDefault, 'MEDIA_COMMIT')
        publicReferencesCommit = requireSha(params.PUBLIC_REFERENCES_COMMIT ?: args.publicReferencesCommitDefault, 'PUBLIC_REFERENCES_COMMIT')
      } else {
        dittScenesCommit = requireSha(params.DITT_SCENES_COMMIT ?: args.dittScenesCommitDefault, 'DITT_SCENES_COMMIT')
        privateReferencesCommit = requireSha(params.PRIVATE_REFERENCES_COMMIT ?: args.privateReferencesCommitDefault, 'PRIVATE_REFERENCES_COMMIT')
      }
      def rawPrefix = params.STORE_PREFIX?.trim() ?: args.defaultStorePrefix ?: defaultPrefixForSuite(suite)
      def allowedPrefixes = suite == 'public' ? ['ditt/public/'] : ['ditt/private/']
      storePrefix = storeNormalizePrefix(rawPrefix, allowedPrefixes)
      failOnRenderFailure = params.FAIL_ON_RENDER_FAILURE == null ? (args.get('failOnRenderFailureDefault', false) as boolean) : (params.FAIL_ON_RENDER_FAILURE as boolean)
      isolateScenes = params.ISOLATE_SCENES == null ? (args.get('isolateScenesDefault', false) as boolean) : (params.ISOLATE_SCENES as boolean)
      publish = params.PUBLISH == null ? (args.get('publishDefault', true) as boolean) : (params.PUBLISH as boolean)
      runCompareSmoke = suite == 'public' && (params.RUN_COMPARE_SMOKE == null ? (args.get('compareSmokeDefault', false) as boolean) : (params.RUN_COMPARE_SMOKE as boolean))
      if (runCompareSmoke) {
        def rawSmokePrefix = params.COMPARE_SMOKE_STORE_PREFIX?.trim() ?: args.get('compareSmokeStorePrefix', 'ditt/public/smoke/latest/')
        compareSmokeStorePrefix = storeNormalizePrefix(rawSmokePrefix, ['ditt/public/'])
      }
      scratchId = params.SCRATCH_ID?.trim()
      scratchVariant = params.SCRATCH_VARIANT?.trim()
      if ((scratchId && !scratchVariant) || (!scratchId && scratchVariant)) {
        error('SCRATCH_ID and SCRATCH_VARIANT must be provided together.')
      }
      if (scratchId && !(scratchId ==~ /[A-Za-z0-9][A-Za-z0-9._-]{0,95}/)) {
        error('SCRATCH_ID contains unsupported characters.')
      }
      if (scratchVariant && !(scratchVariant ==~ /[A-Za-z0-9][A-Za-z0-9._-]{0,63}/)) {
        error('SCRATCH_VARIANT contains unsupported characters.')
      }
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
        currentBuild.displayName = '#' + env.BUILD_NUMBER + ' ' + suite + ' ' + (sourceSha ?: sourceRunId).take(12)
        currentBuild.description = sourceUrl
        echo('Source Actions run: ' + sourceUrl)
      }
      echo('Suite: ' + suite + ', shard=' + shardIndex + '/' + shardCount + ', store_prefix=' + storePrefix + ', isolate_scenes=' + isolateScenes + ', publish=' + publish + ', compare_smoke=' + runCompareSmoke)
      updateBuildDescription()
    }

    timeout(time: runnerTimeoutMinutes, unit: 'MINUTES') {
      withRunner(
        labels: args.get('labels', ['windows', 'gpu', 'nvidia', 'vulkan', 'runtime-only']),
        leaseTtlMinutes: args.get('leaseTtlMinutes', 360),
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
        if (scratchId) {
          stage('Prepare scratch') {
            scratchInfo = runnerScratch(runner: runner, id: scratchId, action: 'create')
            updateBuildDescription()
          }
        }
        withFileParameter(name: args.get('packageFileParameter', 'EX40_PACKAGE_FILE'), allowNoFile: true) {
          withEnv([
            'EX40_PACKAGE_URL=' + (packageUrl ?: ''),
            'SCENE_SUITE=' + suite,
            'SHARD_COUNT=' + shardCount.toString(),
            'SHARD_INDEX=' + shardIndex.toString(),
            'FAIL_ON_RENDER_FAILURE=' + failOnRenderFailure.toString(),
            'ISOLATE_SCENES=' + isolateScenes.toString(),
            'SCRATCH_ID=' + (scratchId ?: ''),
            'SCRATCH_UNC_ROOT=' + (scratchInfo?.unc_root ?: ''),
            'SCRATCH_UNC_PATH=' + (scratchInfo?.unc_path ?: ''),
            'SCRATCH_VARIANT=' + (scratchVariant ?: ''),
            'SCRATCH_SMB_USERNAME=' + (scratchInfo?.smb_username ?: ''),
            'SCRATCH_SMB_CREDENTIAL=' + (scratchInfo?.smb_credential ?: '')
          ]) {
            writePublishRootHelper()
            stage('Acquire package') {
              writeFile file: 'acquire-package.ps1', text: [
              '$ErrorActionPreference = "Stop"',
              '$ProgressPreference = "SilentlyContinue"',
              '$packagePath = Join-Path $env:WORKSPACE "ex40-package.zip"',
              '$extractRoot = Join-Path $env:WORKSPACE "package"',
              'Remove-Item -LiteralPath $packagePath -Force -ErrorAction SilentlyContinue',
              'if (Test-Path -LiteralPath $extractRoot) { Remove-Item -LiteralPath $extractRoot -Recurse -Force }',
              'if ($env:EX40_PACKAGE_FILE -and (Test-Path -LiteralPath $env:EX40_PACKAGE_FILE)) {',
              '  Copy-Item -LiteralPath $env:EX40_PACKAGE_FILE -Destination $packagePath -Force',
              '  Write-Host ("Using uploaded EX40 package file: {0}" -f $env:EX40_PACKAGE_FILE_FILENAME)',
              '} elseif ($env:EX40_PACKAGE_URL) {',
              '  if (-not ($env:EX40_PACKAGE_URL -match "^https://")) { throw "EX40_PACKAGE_URL must be HTTPS." }',
              '  Write-Host "Downloading EX40 package from HTTPS URL."',
              '  Invoke-WebRequest -Uri $env:EX40_PACKAGE_URL -OutFile $packagePath -UseBasicParsing',
              '} else {',
              '  throw "Provide EX40_PACKAGE_FILE or EX40_PACKAGE_URL."',
              '}',
              '$packageSize = (Get-Item -LiteralPath $packagePath).Length',
              'Write-Host ("EX40 package size: {0} bytes" -f $packageSize)',
              'Expand-Archive -LiteralPath $packagePath -DestinationPath $extractRoot -Force',
              'function Resolve-PackagePath {',
              '  param([Parameter(Mandatory = $true)][string] $Base, [Parameter(Mandatory = $true)][string] $Relative, [Parameter(Mandatory = $true)][string] $Name)',
              '  if ([System.IO.Path]::IsPathRooted($Relative)) { throw "Unsafe package manifest path: $Name" }',
              '  $segments = $Relative.Replace([char]92, [char]47).Split([char]47)',
              '  if ($segments -contains "..") { throw "Unsafe package manifest path: $Name" }',
              '  $baseFull = [System.IO.Path]::GetFullPath($Base)',
              '  $candidate = [System.IO.Path]::GetFullPath((Join-Path $baseFull $Relative))',
              '  $prefix = $baseFull.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar',
              '  if ($candidate -ne $baseFull -and -not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Package manifest path escapes package root: $Name" }',
              '  return $candidate',
              '}',
              '$manifestFile = Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "EX40Runtime.json" | Sort-Object FullName | Select-Object -First 1',
              '$manifestUsed = $false',
              '$manifestPath = ""',
              'if ($manifestFile) {',
              '  $manifest = Get-Content -LiteralPath $manifestFile.FullName -Raw | ConvertFrom-Json',
              '  if ($manifest.schema -ne "devsh.nabla.example-runtime.v1" -or $manifest.component -ne "EX40Runtime") { throw "Unsupported EX40 runtime manifest." }',
              '  $manifestBase = $manifestFile.DirectoryName',
              '  $exe = Get-Item -LiteralPath (Resolve-PackagePath -Base $manifestBase -Relative $manifest.executable -Name "executable")',
              '  $runtimeDir = Get-Item -LiteralPath (Resolve-PackagePath -Base $manifestBase -Relative $manifest.nabla_runtime -Name "nabla_runtime")',
              '  $dxcDir = Get-Item -LiteralPath (Resolve-PackagePath -Base $manifestBase -Relative $manifest.dxc_runtime -Name "dxc_runtime")',
              '  $reportTemplate = Resolve-PackagePath -Base $manifestBase -Relative $manifest.report_template -Name "report_template"',
              '  $manifestUsed = $true',
              '  $manifestPath = $manifestFile.FullName',
              '} else {',
              '  $exe = Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "40_pathtracer*.exe" | Select-Object -First 1',
              '  if (-not $exe) { throw "40_pathtracer executable was not found in the package." }',
              '  $runtimeDll = Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "Nabla*.dll" | Select-Object -First 1',
              '  if (-not $runtimeDll) { throw "Nabla runtime DLL was not found in the package." }',
              '  $dxcDll = Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "dxcompiler.dll" | Select-Object -First 1',
              '  if (-not $dxcDll) { throw "DXC runtime DLL was not found in the package." }',
              '  $runtimeDir = $runtimeDll.Directory',
              '  $dxcDir = $dxcDll.Directory',
              '  $reportTemplate = Join-Path $exe.DirectoryName "report"',
              '}',
              'if (-not $exe) { throw "40_pathtracer executable was not found in the package." }',
              '$runtimeDll = Get-ChildItem -LiteralPath $runtimeDir.FullName -File -Filter "Nabla*.dll" | Select-Object -First 1',
              'if (-not $runtimeDll) { throw "Nabla runtime DLL was not found in the package." }',
              '$dxcDll = Get-ChildItem -LiteralPath $dxcDir.FullName -File -Filter "dxcompiler.dll" | Select-Object -First 1',
              'if (-not $dxcDll) { throw "DXC runtime DLL was not found in the package." }',
              'if (-not (Test-Path -LiteralPath $reportTemplate)) { throw "Report template directory was not found next to the executable." }',
              '$info = [pscustomobject]@{ exe = $exe.FullName; bin = $exe.DirectoryName; runtime = $runtimeDir.FullName; dxc = $dxcDir.FullName; reportTemplate = $reportTemplate; packageSize = $packageSize; manifestUsed = $manifestUsed; manifest = $manifestPath }',
              '$utf8NoBom = New-Object System.Text.UTF8Encoding($false)',
              '[System.IO.File]::WriteAllText((Join-Path $env:WORKSPACE "package-info.json"), ($info | ConvertTo-Json -Depth 4), $utf8NoBom)',
              'Write-Host ("EX40 executable: {0}" -f $exe.FullName)'
            ].join('\n')
            powershell './acquire-package.ps1'
            packageSummary = readJSON(file: 'package-info.json', returnPojo: true)
            updateBuildDescription()
          }

          stage('Materialize scenes') {
            dittMaterializeSceneData(
              suite: suite,
              gitObjectCache: runner.git_object_cache,
              mediaCommit: mediaCommit,
              publicReferencesCommit: publicReferencesCommit,
              dittScenesCommit: dittScenesCommit,
              privateReferencesCommit: privateReferencesCommit
            )
          }

          stage('Prepare LDS cache') {
            dittEx40LdsCache(cacheApiUrl: runner.git_object_cache?.api_url)
            powershell './ex40-lds-cache.ps1 -Mode Status -PackageInfoPath package-info.json'
          }

          if (runCompareSmoke) {
            stage('Prepare compare smoke') {
              writeFile file: 'run-compare-smoke-render.ps1', text: [
                'param(',
                '  [Parameter(Mandatory = $true)][string] $Id,',
                '  [Parameter(Mandatory = $true)][string] $Name',
                ')',
                '$ErrorActionPreference = "Stop"',
                'if (-not ($Id -match "^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")) { throw "Invalid smoke input id: $Id" }',
                '$package = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-info.json") | ConvertFrom-Json',
                '$sceneInfo = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "scene-cache-info.json") | ConvertFrom-Json',
                '$scene = Join-Path $sceneInfo.root "mitsuba\\shapetest.xml"',
                'if (-not (Test-Path -LiteralPath $scene)) { throw "Compare smoke scene was not materialized: $scene" }',
                '$root = Join-Path $env:WORKSPACE "compare-smoke"',
                '$reportRoot = Join-Path $root $Id',
                '$renders = Join-Path $reportRoot "renders"',
                '$sharedTmp = Join-Path $package.bin "../../tmp"',
                'if (Test-Path -LiteralPath $reportRoot) { Remove-Item -LiteralPath $reportRoot -Recurse -Force }',
                'New-Item -ItemType Directory -Path $renders -Force | Out-Null',
                'New-Item -ItemType Directory -Path $sharedTmp -Force | Out-Null',
                'Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $reportRoot -Recurse -Force',
                '$env:PATH = $package.runtime + ";" + $package.dxc + ";" + $env:PATH',
                '$log = Join-Path $env:WORKSPACE ("ex40-compare-smoke-" + $Id + ".log")',
                'Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue',
                '& (Join-Path $env:WORKSPACE "ex40-lds-cache.ps1") -Mode Restore -PackageInfoPath (Join-Path $env:WORKSPACE "package-info.json")',
                '$runArgs = @("--scene", $scene, "--process-sensors", "RenderAllThenTerminate", "--headless", "--output-dir", $renders, "--report-dir", $reportRoot)',
                'Write-Host ("Running compare smoke input {0}: {1}" -f $Name, ($runArgs -join " "))',
                '$exitCode = 0',
                'Push-Location -LiteralPath $package.bin',
                'try {',
                '  & $package.exe @runArgs 2>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { Write-Host $_ }',
                '  $exitCode = $LASTEXITCODE',
                '} finally {',
                '  Pop-Location',
                '}',
                '& (Join-Path $env:WORKSPACE "ex40-lds-cache.ps1") -Mode Save -PackageInfoPath (Join-Path $env:WORKSPACE "package-info.json")',
                '$summaryPath = Join-Path $reportRoot "summary.json"',
                'if ($exitCode -ne 0) { throw "Compare smoke input $Name failed with exit code $exitCode." }',
                'if (-not (Test-Path -LiteralPath $summaryPath)) { throw "Compare smoke input $Name did not write summary.json." }',
                '$summary = Get-Content -LiteralPath $summaryPath | ConvertFrom-Json',
                'if ([int]$summary.num_of_tests -lt 1) { throw "Compare smoke input $Name contains no tests." }',
                'Write-Host ("Compare smoke input {0}: status={1}, tests={2}, failures={3}." -f $Name, $summary.pass_status, $summary.num_of_tests, $summary.failure_count)'
              ].join('\n')

              writeFile file: 'run-compare-smoke-set.ps1', text: [
                '$ErrorActionPreference = "Stop"',
                '$package = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-info.json") | ConvertFrom-Json',
                '$root = Join-Path $env:WORKSPACE "compare-smoke"',
                '$publishRoot = Join-Path $env:WORKSPACE "compare-smoke-publish"',
                'if (Test-Path -LiteralPath $publishRoot) { Remove-Item -LiteralPath $publishRoot -Recurse -Force }',
                'New-Item -ItemType Directory -Path $publishRoot -Force | Out-Null',
                'Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $publishRoot -Recurse -Force',
                '$manifestPath = Join-Path $root "manifest.json"',
                '$manifest = [ordered]@{',
                '  name = "EX40 release compare smoke"',
                '  baseline = "nvidia"',
                '  inputs = @(',
                '    [ordered]@{ id = "nvidia"; name = "NVIDIA"; reportDir = "nvidia" },',
                '    [ordered]@{ id = "amd"; name = "AMD"; reportDir = "amd" },',
                '    [ordered]@{ id = "intel"; name = "Intel"; reportDir = "intel" }',
                '  )',
                '}',
                '$utf8NoBom = New-Object System.Text.UTF8Encoding($false)',
                '[System.IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8), $utf8NoBom)',
                'Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $env:WORKSPACE "compare-smoke-manifest.json") -Force',
                '$env:PATH = $package.runtime + ";" + $package.dxc + ";" + $env:PATH',
                '$log = Join-Path $env:WORKSPACE "ex40-compare-smoke-set.log"',
                'Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue',
                '$runArgs = @("--compare-report-set", $manifestPath, "--report-dir", $publishRoot)',
                'Write-Host ("Running compare smoke set: {0}" -f ($runArgs -join " "))',
                '$exitCode = 0',
                'Push-Location -LiteralPath $package.bin',
                'try {',
                '  & $package.exe @runArgs 2>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { Write-Host $_ }',
                '  $exitCode = $LASTEXITCODE',
                '} finally {',
                '  Pop-Location',
                '}',
                '$summaryPath = Join-Path $publishRoot "summary.json"',
                'if ($exitCode -ne 0) { throw "Compare smoke set failed with exit code $exitCode." }',
                'if (-not (Test-Path -LiteralPath $summaryPath)) { throw "Compare smoke set did not write summary.json." }',
                'Copy-Item -LiteralPath $summaryPath -Destination (Join-Path $env:WORKSPACE "compare-smoke-summary.json") -Force',
                'foreach ($pairRoot in @("pairs/amd_vs_nvidia", "pairs/intel_vs_nvidia")) {',
                '  $pairPath = Join-Path $publishRoot $pairRoot',
                '  if (-not (Test-Path -LiteralPath $pairPath)) { throw "Compare smoke pair output is missing: $pairRoot" }',
                '  Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $pairPath -Recurse -Force',
                '}',
                'foreach ($relative in @("index.html", "summary.json", "pairs/amd_vs_nvidia/index.html", "pairs/intel_vs_nvidia/index.html")) {',
                '  if (-not (Test-Path -LiteralPath (Join-Path $publishRoot $relative))) { throw "Compare smoke output is missing: $relative" }',
                '}'
              ].join('\n')

              writeFile file: 'prepare-compare-smoke-publish.ps1', text: [
                '$ErrorActionPreference = "Stop"',
                '$publishRoot = Join-Path $env:WORKSPACE "compare-smoke-publish"',
                '$zipPath = Join-Path $env:WORKSPACE "publish-compare-smoke.zip"',
                'if (-not (Test-Path -LiteralPath $publishRoot)) { throw "Compare smoke publish directory does not exist." }',
                'if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }',
                '$files = Get-ChildItem -LiteralPath $publishRoot -Recurse -File',
                'if (-not $files) { throw "Compare smoke publish directory is empty." }',
                '$started = Get-Date',
                'Compress-Archive -Path (Join-Path $publishRoot "*") -DestinationPath $zipPath -Force',
                '$elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds',
                '$zipSize = (Get-Item -LiteralPath $zipPath).Length',
                'Write-Host ("Prepared publish-compare-smoke.zip with {0} files, {1} bytes in {2} ms." -f @($files).Count, $zipSize, $elapsedMs)'
              ].join('\n')
            }

            [
              [id: 'nvidia', name: 'NVIDIA'],
              [id: 'amd', name: 'AMD'],
              [id: 'intel', name: 'Intel']
            ].each { smokeInput ->
              stage('Compare smoke render ' + smokeInput.name) {
                powershell('./run-compare-smoke-render.ps1 -Id "' + smokeInput.id + '" -Name "' + smokeInput.name + '"')
              }
            }

            stage('Compare smoke set') {
              powershell './run-compare-smoke-set.ps1'
              def smokeSummary = readJSON(file: 'compare-smoke-publish/summary.json', returnPojo: true)
              compareSmokeFailureCount = (smokeSummary.failure_count ?: 0) as int
              compareSmokeTestCount = (smokeSummary.num_of_tests ?: 0) as int
              updateBuildDescription()
              def warningCount = (smokeSummary.warning_count ?: 0) as int
              if (compareSmokeFailureCount > 0 || warningCount > 0) {
                error("Compare smoke failed: failures=${compareSmokeFailureCount}, warnings=${warningCount}.")
              }
            }

            stage('Publish compare smoke') {
              if (publish) {
                powershell './prepare-compare-smoke-publish.ps1'
                def result = storePublishReportUpload(compareSmokeStorePrefix, 'publish-compare-smoke.zip', [
                  jobs: args.get('compareSmokePublishJobs', 4),
                  pruneAfterPublish: args.get('compareSmokePruneAfterPublish', true),
                  artifactName: 'publish-compare-smoke.zip'
                ])
                compareSmokeUrl = result.url
                updateBuildDescription()
              } else {
                echo 'Compare smoke publishing disabled by PUBLISH=false.'
              }
            }
          }

          stage('Select shard') {
            writeFile file: 'select-scenes.ps1', text: [
              '$ErrorActionPreference = "Stop"',
              '$info = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "scene-cache-info.json") | ConvertFrom-Json',
              '$commands = @()',
              'foreach ($line in Get-Content -LiteralPath $info.sceneList) {',
              '  $trimmed = $line.Trim()',
              '  if ($trimmed.Length -eq 0) { continue }',
              '  if ($trimmed.StartsWith(";") -or $trimmed.StartsWith("#")) { continue }',
              '  $commands += $line',
              '}',
              'if ($commands.Count -eq 0) { throw "Scene list contains no runnable scenes." }',
              '$shardCount = [int]$env:SHARD_COUNT',
              '$shardIndex = [int]$env:SHARD_INDEX',
              '$rootForList = $info.root.Replace([char]92, [char]47)',
              '$sceneRootToken = [string][char]36 + "{SCENE_ROOT}"',
              '$selected = @()',
              'for ($i = 0; $i -lt $commands.Count; $i++) {',
              '  if (($i % $shardCount) -eq $shardIndex) {',
              '    $selected += $commands[$i].Replace($sceneRootToken, $rootForList)',
              '  }',
              '}',
              'if ($selected.Count -eq 0) { throw "Shard selected no scenes." }',
              '$selectedPath = Join-Path $env:WORKSPACE "selected-scenes.txt"',
              '$utf8NoBom = New-Object System.Text.UTF8Encoding($false)',
              '[System.IO.File]::WriteAllLines($selectedPath, [string[]]$selected, $utf8NoBom)',
              '$resolved = [pscustomObject]@{ suite = $info.suite; commit = $info.commit; shardIndex = $shardIndex; shardCount = $shardCount; selectedSceneCount = $selected.Count; totalSceneCount = $commands.Count; sceneList = $selectedPath; referenceDir = $info.referenceDir }',
              '[System.IO.File]::WriteAllText((Join-Path $env:WORKSPACE "resolved-scenes.json"), ($resolved | ConvertTo-Json -Depth 4), $utf8NoBom)',
              'Write-Host ("Selected {0}/{1} scenes for shard {2}/{3}." -f $selected.Count, $commands.Count, $shardIndex, $shardCount)'
            ].join('\n')
            powershell './select-scenes.ps1'
            def resolved = readJSON(file: 'resolved-scenes.json', returnPojo: true)
            selectedSceneCount = (resolved.selectedSceneCount ?: 0) as int
            totalSceneCount = (resolved.totalSceneCount ?: 0) as int
            updateBuildDescription()
          }

          if (isolateScenes) {
            def sceneTimeoutSeconds = args.get('sceneTimeoutSeconds', 900) as int
            if (sceneTimeoutSeconds < 30 || sceneTimeoutSeconds > 14400) {
              error('sceneTimeoutSeconds is outside the allowed range.')
            }

            stage('Prepare isolated render') {
              writeFile file: 'prepare-isolated-render.ps1', text: [
                '$ErrorActionPreference = "Stop"',
                '. (Join-Path $env:WORKSPACE "publish-root.ps1")',
                '$package = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-info.json") | ConvertFrom-Json',
                '$scenes = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "resolved-scenes.json") | ConvertFrom-Json',
                '$publishRoot = Initialize-PublishRoot -Clean',
                '$renders = Join-Path $publishRoot "renders"',
                '$summaryDir = Join-Path $env:WORKSPACE "isolated-summaries"',
                '$sharedTmp = Join-Path $package.bin "../../tmp"',
                'if (Test-Path -LiteralPath $publishRoot) { Remove-Item -LiteralPath $publishRoot -Recurse -Force }',
                'if (Test-Path -LiteralPath $summaryDir) { Remove-Item -LiteralPath $summaryDir -Recurse -Force }',
                'New-Item -ItemType Directory -Path $renders -Force | Out-Null',
                'New-Item -ItemType Directory -Path $summaryDir -Force | Out-Null',
                'New-Item -ItemType Directory -Path $sharedTmp -Force | Out-Null',
                'Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $publishRoot -Recurse -Force',
                '$log = Join-Path $env:WORKSPACE "ex40.log"',
                'Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue',
                '$sceneLines = @(Get-Content -LiteralPath $scenes.sceneList | Where-Object { $_.Trim().Length -gt 0 })',
                'if ($sceneLines.Count -eq 0) { throw "Selected scene list is empty." }',
                '$utf8NoBom = New-Object System.Text.UTF8Encoding($false)',
                'for ($i = 0; $i -lt $sceneLines.Count; $i++) {',
                '  $sceneNumber = $i + 1',
                '  $oneSceneList = Join-Path $env:WORKSPACE ("selected-scene-{0:D4}.txt" -f $sceneNumber)',
                '  [System.IO.File]::WriteAllLines($oneSceneList, [string[]]@($sceneLines[$i]), $utf8NoBom)',
                '}',
                '[System.IO.File]::WriteAllText((Join-Path $env:WORKSPACE "isolated-scene-count.txt"), [string]$sceneLines.Count, $utf8NoBom)',
                'Write-Host ("Prepared {0} isolated scene invocations." -f $sceneLines.Count)'
              ].join('\n')
              powershell './prepare-isolated-render.ps1'

              writeFile file: 'kill-pathtracer.ps1', text: [
                '$ErrorActionPreference = "Continue"',
                '$package = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-info.json") | ConvertFrom-Json',
                '$name = [System.IO.Path]::GetFileNameWithoutExtension($package.exe)',
                'Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue'
              ].join('\n')

              writeFile file: 'run-isolated-scene.ps1', text: [
                'param([Parameter(Mandatory = $true)][int] $SceneNumber)',
                '$ErrorActionPreference = "Stop"',
                '. (Join-Path $env:WORKSPACE "publish-root.ps1")',
                '$package = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-info.json") | ConvertFrom-Json',
                '$scenes = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "resolved-scenes.json") | ConvertFrom-Json',
                '$publishRoot = Initialize-PublishRoot',
                '$renders = Join-Path $publishRoot "renders"',
                '$summaryDir = Join-Path $env:WORKSPACE "isolated-summaries"',
                '$summaryPath = Join-Path $publishRoot "summary.json"',
                '$summaryCopy = Join-Path $summaryDir ("summary-{0:D4}.json" -f $SceneNumber)',
                '$exitPath = Join-Path $summaryDir ("exit-{0:D4}.txt" -f $SceneNumber)',
                '$oneSceneList = Join-Path $env:WORKSPACE ("selected-scene-{0:D4}.txt" -f $SceneNumber)',
                '$log = Join-Path $env:WORKSPACE "ex40.log"',
                'if (-not (Test-Path -LiteralPath $oneSceneList)) { throw "Selected scene file is missing: $oneSceneList" }',
                'Remove-Item -LiteralPath $summaryPath -Force -ErrorAction SilentlyContinue',
                'Remove-Item -LiteralPath $summaryCopy -Force -ErrorAction SilentlyContinue',
                '$env:PATH = $package.runtime + ";" + $package.dxc + ";" + $env:PATH',
                '& (Join-Path $env:WORKSPACE "ex40-lds-cache.ps1") -Mode Restore -PackageInfoPath (Join-Path $env:WORKSPACE "package-info.json")',
                '$runArgs = @("--scene-list", $oneSceneList, "--process-sensors", "RenderAllThenTerminate", "--headless", "--output-dir", $renders, "--report-dir", $publishRoot)',
                'if ($scenes.referenceDir) { $runArgs += @("--reference-dir", $scenes.referenceDir) }',
                'Write-Host ("Running isolated scene {0}." -f $SceneNumber)',
                '$exitCode = 0',
                'Push-Location -LiteralPath $package.bin',
                'try {',
                '  & $package.exe @runArgs 2>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { Write-Host $_ }',
                '  $exitCode = $LASTEXITCODE',
                '} finally {',
                '  Pop-Location',
                '}',
                '& (Join-Path $env:WORKSPACE "ex40-lds-cache.ps1") -Mode Save -PackageInfoPath (Join-Path $env:WORKSPACE "package-info.json")',
                '[System.IO.File]::WriteAllText($exitPath, [string]$exitCode)',
                'if (Test-Path -LiteralPath $summaryPath) { Copy-Item -LiteralPath $summaryPath -Destination $summaryCopy -Force }',
                'if ($exitCode -ne 0) {',
                '  if ($env:FAIL_ON_RENDER_FAILURE -eq "true") { throw "EX40 failed with exit code $exitCode for isolated scene $SceneNumber." }',
                '  Write-Warning ("EX40 exited with code {0} for isolated scene {1}; continuing because FAIL_ON_RENDER_FAILURE=false." -f $exitCode, $SceneNumber)',
                '}',
                'exit 0'
              ].join('\n')

              writeFile file: 'merge-isolated-report.ps1', text: [
                '$ErrorActionPreference = "Stop"',
                '. (Join-Path $env:WORKSPACE "publish-root.ps1")',
                '$publishRoot = Initialize-PublishRoot',
                '$summaryDir = Join-Path $env:WORKSPACE "isolated-summaries"',
                '$summaryPath = Join-Path $publishRoot "summary.json"',
                'Write-Host "Starting isolated report merge."',
                'function Get-SceneDisplayName {',
                '  param([Parameter(Mandatory = $true)][string] $Line, [Parameter(Mandatory = $true)][int] $SceneNumber)',
                '  $scenePath = ""',
                '  if ($Line -match "^\\s*`"([^`"]+)`"") { $scenePath = $Matches[1] } else { $scenePath = ($Line -split "\\s+", 2)[0].Trim([char]34) }',
                '  if ($scenePath) { return [System.IO.Path]::GetFileNameWithoutExtension($scenePath) }',
                '  return "scene_" + ("{0:D2}" -f $SceneNumber)',
                '}',
                '$sceneListPath = Join-Path $env:WORKSPACE "selected-scenes.txt"',
                '$sceneLines = @([System.IO.File]::ReadAllLines($sceneListPath) | Where-Object { $_.Trim().Length -gt 0 })',
                'Write-Host ("Loaded {0} isolated scene lines." -f $sceneLines.Count)',
                '$results = New-Object System.Collections.ArrayList',
                '$mergedFailureCount = 0',
                '$mergedTestCount = 0',
                '$utf8NoBom = New-Object System.Text.UTF8Encoding($false)',
                'for ($i = 0; $i -lt $sceneLines.Count; $i++) {',
                '  $sceneNumber = $i + 1',
                '  $line = $sceneLines[$i]',
                '  $exitPath = Join-Path $summaryDir ("exit-{0:D4}.txt" -f $sceneNumber)',
                '  $timeoutPath = Join-Path $summaryDir ("timeout-{0:D4}.txt" -f $sceneNumber)',
                '  $exitText = if (Test-Path -LiteralPath $exitPath) { (Get-Content -LiteralPath $exitPath -Raw).Trim() } else { "" }',
                '  $hasNonZeroExit = $exitText -and $exitText -ne "0"',
                '  $display = Get-SceneDisplayName -Line $line -SceneNumber $sceneNumber',
                '  $isFailed = (Test-Path -LiteralPath $timeoutPath) -or $hasNonZeroExit -or -not (Test-Path -LiteralPath $exitPath)',
                '  $reason = if (Test-Path -LiteralPath $timeoutPath) { "EX40 exceeded Jenkins scene timeout before writing summary.json." } elseif ($hasNonZeroExit) { "EX40 exited with code " + $exitText + "." } elseif (Test-Path -LiteralPath $exitPath) { "EX40 exited without detailed merged results." } else { "EX40 did not write exit status." }',
                '  $status = if ($isFailed) { "failed" } else { "passed" }',
                '  $statusColor = if ($isFailed) { "red" } else { "green" }',
                '  $imageStatus = if ($isFailed) { "error" } else { "ok" }',
                '  $runtimeImage = [pscustomobject]@{ identifier = "runtime"; title = "Runtime"; status = $imageStatus; status_color = $statusColor; details = $reason; filename = $display }',
                '  $runtimeScene = [pscustomobject]@{ array = @($runtimeImage); compare = $null; details = ("Command: {0}" -f $line); display_name = $display; index = $sceneNumber; scene_name = ("{0:D2}_{1}" -f $sceneNumber, $display); scene_path = $line; sensor = 0; status = $status; status_color = $statusColor }',
                '  [void]$results.Add($runtimeScene)',
                '  if ($isFailed) { $mergedFailureCount++ }',
                '  $mergedTestCount++',
                '}',
                '$passStatus = if ($mergedFailureCount -gt 0) { "failed" } else { "passed" }',
                '$summary = [pscustomobject]@{ buildConfig = $env:CONFIGURATION; compare = @{}; datetime = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz"); failure_count = $mergedFailureCount; num_of_tests = $mergedTestCount; pass_status = $passStatus; results = @($results) }',
                'Write-Host ("Writing merged isolated summary.json. tests={0}, failures={1}." -f $mergedTestCount, $mergedFailureCount)',
                '$finalJson = $summary | ConvertTo-Json -Depth 16',
                '[System.IO.File]::WriteAllText($summaryPath, $finalJson, $utf8NoBom)',
                'Sync-PublishSummaryToWorkspace -PublishRoot $publishRoot',
                'Write-Host "Merged isolated summary.json is ready."'
              ].join('\n')
            }

            def selectedScenes = readFile('selected-scenes.txt').readLines().findAll { it.trim() }
            for (int sceneOffset = 0; sceneOffset < selectedScenes.size(); sceneOffset++) {
              def sceneNumber = sceneOffset + 1
              stage('Scene ' + sceneNumber) {
                try {
                  timeout(time: sceneTimeoutSeconds, unit: 'SECONDS') {
                    powershell script: './run-isolated-scene.ps1 -SceneNumber ' + sceneNumber
                  }
                } catch (err) {
                  timeout(time: 1, unit: 'MINUTES') {
                    powershell script: './kill-pathtracer.ps1'
                  }
                  writeFile file: String.format('isolated-summaries/timeout-%04d.txt', sceneNumber), text: 'timeout'
                  if (failOnRenderFailure) {
                    throw err
                  }
                  echo('Scene ' + sceneNumber + ' timed out or failed unexpectedly; continuing because FAIL_ON_RENDER_FAILURE=false.')
                }
              }
            }

            stage('Merge isolated report') {
              timeout(time: 5, unit: 'MINUTES') {
                powershell './merge-isolated-report.ps1'
              }
            }
          } else {
            stage('Render scenes') {
              writeFile file: 'run-scenes.ps1', text: [
                '$ErrorActionPreference = "Stop"',
                '. (Join-Path $env:WORKSPACE "publish-root.ps1")',
                '$package = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-info.json") | ConvertFrom-Json',
                '$scenes = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "resolved-scenes.json") | ConvertFrom-Json',
                '$publishRoot = Initialize-PublishRoot -Clean',
                '$renders = Join-Path $publishRoot "renders"',
                '$sharedTmp = Join-Path $package.bin "../../tmp"',
                'if (Test-Path -LiteralPath $publishRoot) { Remove-Item -LiteralPath $publishRoot -Recurse -Force }',
                'New-Item -ItemType Directory -Path $renders -Force | Out-Null',
                'New-Item -ItemType Directory -Path $sharedTmp -Force | Out-Null',
                'Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $publishRoot -Recurse -Force',
                '$env:PATH = $package.runtime + ";" + $package.dxc + ";" + $env:PATH',
                '$log = Join-Path $env:WORKSPACE "ex40.log"',
                'Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue',
                '& (Join-Path $env:WORKSPACE "ex40-lds-cache.ps1") -Mode Restore -PackageInfoPath (Join-Path $env:WORKSPACE "package-info.json")',
                '$runArgs = @("--scene-list", $scenes.sceneList, "--process-sensors", "RenderAllThenTerminate", "--headless", "--output-dir", $renders, "--report-dir", $publishRoot)',
                'if ($scenes.referenceDir) { $runArgs += @("--reference-dir", $scenes.referenceDir) }',
                '$exitCode = 0',
                'Push-Location -LiteralPath $package.bin',
                'try {',
                '  & $package.exe @runArgs 2>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { Write-Host $_ }',
                '  $exitCode = $LASTEXITCODE',
                '} finally {',
                '  Pop-Location',
                '}',
                '& (Join-Path $env:WORKSPACE "ex40-lds-cache.ps1") -Mode Save -PackageInfoPath (Join-Path $env:WORKSPACE "package-info.json")',
                'if ($exitCode -ne 0) {',
                '  $summaryPath = Join-Path $publishRoot "summary.json"',
                '  if ($env:FAIL_ON_RENDER_FAILURE -eq "true" -or -not (Test-Path -LiteralPath $summaryPath)) { throw "EX40 failed with exit code $exitCode." }',
                '  Write-Warning ("EX40 exited with code {0}; continuing because FAIL_ON_RENDER_FAILURE=false and summary.json exists." -f $exitCode)',
                '}',
                '$logText = if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Raw } else { "" }',
                'if ($logText.Contains("[ERROR]") -or $logText.Contains("Failed to Load") -or $logText.Contains("Could not create scene")) { Write-Warning "EX40 log contains render or scene errors; continuing because the report captures them." }',
                'exit 0'
              ].join('\n')
              powershell './run-scenes.ps1'
            }
          }

          stage('Validate report') {
            writeFile file: 'validate-report.ps1', text: [
              '$ErrorActionPreference = "Stop"',
              '. (Join-Path $env:WORKSPACE "publish-root.ps1")',
              '$publishRoot = Initialize-PublishRoot',
              '$required = @("index.html", "summary.json", "css/report.css", "js/report.js")',
              'foreach ($relative in $required) {',
              '  $path = Join-Path $publishRoot $relative',
              '  if (-not (Test-Path -LiteralPath $path)) { throw "Missing publish file: $relative" }',
              '}',
              '$summary = Get-Content -LiteralPath (Join-Path $publishRoot "summary.json") | ConvertFrom-Json',
              'if ([int]$summary.num_of_tests -lt 1) { throw "Summary contains no rendered tests." }',
              '$failureCount = [int]$summary.failure_count',
              'Write-Host ("EX40 report status={0}, tests={1}, failures={2}" -f $summary.pass_status, $summary.num_of_tests, $failureCount)',
              'if ($summary.PSObject.Properties.Name -contains "lowDiscrepancySequenceCache") {',
              '  $lds = $summary.lowDiscrepancySequenceCache',
              '  Write-Host ("LDS cache report status={0}, size={1}, hash={2}" -f $lds.status, $lds.sizeBytes, $lds.hash)',
              '}',
              'Sync-PublishSummaryToWorkspace -PublishRoot $publishRoot',
              'if ($env:FAIL_ON_RENDER_FAILURE -eq "true" -and $failureCount -gt 0) { throw "EX40 report contains failed scenes." }'
            ].join('\n')
            powershell './validate-report.ps1'
            def summary = readJSON(file: 'publish/summary.json', returnPojo: true)
            def failureCount = (summary.failure_count ?: 0) as int
            reportFailureCount = failureCount
            reportTestCount = (summary.num_of_tests ?: 0) as int
            updateBuildDescription()
            if (!failOnRenderFailure && failureCount > 0) {
              unstable("EX40 report contains ${failureCount} failed scene(s).")
            }
          }

          stage('Prepare publish') {
            if (publish) {
              writeFile file: 'prepare-publish-zip.ps1', text: [
                '$ErrorActionPreference = "Stop"',
                '. (Join-Path $env:WORKSPACE "publish-root.ps1")',
                '$publishRoot = Initialize-PublishRoot',
                '$zipPath = Join-Path $env:WORKSPACE "publish.zip"',
                'if (-not (Test-Path -LiteralPath $publishRoot)) { throw "Publish directory does not exist." }',
                'if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }',
                '$files = Get-ChildItem -LiteralPath $publishRoot -Recurse -File',
                'if (-not $files) { throw "Publish directory is empty." }',
                '$started = Get-Date',
                'Compress-Archive -Path (Join-Path $publishRoot "*") -DestinationPath $zipPath -Force',
                '$elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds',
                '$zipSize = (Get-Item -LiteralPath $zipPath).Length',
                'Write-Host ("Prepared publish.zip with {0} files, {1} bytes in {2} ms." -f @($files).Count, $zipSize, $elapsedMs)'
              ].join('\n')
              powershell './prepare-publish-zip.ps1'
              storePublishArtifact = 'publish.zip'
            } else {
              echo 'Publishing disabled by PUBLISH=false.'
            }
          }

          stage('Artifacts') {
            archiveArtifacts artifacts: 'package-info.json,scene-cache-info.json,scene-git-request.json,git-object-cache.json,ex40-lds-cache.json,resolved-scenes.json,selected-scenes.txt,ex40.log,publish/index.html,publish/summary.json,compare-smoke-manifest.json,compare-smoke-summary.json,ex40-compare-smoke-*.log,compare-smoke-publish/index.html,compare-smoke-publish/summary.json', allowEmptyArchive: true, fingerprint: false
          }

          stage('Publish report') {
            if (publish) {
              if (!storePublishArtifact) {
                error 'Store publish artifact was not prepared.'
              }
              def result = storePublishReportUpload(storePrefix, storePublishArtifact, [
                jobs: args.get('publishJobs', 8),
                pruneAfterPublish: args.get('pruneAfterPublish', true)
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
    }
  }
}
}
