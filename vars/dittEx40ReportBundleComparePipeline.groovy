def call(Map args = [:]) {
  def fixedSuite = args.suite?.toString()?.trim()
  if (!(fixedSuite in ['public', 'private'])) {
    error('dittEx40ReportBundleComparePipeline requires suite public or private.')
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
  def storePrefix = null
  def publish = true
  def sourceRepository = null
  def sourceBranch = null
  def sourceSha = null
  def sourceRunId = null
  def sourceRunAttempt = null
  def sourceWorkflow = null
  def sourceUrl = null
  def baselineReportUrl = null
  def candidateReportUrl = null
  def storeHostAliases = []
  def runnerTimeoutMinutes = null
  def runnerSummary = [:]
  def compareFailureCount = null
  def compareTestCount = null
  def compareWarningCount = null
  def reportUrl = null
  def storePublishArtifacts = []
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
      runnerTimeoutMinutes = requireNumber(args.get('runnerTimeoutMinutes', '180'), 'runnerTimeoutMinutes', 10, 720)
      def rawPrefix = params.STORE_PREFIX?.trim() ?: args.defaultStorePrefix ?: defaultPrefixForSuite(suite)
      storePrefix = storeNormalizePrefix(rawPrefix, ['ditt/compare/o1experimental-vs-o3/' + suite + '/'])
      def normalizeReportUrl = { Object value, String name ->
        def text = value?.toString()?.trim()
        if (!text) {
          return null
        }
        if (!text.endsWith('/')) {
          text += '/'
        }
        def expectedPrefix = 'https://store.devsh.eu/ditt/' + suite + '/'
        if (!text.startsWith(expectedPrefix)) {
          error(name + ' must stay under ' + expectedPrefix)
        }
        if (!(text ==~ /https:\/\/store\.devsh\.eu\/ditt\/[a-z]+\/[A-Za-z0-9._\/-]*\//)) {
          error(name + ' contains unsupported characters.')
        }
        return text
      }
      baselineReportUrl = normalizeReportUrl(params.BASELINE_REPORT_URL, 'BASELINE_REPORT_URL')
      candidateReportUrl = normalizeReportUrl(params.CANDIDATE_REPORT_URL, 'CANDIDATE_REPORT_URL')
      if ((baselineReportUrl && !candidateReportUrl) || (!baselineReportUrl && candidateReportUrl)) {
        error('BASELINE_REPORT_URL and CANDIDATE_REPORT_URL must be provided together.')
      }
      storeHostAliases = []
      [baselineReportUrl, candidateReportUrl].findAll { it }.collect { new URI(it).host }.unique().each { host ->
        def addresses = java.net.InetAddress.getAllByName(host).findAll { it instanceof java.net.Inet4Address }.collect { it.hostAddress }
        if (!addresses) {
          error('Could not resolve an IPv4 address for ' + host + '.')
        }
        storeHostAliases << [host: host, address: addresses[0]]
      }
      if (storeHostAliases) {
        echo('Store host aliases: ' + storeHostAliases.collect { it.host + '=' + it.address }.join(', '))
      }
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
        currentBuild.displayName = '#' + env.BUILD_NUMBER + ' ' + suite + ' report compare ' + (sourceSha ?: sourceRunId).take(12)
        currentBuild.description = sourceUrl
        echo('Source Actions run: ' + sourceUrl)
      }
      echo('Report-bundle compare suite: ' + suite + ', store_prefix=' + storePrefix + ', publish=' + publish)
      updateBuildDescription()
    }

    timeout(time: runnerTimeoutMinutes, unit: 'MINUTES') {
      withRunner(
        labels: args.get('labels', ['windows', 'gpu', 'nvidia', 'vulkan', 'runtime-only']),
        leaseTtlMinutes: args.get('leaseTtlMinutes', 240),
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
        withFileParameter(name: 'EX40_COMPARE_PACKAGE_FILE', allowNoFile: false) {
          withFileParameter(name: 'BASELINE_REPORT_FILE', allowNoFile: true) {
            withFileParameter(name: 'CANDIDATE_REPORT_FILE', allowNoFile: true) {
              withEnv([
                'SCENE_SUITE=' + suite,
                'BASELINE_REPORT_URL=' + (baselineReportUrl ?: ''),
                'CANDIDATE_REPORT_URL=' + (candidateReportUrl ?: ''),
                'STORE_HOST_ALIASES_JSON=' + (storeHostAliases ? groovy.json.JsonOutput.toJson(storeHostAliases) : '[]')
              ]) {
              stage('Acquire compare package') {
                writeFile file: 'acquire-compare-package.ps1', text: '''
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
$zip = Join-Path $env:WORKSPACE "compare-package.zip"
$root = Join-Path $env:WORKSPACE "package-compare"
Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
Copy-Item -LiteralPath $env:EX40_COMPARE_PACKAGE_FILE -Destination $zip -Force
$packageSize = (Get-Item -LiteralPath $zip).Length
Write-Host ("Compare package size: {0} bytes" -f $packageSize)
Expand-Archive -LiteralPath $zip -DestinationPath $root -Force
$manifestFile = Get-ChildItem -LiteralPath $root -Recurse -File -Filter "EX40Runtime.json" | Sort-Object FullName | Select-Object -First 1
if (-not $manifestFile) { throw "Compare package has no EX40Runtime.json manifest." }
$manifest = Get-Content -LiteralPath $manifestFile.FullName -Raw | ConvertFrom-Json
if ($manifest.schema -ne "devsh.nabla.example-runtime.v1" -or $manifest.component -ne "EX40Runtime") { throw "Unsupported EX40 runtime manifest in compare package." }
$manifestBase = $manifestFile.DirectoryName
$exe = Get-Item -LiteralPath (Resolve-PackagePath -Base $manifestBase -Relative $manifest.executable -Name "executable")
$runtimeDir = Get-Item -LiteralPath (Resolve-PackagePath -Base $manifestBase -Relative $manifest.nabla_runtime -Name "nabla_runtime")
$dxcDir = Get-Item -LiteralPath (Resolve-PackagePath -Base $manifestBase -Relative $manifest.dxc_runtime -Name "dxc_runtime")
$reportTemplate = Resolve-PackagePath -Base $manifestBase -Relative $manifest.report_template -Name "report_template"
if (-not (Get-ChildItem -LiteralPath $runtimeDir.FullName -File -Filter "Nabla*.dll" | Select-Object -First 1)) { throw "Nabla runtime DLL was not found in compare package." }
if (-not (Get-ChildItem -LiteralPath $dxcDir.FullName -File -Filter "dxcompiler.dll" | Select-Object -First 1)) { throw "DXC runtime DLL was not found in compare package." }
if (-not (Test-Path -LiteralPath $reportTemplate)) { throw "Report template directory was not found in compare package." }
$info = [pscustomobject]@{ exe = $exe.FullName; bin = $exe.DirectoryName; runtime = $runtimeDir.FullName; dxc = $dxcDir.FullName; reportTemplate = $reportTemplate; packageSize = $packageSize; manifest = $manifestFile.FullName; buildConfig = $manifest.build_config }
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $env:WORKSPACE "package-compare.json"), ($info | ConvertTo-Json -Depth 4), $utf8NoBom)
Write-Host ("Compare executable: {0}" -f $exe.FullName)
'''
                powershell './acquire-compare-package.ps1'
              }

              stage('Extract report bundles') {
                writeFile file: 'extract-report-bundles.ps1', text: '''
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
function Expand-SafeZip {
  param([Parameter(Mandatory = $true)][string] $Source, [Parameter(Mandatory = $true)][string] $Destination)
  if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  $destFull = [System.IO.Path]::GetFullPath($Destination)
  $prefix = $destFull.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
  $zip = [System.IO.Compression.ZipFile]::OpenRead($Source)
  try {
    foreach ($entry in $zip.Entries) {
      if (-not $entry.Name) { continue }
      $target = [System.IO.Path]::GetFullPath((Join-Path $destFull $entry.FullName))
      if (-not $target.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Zip entry escapes destination: $($entry.FullName)" }
      New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($target)) -Force | Out-Null
      [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
    }
  } finally {
    $zip.Dispose()
  }
}
function Find-ReportRoot {
  param([Parameter(Mandatory = $true)][string] $Root, [Parameter(Mandatory = $true)][string] $Name)
  if ((Test-Path -LiteralPath (Join-Path $Root "summary.json")) -and (Test-Path -LiteralPath (Join-Path $Root "index.html"))) {
    return $Root
  }
  $candidates = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Filter "summary.json" | Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName "index.html") })
  if ($candidates.Count -ne 1) { throw "$Name report bundle must contain exactly one report root with summary.json and index.html." }
  return $candidates[0].DirectoryName
}
function Install-ReportBundle {
  param([Parameter(Mandatory = $true)][string] $Source, [Parameter(Mandatory = $true)][string] $Name, [Parameter(Mandatory = $true)][string] $DestinationRelative)
  $tmp = Join-Path $env:WORKSPACE ("extract-" + $Name)
  $destination = Join-Path $env:WORKSPACE $DestinationRelative
  Expand-SafeZip -Source $Source -Destination $tmp
  $reportRoot = Find-ReportRoot -Root $tmp -Name $Name
  if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
  New-Item -ItemType Directory -Path $destination -Force | Out-Null
  Copy-Item -Path (Join-Path $reportRoot "*") -Destination $destination -Recurse -Force
  $summary = Get-Content -LiteralPath (Join-Path $destination "summary.json") | ConvertFrom-Json
  if ([int]$summary.num_of_tests -lt 1) { throw "$Name report contains no tests." }
  Write-Host ("Installed {0} report: status={1}, tests={2}, failures={3}" -f $Name, $summary.pass_status, $summary.num_of_tests, $summary.failure_count)
}
function Test-SafeRelativePath {
  param([Parameter(Mandatory = $true)][string] $Relative)
  $path = $Relative.Replace([char]92, [char]47)
  if ($path.StartsWith("/") -or $path.Contains("//")) { return $false }
  $segments = $path.Split([char]47)
  if ($segments -contains ".." -or $segments -contains "") { return $false }
  return $true
}
function Install-ReportBundleFromStore {
  param([Parameter(Mandatory = $true)][string] $SourceUrl, [Parameter(Mandatory = $true)][string] $Name, [Parameter(Mandatory = $true)][string] $DestinationRelative)
  $uri = [System.Uri]$SourceUrl
  if ($uri.Scheme -ne "https" -or $uri.Host -ne "store.devsh.eu") { throw "$Name report URL must use https://store.devsh.eu." }
  if (-not $uri.AbsolutePath.StartsWith("/ditt/$env:SCENE_SUITE/")) { throw "$Name report URL does not match suite $env:SCENE_SUITE." }
  if (-not $SourceUrl.EndsWith("/")) { $SourceUrl += "/" }
  Install-StoreHostAliases
  $destination = Join-Path $env:WORKSPACE $DestinationRelative
  if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
  New-Item -ItemType Directory -Path $destination -Force | Out-Null
  $manifestPath = Join-Path $env:WORKSPACE ("store-manifest-" + $Name + ".json")
  Invoke-WebRequest -Uri ($SourceUrl + "publish-manifest.json") -OutFile $manifestPath -UseBasicParsing
  $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  if ([int]$manifest.schema -ne 1 -or -not $manifest.files) { throw "$Name store manifest is unsupported." }
  $files = @($manifest.files)
  if ($files.Count -lt 1) { throw "$Name store manifest contains no files." }
  $started = Get-Date
  foreach ($relative in $files) {
    $relativeText = [string]$relative
    if (-not (Test-SafeRelativePath -Relative $relativeText)) { throw "$Name store manifest contains an unsafe path: $relativeText" }
    $target = [System.IO.Path]::GetFullPath((Join-Path $destination $relativeText))
    $destinationFull = [System.IO.Path]::GetFullPath($destination)
    $prefix = $destinationFull.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if ($target -ne $destinationFull -and -not $target.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "$Name report path escapes destination: $relativeText" }
    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($target)) -Force | Out-Null
    Invoke-WebRequest -Uri ($SourceUrl + ($relativeText -replace "\\", "/")) -OutFile $target -UseBasicParsing
  }
  $elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds
  $summary = Get-Content -LiteralPath (Join-Path $destination "summary.json") | ConvertFrom-Json
  if ([int]$summary.num_of_tests -lt 1) { throw "$Name report contains no tests." }
  if (-not (Test-Path -LiteralPath (Join-Path $destination "index.html"))) { throw "$Name report is missing index.html." }
  Write-Host ("Installed {0} report from store: status={1}, tests={2}, failures={3}, files={4}, elapsed_ms={5}" -f $Name, $summary.pass_status, $summary.num_of_tests, $summary.failure_count, $files.Count, $elapsedMs)
}
function Install-StoreHostAliases {
  if (-not $env:STORE_HOST_ALIASES_JSON) { return }
  $aliases = @($env:STORE_HOST_ALIASES_JSON | ConvertFrom-Json)
  if ($aliases.Count -lt 1) { return }
  $hostsPath = Join-Path $env:WINDIR "System32/drivers/etc/hosts"
  $lines = @()
  if (Test-Path -LiteralPath $hostsPath) {
    $lines = @([System.IO.File]::ReadAllLines($hostsPath) | Where-Object { -not $_.EndsWith(" # devsh-ci-store-host-alias") })
  }
  foreach ($alias in $aliases) {
    $hostName = [string]$alias.host
    $address = [string]$alias.address
    if (-not ($hostName -match "^[A-Za-z0-9.-]+$")) { throw "Unsafe store host alias name: $hostName" }
    if (-not ($address -match "^[0-9]{1,3}([.][0-9]{1,3}){3}$")) { throw "Unsafe store host alias address: $address" }
    $lines += ("{0} {1} # devsh-ci-store-host-alias" -f $address, $hostName)
    Write-Host ("Store host alias: {0} -> {1}" -f $hostName, $address)
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($hostsPath, (($lines -join [Environment]::NewLine) + [Environment]::NewLine), $utf8NoBom)
  Clear-DnsClientCache -ErrorAction SilentlyContinue
}
$publishRoot = Join-Path $env:WORKSPACE "publish"
if (Test-Path -LiteralPath $publishRoot) { Remove-Item -LiteralPath $publishRoot -Recurse -Force }
New-Item -ItemType Directory -Path $publishRoot -Force | Out-Null
if ($env:BASELINE_REPORT_FILE -and (Test-Path -LiteralPath $env:BASELINE_REPORT_FILE)) {
  Install-ReportBundle -Source $env:BASELINE_REPORT_FILE -Name "baseline" -DestinationRelative "publish/release-o3"
} elseif ($env:BASELINE_REPORT_URL) {
  Install-ReportBundleFromStore -SourceUrl $env:BASELINE_REPORT_URL -Name "baseline" -DestinationRelative "publish/release-o3"
} else {
  throw "Provide BASELINE_REPORT_FILE or BASELINE_REPORT_URL."
}
if ($env:CANDIDATE_REPORT_FILE -and (Test-Path -LiteralPath $env:CANDIDATE_REPORT_FILE)) {
  Install-ReportBundle -Source $env:CANDIDATE_REPORT_FILE -Name "candidate" -DestinationRelative "publish/o1experimental"
} elseif ($env:CANDIDATE_REPORT_URL) {
  Install-ReportBundleFromStore -SourceUrl $env:CANDIDATE_REPORT_URL -Name "candidate" -DestinationRelative "publish/o1experimental"
} else {
  throw "Provide CANDIDATE_REPORT_FILE or CANDIDATE_REPORT_URL."
}
'''
                powershell './extract-report-bundles.ps1'
              }

              stage('Compare reports') {
                writeFile file: 'run-report-comparison.ps1', text: '''
$ErrorActionPreference = "Stop"
$package = Get-Content -LiteralPath (Join-Path $env:WORKSPACE "package-compare.json") | ConvertFrom-Json
$baselineReport = [System.IO.Path]::GetFullPath((Join-Path $env:WORKSPACE "publish/release-o3"))
$candidateReport = [System.IO.Path]::GetFullPath((Join-Path $env:WORKSPACE "publish/o1experimental"))
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $env:WORKSPACE "publish/o1experimental-vs-o3"))
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
Write-Host ("Running report comparison without rendering: {0}" -f ($runArgs -join " "))
$started = Get-Date
$exitCode = 0
Push-Location -LiteralPath $package.bin
try {
  & $package.exe @runArgs 2>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { Write-Host $_ }
  $exitCode = $LASTEXITCODE
} finally {
  Pop-Location
}
$elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds
Write-Host ("Report comparison finished with exit code {0} in {1} ms." -f $exitCode, $elapsedMs)
$summaryPath = Join-Path $outputRoot "summary.json"
if ($exitCode -ne 0) {
  if (-not (Test-Path -LiteralPath $summaryPath)) { throw "Report comparison failed with exit code $exitCode and did not write summary.json." }
  Write-Warning ("Report comparison exited with code {0}; continuing because summary.json exists." -f $exitCode)
}
if (-not (Test-Path -LiteralPath $summaryPath)) { throw "Report comparison did not write summary.json." }
exit 0
'''
                powershell './run-report-comparison.ps1'
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
    th { color: #b7c9b7; font-size: 13px; text-transform: uppercase; letter-spacing: 0; }
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
if (-not (Test-Path -LiteralPath $publishRoot)) { throw "Publish directory does not exist." }
function New-ZipFromDirectory {
  param([Parameter(Mandatory=$true)][string]$Source, [Parameter(Mandatory=$true)][string]$Zip)
  if (-not (Test-Path -LiteralPath $Source)) { throw "Publish source does not exist: $Source" }
  if (Test-Path -LiteralPath $Zip) { Remove-Item -LiteralPath $Zip -Force }
  $files = Get-ChildItem -LiteralPath $Source -Recurse -File
  if (-not $files) { throw "Publish source is empty: $Source" }
  $started = Get-Date
  Compress-Archive -Path (Join-Path $Source "*") -DestinationPath $Zip -Force
  $elapsedMs = [int]((Get-Date) - $started).TotalMilliseconds
  $zipSize = (Get-Item -LiteralPath $Zip).Length
  Write-Host ("Prepared {0} with {1} files, {2} bytes in {3} ms." -f (Split-Path -Leaf $Zip), @($files).Count, $zipSize, $elapsedMs)
}
$rootStage = Join-Path $env:WORKSPACE "publish-root"
if (Test-Path -LiteralPath $rootStage) { Remove-Item -LiteralPath $rootStage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $rootStage | Out-Null
foreach ($name in @("index.html", "summary.json", "release-o3-summary.json", "o1experimental-summary.json", "publishS3.py")) {
  Copy-Item -LiteralPath (Join-Path $publishRoot $name) -Destination (Join-Path $rootStage $name) -Force
}
New-ZipFromDirectory -Source $rootStage -Zip (Join-Path $env:WORKSPACE "publish-root.zip")
New-ZipFromDirectory -Source (Join-Path $publishRoot "release-o3") -Zip (Join-Path $env:WORKSPACE "publish-release-o3.zip")
New-ZipFromDirectory -Source (Join-Path $publishRoot "o1experimental") -Zip (Join-Path $env:WORKSPACE "publish-o1experimental.zip")
New-ZipFromDirectory -Source (Join-Path $publishRoot "o1experimental-vs-o3") -Zip (Join-Path $env:WORKSPACE "publish-o1experimental-vs-o3.zip")
'''
                  powershell './prepare-publish-zip.ps1'
                  storePublishArtifacts = [
                    [artifact: 'publish-root.zip', prefix: storePrefix, prune: false],
                    [artifact: 'publish-release-o3.zip', prefix: storePrefix + 'release-o3/', prune: args.get('pruneAfterPublish', true)],
                    [artifact: 'publish-o1experimental.zip', prefix: storePrefix + 'o1experimental/', prune: args.get('pruneAfterPublish', true)],
                    [artifact: 'publish-o1experimental-vs-o3.zip', prefix: storePrefix + 'o1experimental-vs-o3/', prune: args.get('pruneAfterPublish', true)]
                  ]
                } else {
                  echo 'Publishing disabled by PUBLISH=false.'
                }
              }

              stage('Artifacts') {
                archiveArtifacts artifacts: 'package-compare.json,ex40-o1experimental-vs-o3.log,publish/index.html,publish/summary.json,publish/publishS3.py,publish/release-o3-summary.json,publish/o1experimental-summary.json,publish/release-o3/summary.json,publish/o1experimental/summary.json,publish/o1experimental-vs-o3/summary.json', allowEmptyArchive: true, fingerprint: false
              }

              stage('Publish comparison') {
                if (publish) {
                  if (!storePublishArtifacts) {
                    error 'Store publish artifacts were not prepared.'
                  }
                  storePublishArtifacts.eachWithIndex { item, index ->
                    def result = storePublishReportUpload(item.prefix, item.artifact, [
                      jobs: args.get('publishJobs', 8),
                      pruneAfterPublish: item.prune,
                      artifactName: item.artifact
                    ])
                    if (index == 0) {
                      reportUrl = result.url
                    }
                  }
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
  }
}
