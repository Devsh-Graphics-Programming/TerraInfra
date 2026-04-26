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
      isolateScenes = params.ISOLATE_SCENES == null ? (args.get('isolateScenesDefault', false) as boolean) : (params.ISOLATE_SCENES as boolean)
      publish = params.PUBLISH == null ? (args.get('publishDefault', true) as boolean) : (params.PUBLISH as boolean)
      echo('Suite: ' + suite + ', shard=' + shardIndex + '/' + shardCount + ', store_prefix=' + storePrefix + ', isolate_scenes=' + isolateScenes + ', publish=' + publish)
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
          'FAIL_ON_RENDER_FAILURE=' + failOnRenderFailure.toString(),
          'ISOLATE_SCENES=' + isolateScenes.toString()
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
              'Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue',
              'function Invoke-PathTracerSceneList {',
              '  param([Parameter(Mandatory = $true)][string] $SceneList)',
              '  $runArgs = @("--scene-list", $SceneList, "--process-sensors", "RenderAllThenTerminate", "--headless", "--output-dir", $renders, "--report-dir", $publishRoot)',
              '  if ($scenes.referenceDir) { $runArgs += @("--reference-dir", $scenes.referenceDir) }',
              '  $runExitCode = 0',
              '  Push-Location -LiteralPath $package.bin',
              '  try {',
              '    & $package.exe @runArgs 2>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { Write-Host $_ }',
              '    $runExitCode = $LASTEXITCODE',
              '  } finally {',
              '    Pop-Location',
              '  }',
              '  return $runExitCode',
              '}',
              'function Count-ReportFailures {',
              '  param([Parameter(Mandatory = $true)] $Results)',
              '  $count = 0',
              '  foreach ($sceneResult in @($Results)) {',
              '    $images = if ($sceneResult -is [System.Collections.IDictionary]) { $sceneResult["array"] } else { $sceneResult.array }',
              '    foreach ($image in @($images)) {',
              '      $rawStatus = if ($image -is [System.Collections.IDictionary]) { $image["status"] } else { $image.status }',
              '      $status = ([string]$rawStatus).ToLowerInvariant()',
              '      if ($status -in @("failed", "error", "missing-render", "missing-reference")) { $count++ }',
              '    }',
              '  }',
              '  return $count',
              '}',
              'function Get-SceneDisplayName {',
              '  param([Parameter(Mandatory = $true)][string] $Line, [Parameter(Mandatory = $true)][int] $SceneNumber)',
              '  $scenePath = ""',
              '  if ($Line -match "^\\s*`"([^`"]+)`"") { $scenePath = $Matches[1] } else { $scenePath = ($Line -split "\\s+", 2)[0].Trim([char]34) }',
              '  if ($scenePath) { return [System.IO.Path]::GetFileNameWithoutExtension($scenePath) }',
              '  return "scene_" + ("{0:D2}" -f $SceneNumber)',
              '}',
              'if ($env:ISOLATE_SCENES -eq "true") {',
              '  Add-Type -AssemblyName System.Web.Extensions',
              '  $serializer = New-Object System.Web.Script.Serialization.JavaScriptSerializer',
              '  $serializer.MaxJsonLength = [int]::MaxValue',
              '  $serializer.RecursionLimit = 100',
              '  function Find-JsonArrayRange {',
              '    param([Parameter(Mandatory = $true)][string] $Json, [Parameter(Mandatory = $true)][string] $Property)',
              '    $match = [regex]::Match($Json, "`"" + [regex]::Escape($Property) + "`"\\s*:")',
              '    if (-not $match.Success) { throw "JSON property was not found: $Property" }',
              '    $start = $Json.IndexOf("[", $match.Index + $match.Length)',
              '    if ($start -lt 0) { throw "JSON array property was not found: $Property" }',
              '    $depth = 0',
              '    $inString = $false',
              '    $escape = $false',
              '    for ($pos = $start; $pos -lt $Json.Length; $pos++) {',
              '      $char = $Json[$pos]',
              '      if ($inString) {',
              '        if ($escape) { $escape = $false }',
              '        elseif ($char -eq [char]92) { $escape = $true }',
              '        elseif ($char -eq [char]34) { $inString = $false }',
              '        continue',
              '      }',
              '      if ($char -eq [char]34) { $inString = $true; continue }',
              '      if ($char -eq "[") { $depth++ }',
              '      elseif ($char -eq "]") {',
              '        $depth--',
              '        if ($depth -eq 0) { return @{ start = $start; end = $pos } }',
              '      }',
              '    }',
              '    throw "JSON array property was not terminated: $Property"',
              '  }',
              '  function Get-JsonArrayContent {',
              '    param([Parameter(Mandatory = $true)][string] $Json, [Parameter(Mandatory = $true)][string] $Property)',
              '    $range = Find-JsonArrayRange -Json $Json -Property $Property',
              '    return $Json.Substring($range.start + 1, $range.end - $range.start - 1).Trim()',
              '  }',
              '  function Set-JsonArrayRaw {',
              '    param([Parameter(Mandatory = $true)][string] $Json, [Parameter(Mandatory = $true)][string] $Property, [Parameter(Mandatory = $true)][string] $RawArray)',
              '    $range = Find-JsonArrayRange -Json $Json -Property $Property',
              '    return $Json.Substring(0, $range.start) + $RawArray + $Json.Substring($range.end + 1)',
              '  }',
              '  function Set-JsonNumber {',
              '    param([Parameter(Mandatory = $true)][string] $Json, [Parameter(Mandatory = $true)][string] $Property, [Parameter(Mandatory = $true)][int] $Value)',
              '    $regex = New-Object System.Text.RegularExpressions.Regex("`"" + [regex]::Escape($Property) + "`"\\s*:\\s*[0-9]+")',
              '    return $regex.Replace($Json, "`"$Property`": $Value", 1)',
              '  }',
              '  function Set-JsonString {',
              '    param([Parameter(Mandatory = $true)][string] $Json, [Parameter(Mandatory = $true)][string] $Property, [Parameter(Mandatory = $true)][string] $Value)',
              '    $regex = New-Object System.Text.RegularExpressions.Regex("`"" + [regex]::Escape($Property) + "`"\\s*:\\s*`"[^`"]*`"")',
              '    return $regex.Replace($Json, "`"$Property`": " + $serializer.Serialize($Value), 1)',
              '  }',
              '  $sceneLines = @(Get-Content -LiteralPath $scenes.sceneList | Where-Object { $_.Trim().Length -gt 0 })',
              '  if ($sceneLines.Count -eq 0) { throw "Selected scene list is empty." }',
              '  $summaryPath = Join-Path $publishRoot "summary.json"',
              '  $resultJsonParts = New-Object System.Collections.ArrayList',
              '  $firstSummaryJson = $null',
              '  $mergedFailureCount = 0',
              '  $mergedTestCount = 0',
              '  for ($i = 0; $i -lt $sceneLines.Count; $i++) {',
              '    $sceneNumber = $i + 1',
              '    $line = $sceneLines[$i]',
              '    $oneSceneList = Join-Path $env:WORKSPACE ("selected-scene-{0:D4}.txt" -f $sceneNumber)',
              '    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)',
              '    [System.IO.File]::WriteAllLines($oneSceneList, [string[]]@($line), $utf8NoBom)',
              '    Remove-Item -LiteralPath $summaryPath -Force -ErrorAction SilentlyContinue',
              '    Write-Host ("Running isolated scene {0}/{1}." -f $sceneNumber, $sceneLines.Count)',
              '    $exitCode = Invoke-PathTracerSceneList -SceneList $oneSceneList',
              '    if (Test-Path -LiteralPath $summaryPath) {',
              '      $summaryJson = [System.IO.File]::ReadAllText($summaryPath)',
              '      $summary = $serializer.DeserializeObject($summaryJson)',
              '      if (-not $firstSummaryJson) { $firstSummaryJson = $summaryJson }',
              '      $resultsContent = Get-JsonArrayContent -Json $summaryJson -Property "results"',
              '      if ($resultsContent) { [void]$resultJsonParts.Add($resultsContent) }',
              '      $mergedFailureCount += [int]$summary["failure_count"]',
              '      $mergedTestCount += [int]$summary["num_of_tests"]',
              '    } else {',
              '      $display = Get-SceneDisplayName -Line $line -SceneNumber $sceneNumber',
              '      $runtimeImage = [pscustomobject]@{ identifier = "runtime"; title = "Runtime"; status = "error"; status_color = "red"; details = ("EX40 exited with code {0} before writing summary.json." -f $exitCode); filename = $display }',
              '      $runtimeScene = [pscustomobject]@{ array = @($runtimeImage); compare = $null; details = ("Command: {0}" -f $line); display_name = $display; index = $sceneNumber; scene_name = ("{0:D2}_{1}" -f $sceneNumber, $display); scene_path = $line; sensor = 0; status = "failed"; status_color = "red" }',
              '      [void]$resultJsonParts.Add(($runtimeScene | ConvertTo-Json -Depth 8 -Compress))',
              '      $mergedFailureCount++',
              '      $mergedTestCount++',
              '    }',
              '    if ($exitCode -ne 0) {',
              '      if ($env:FAIL_ON_RENDER_FAILURE -eq "true") { throw "EX40 failed with exit code $exitCode for isolated scene $sceneNumber." }',
              '      Write-Warning ("EX40 exited with code {0} for isolated scene {1}; continuing because FAIL_ON_RENDER_FAILURE=false." -f $exitCode, $sceneNumber)',
              '    }',
              '  }',
              '  if (-not $firstSummaryJson) {',
              '    $firstSummaryJson = "{`"buildConfig`":`"`",`"compare`":{},`"datetime`":`"`",`"failure_count`":0,`"num_of_tests`":0,`"pass_status`":`"failed`",`"results`":[]}"',
              '  }',
              '  Write-Host ("Merging {0} isolated scene result JSON fragments." -f $resultJsonParts.Count)',
              '  $finalJson = Set-JsonArrayRaw -Json $firstSummaryJson -Property "results" -RawArray ("[" + (($resultJsonParts.ToArray()) -join ",") + "]")',
              '  $finalJson = Set-JsonNumber -Json $finalJson -Property "num_of_tests" -Value $mergedTestCount',
              '  $finalJson = Set-JsonNumber -Json $finalJson -Property "failure_count" -Value $mergedFailureCount',
              '  $passStatus = if ($mergedFailureCount -gt 0) { "failed" } else { "passed" }',
              '  $finalJson = Set-JsonString -Json $finalJson -Property "pass_status" -Value $passStatus',
              '  $finalJson = Set-JsonString -Json $finalJson -Property "datetime" -Value (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")',
              '  Write-Host ("Writing merged isolated summary.json. tests={0}, failures={1}." -f $mergedTestCount, $mergedFailureCount)',
              '  [System.IO.File]::WriteAllText($summaryPath, $finalJson, $utf8NoBom)',
              '  Write-Host "Merged isolated summary.json is ready."',
              '} else {',
              '  $exitCode = Invoke-PathTracerSceneList -SceneList $scenes.sceneList',
              '  if ($exitCode -ne 0) {',
              '    $summaryPath = Join-Path $publishRoot "summary.json"',
              '    if ($env:FAIL_ON_RENDER_FAILURE -eq "true" -or -not (Test-Path -LiteralPath $summaryPath)) { throw "EX40 failed with exit code $exitCode." }',
              '    Write-Warning ("EX40 exited with code {0}; continuing because FAIL_ON_RENDER_FAILURE=false and summary.json exists." -f $exitCode)',
              '  }',
              '}',
              '$logText = Get-Content -LiteralPath $log -Raw',
              'if ($logText.Contains("[ERROR]") -or $logText.Contains("Failed to Load") -or $logText.Contains("Could not create scene")) { Write-Warning "EX40 log contains render or scene errors; continuing because the report captures them." }',
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
