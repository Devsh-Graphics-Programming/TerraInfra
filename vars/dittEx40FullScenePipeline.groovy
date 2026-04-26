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
  def publish = true
  def storePublishArtifact = null

  timestamps {
    stage('Validate request') {
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
      publish = params.PUBLISH == null ? (args.get('publishDefault', true) as boolean) : (params.PUBLISH as boolean)
      echo('Suite: ' + suite + ', shard=' + shardIndex + '/' + shardCount + ', store_prefix=' + storePrefix + ', publish=' + publish)
    }

    withRunner(
      labels: args.get('labels', ['windows', 'gpu', 'nvidia', 'vulkan', 'runtime-only']),
      leaseTtlMinutes: args.get('leaseTtlMinutes', 360),
      maxReadySeconds: args.get('maxReadySeconds', 180)
    ) { runner ->
      withFileParameter(name: args.get('packageFileParameter', 'EX40_PACKAGE_FILE'), allowNoFile: true) {
        withEnv([
          'EX40_PACKAGE_URL=' + (packageUrl ?: ''),
          'SCENE_SUITE=' + suite,
          'SHARD_COUNT=' + shardCount.toString(),
          'SHARD_INDEX=' + shardIndex.toString(),
          'FAIL_ON_RENDER_FAILURE=' + failOnRenderFailure.toString()
        ]) {
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
              '$exe = Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "40_pathtracer*.exe" | Select-Object -First 1',
              'if (-not $exe) { throw "40_pathtracer executable was not found in the package." }',
              '$runtimeDll = Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "Nabla_*.dll" | Select-Object -First 1',
              'if (-not $runtimeDll) { throw "Nabla runtime DLL was not found in the package." }',
              '$dxcDll = Get-ChildItem -LiteralPath $extractRoot -Recurse -File -Filter "dxcompiler.dll" | Select-Object -First 1',
              'if (-not $dxcDll) { throw "DXC runtime DLL was not found in the package." }',
              '$reportTemplate = Join-Path $exe.DirectoryName "report"',
              'if (-not (Test-Path -LiteralPath $reportTemplate)) { throw "Report template directory was not found next to the executable." }',
              '$info = [pscustomobject]@{ exe = $exe.FullName; bin = $exe.DirectoryName; runtime = $runtimeDll.DirectoryName; dxc = $dxcDll.DirectoryName; reportTemplate = $reportTemplate; packageSize = $packageSize }',
              '$info | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $env:WORKSPACE "package-info.json") -Encoding UTF8',
              'Write-Host ("EX40 executable: {0}" -f $exe.FullName)'
            ].join('\n')
            powershell './acquire-package.ps1'
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
              '$resolved | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $env:WORKSPACE "resolved-scenes.json") -Encoding UTF8',
              'Write-Host ("Selected {0}/{1} scenes for shard {2}/{3}." -f $selected.Count, $commands.Count, $shardIndex, $shardCount)'
            ].join('\n')
            powershell './select-scenes.ps1'
          }

          stage('Render scenes') {
            writeFile file: 'run-scenes.ps1', text: [
              '$ErrorActionPreference = "Stop"',
              '$package = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-info.json") | ConvertFrom-Json',
              '$scenes = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "resolved-scenes.json") | ConvertFrom-Json',
              '$publishRoot = Join-Path $env:WORKSPACE "publish"',
              '$renders = Join-Path $publishRoot "renders"',
              '$sharedTmp = Join-Path $package.bin "../../tmp"',
              'if (Test-Path -LiteralPath $publishRoot) { Remove-Item -LiteralPath $publishRoot -Recurse -Force }',
              'New-Item -ItemType Directory -Path $renders -Force | Out-Null',
              'New-Item -ItemType Directory -Path $sharedTmp -Force | Out-Null',
              'Copy-Item -Path (Join-Path $package.reportTemplate "*") -Destination $publishRoot -Recurse -Force',
              '$env:PATH = $package.runtime + ";" + $package.dxc + ";" + $env:PATH',
              '$log = Join-Path $env:WORKSPACE "ex40.log"',
              '$args = @("--scene-list", $scenes.sceneList, "--process-sensors", "RenderAllThenTerminate", "--headless", "--output-dir", $renders, "--report-dir", $publishRoot)',
              'if ($scenes.referenceDir) { $args += @("--reference-dir", $scenes.referenceDir) }',
              '$exitCode = 0',
              'Push-Location -LiteralPath $package.bin',
              'try {',
              '  & $package.exe @args 2>&1 | Tee-Object -FilePath $log',
              '  $exitCode = $LASTEXITCODE',
              '} finally {',
              '  Pop-Location',
              '}',
              'if ($exitCode -ne 0) {',
              '  $summaryPath = Join-Path $publishRoot "summary.json"',
              '  if ($env:FAIL_ON_RENDER_FAILURE -eq "true" -or -not (Test-Path -LiteralPath $summaryPath)) { throw "EX40 failed with exit code $exitCode." }',
              '  Write-Warning ("EX40 exited with code {0}; continuing because FAIL_ON_RENDER_FAILURE=false and summary.json exists." -f $exitCode)',
              '}',
              '$logText = Get-Content -LiteralPath $log -Raw',
              'if ($logText.Contains("[ERROR]") -or $logText.Contains("Failed to Load") -or $logText.Contains("Could not create scene")) { throw "EX40 log contains scene load or runtime errors." }',
              'exit 0'
            ].join('\n')
            powershell './run-scenes.ps1'
          }

          stage('Validate report') {
            writeFile file: 'validate-report.ps1', text: [
              '$ErrorActionPreference = "Stop"',
              '$publishRoot = Join-Path $env:WORKSPACE "publish"',
              '$required = @("index.html", "summary.json", "css/report.css", "js/report.js")',
              'foreach ($relative in $required) {',
              '  $path = Join-Path $publishRoot $relative',
              '  if (-not (Test-Path -LiteralPath $path)) { throw "Missing publish file: $relative" }',
              '}',
              '$summary = Get-Content -LiteralPath (Join-Path $publishRoot "summary.json") | ConvertFrom-Json',
              'if ([int]$summary.num_of_tests -lt 1) { throw "Summary contains no rendered tests." }',
              '$failureCount = [int]$summary.failure_count',
              'Write-Host ("EX40 report status={0}, tests={1}, failures={2}" -f $summary.pass_status, $summary.num_of_tests, $failureCount)',
              'if ($env:FAIL_ON_RENDER_FAILURE -eq "true" -and $failureCount -gt 0) { throw "EX40 report contains failed scenes." }'
            ].join('\n')
            powershell './validate-report.ps1'
          }

          stage('Prepare publish') {
            if (publish) {
              writeFile file: 'prepare-publish-zip.ps1', text: [
                '$ErrorActionPreference = "Stop"',
                '$publishRoot = Join-Path $env:WORKSPACE "publish"',
                '$zipPath = Join-Path $env:WORKSPACE "publish.zip"',
                'if (-not (Test-Path -LiteralPath $publishRoot)) { throw "Publish directory does not exist." }',
                'if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }',
                '$files = Get-ChildItem -LiteralPath $publishRoot -Recurse -File',
                'if (-not $files) { throw "Publish directory is empty." }',
                'Compress-Archive -Path (Join-Path $publishRoot "*") -DestinationPath $zipPath -Force',
                '$zipSize = (Get-Item -LiteralPath $zipPath).Length',
                'Write-Host ("Prepared publish.zip with {0} files, {1} bytes." -f @($files).Count, $zipSize)'
              ].join('\n')
              powershell './prepare-publish-zip.ps1'
              storePublishArtifact = 'publish.zip'
            } else {
              echo 'Publishing disabled by PUBLISH=false.'
            }
          }

          stage('Artifacts') {
            archiveArtifacts artifacts: 'package-info.json,scene-cache-info.json,scene-git-request.json,git-object-cache.json,resolved-scenes.json,selected-scenes.txt,ex40.log,publish.zip,publish/index.html,publish/summary.json', allowEmptyArchive: true, fingerprint: false
          }
        }
      }
    }

    stage('Publish report') {
      if (publish) {
        if (!storePublishArtifact) {
          error 'Store publish artifact was not prepared.'
        }
        def result = storePublishReportArtifact(storePrefix, storePublishArtifact, [jobs: args.get('publishJobs', 8)])
        currentBuild.description = result.url
      } else {
        echo 'Publishing disabled by PUBLISH=false.'
      }
    }
  }
}
