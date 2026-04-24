$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$workDir = "C:\Windows\Temp\packer-runtime-components"
$workspaceDir = "C:\runner\work"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
New-Item -ItemType Directory -Force -Path $workspaceDir | Out-Null

function Get-EnvText {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        return ""
    }
    return $value.Trim()
}

function Get-EnvBool {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    $value = (Get-EnvText -Name $Name).ToLowerInvariant()
    return @("1", "true", "yes", "on") -contains $value
}

function Save-Installer {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string] $Url,

        [Parameter(Mandatory = $true)]
        [string[]] $ArtifactPatterns
    )

    if ([string]::IsNullOrWhiteSpace($Url)) {
        $artifactDir = Get-EnvText -Name "RUNTIME_ARTIFACT_DIR"
        if (-not [string]::IsNullOrWhiteSpace($artifactDir) -and (Test-Path $artifactDir)) {
            foreach ($pattern in $ArtifactPatterns) {
                $match = Get-ChildItem -Path $artifactDir -File -Filter $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($match) {
                    Write-Host "Using uploaded $Name installer artifact."
                    return $match.FullName
                }
            }
        }
        Write-Host "$Name installer is not configured. Skipping."
        return $null
    }

    $extension = [IO.Path]::GetExtension(([Uri] $Url).AbsolutePath)
    if ([string]::IsNullOrWhiteSpace($extension)) {
        $extension = ".exe"
    }
    $target = Join-Path $workDir "$Name$extension"
    Write-Host "Downloading $Name installer."
    Invoke-WebRequest -Uri $Url -OutFile $target -UseBasicParsing
    return $target
}

function Invoke-Installer {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $Arguments,

        [int] $TimeoutMinutes = 10,

        [scriptblock] $TimeoutSuccessCheck = $null
    )

    Write-Host "Installing $Name."
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru
    $timeoutMilliseconds = [Math]::Max(1, $TimeoutMinutes) * 60 * 1000
    $finished = $process.WaitForExit($timeoutMilliseconds)
    if (-not $finished) {
        if ($TimeoutSuccessCheck -and (& $TimeoutSuccessCheck)) {
            Write-Host "$Name installer timed out but runtime validation passed. Stopping installer wrapper."
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            return
        }
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "$Name installer timed out after $TimeoutMinutes minutes."
    }
    $allowedExitCodes = @(0, 3010, 1641)
    if ($allowedExitCodes -notcontains $process.ExitCode) {
        throw "$Name installer failed with exit code $($process.ExitCode)."
    }
    Write-Host "$Name installer completed with exit code $($process.ExitCode)."
}

function Find-NvidiaSmi {
    $knownPath = Join-Path $env:ProgramFiles "NVIDIA Corporation\NVSMI\nvidia-smi.exe"
    if (Test-Path $knownPath) {
        return $knownPath
    }

    $command = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    return $null
}

function Test-NvidiaDriverReady {
    $nvidiaDevices = @(Get-CimInstance Win32_PnPEntity | Where-Object {
            $_.Name -match "NVIDIA" -or $_.DeviceID -match "VEN_10DE"
        })
    if ($nvidiaDevices.Count -eq 0) {
        return $false
    }

    foreach ($device in $nvidiaDevices) {
        Write-Host "NVIDIA device probe: $($device.Name) status=$($device.Status) error=$($device.ConfigManagerErrorCode)"
        if ($device.ConfigManagerErrorCode -ne 0) {
            return $false
        }
    }

    $nvidiaSmi = Find-NvidiaSmi
    if (-not $nvidiaSmi) {
        return $false
    }

    & $nvidiaSmi | Write-Host
    return $LASTEXITCODE -eq 0
}

$requireNvidiaDriver = Get-EnvBool -Name "REQUIRE_NVIDIA_DRIVER"
$runtimeComponentTimeoutMinutes = 10
$runtimeComponentTimeoutText = Get-EnvText -Name "RUNTIME_COMPONENT_TIMEOUT_MINUTES"
if (-not [string]::IsNullOrWhiteSpace($runtimeComponentTimeoutText)) {
    $runtimeComponentTimeoutMinutes = [int] $runtimeComponentTimeoutText
}
$nvidiaDriverTimeoutMinutes = 30
$nvidiaDriverTimeoutText = Get-EnvText -Name "NVIDIA_DRIVER_TIMEOUT_MINUTES"
if (-not [string]::IsNullOrWhiteSpace($nvidiaDriverTimeoutText)) {
    $nvidiaDriverTimeoutMinutes = [int] $nvidiaDriverTimeoutText
}

$vcRedistUrl = Get-EnvText -Name "VC_REDIST_X64_URL"
$vcRedistArgs = Get-EnvText -Name "VC_REDIST_X64_ARGS"
if ([string]::IsNullOrWhiteSpace($vcRedistArgs)) {
    $vcRedistArgs = "/install /quiet /norestart"
}
$vcRedistInstaller = Save-Installer -Name "vc_redist_x64" -Url $vcRedistUrl -ArtifactPatterns @("vc_redist.x64*.exe", "*vc*redist*x64*.exe")
if ($vcRedistInstaller) {
    Invoke-Installer -Name "VC++ Redistributable x64" -Path $vcRedistInstaller -Arguments $vcRedistArgs -TimeoutMinutes $runtimeComponentTimeoutMinutes
}

$vulkanRuntimeUrl = Get-EnvText -Name "VULKAN_RUNTIME_URL"
$vulkanRuntimeArgs = Get-EnvText -Name "VULKAN_RUNTIME_ARGS"
if ([string]::IsNullOrWhiteSpace($vulkanRuntimeArgs)) {
    $vulkanRuntimeArgs = "/S"
}
$vulkanInstaller = Save-Installer -Name "vulkan_runtime" -Url $vulkanRuntimeUrl -ArtifactPatterns @("*vulkan*.exe", "*Vulkan*.exe")
if ($vulkanInstaller) {
    Invoke-Installer -Name "Vulkan Runtime" -Path $vulkanInstaller -Arguments $vulkanRuntimeArgs -TimeoutMinutes $runtimeComponentTimeoutMinutes
}

$nvidiaDriverUrl = Get-EnvText -Name "NVIDIA_DRIVER_URL"
$nvidiaDriverArgs = Get-EnvText -Name "NVIDIA_DRIVER_ARGS"
if ([string]::IsNullOrWhiteSpace($nvidiaDriverArgs)) {
    $nvidiaDriverArgs = "-s"
}
$nvidiaInstaller = Save-Installer -Name "nvidia_driver" -Url $nvidiaDriverUrl -ArtifactPatterns @("*nvidia*.exe", "*NVIDIA*.exe", "*desktop*win10*win11*.exe", "*quadro*.exe")
if ($nvidiaInstaller) {
    Invoke-Installer -Name "NVIDIA driver" -Path $nvidiaInstaller -Arguments $nvidiaDriverArgs -TimeoutMinutes $nvidiaDriverTimeoutMinutes -TimeoutSuccessCheck { Test-NvidiaDriverReady }
} elseif ($requireNvidiaDriver) {
    throw "NVIDIA driver is required but NVIDIA_DRIVER_URL is not configured."
}

$nvidiaDevices = @(Get-CimInstance Win32_PnPEntity | Where-Object {
        $_.Name -match "NVIDIA" -or $_.DeviceID -match "VEN_10DE"
    })
if ($requireNvidiaDriver -and $nvidiaDevices.Count -eq 0) {
    throw "No NVIDIA device is visible in Windows."
}
foreach ($device in $nvidiaDevices) {
    Write-Host "NVIDIA device: $($device.Name) status=$($device.Status) error=$($device.ConfigManagerErrorCode)"
    if ($requireNvidiaDriver -and $device.ConfigManagerErrorCode -ne 0) {
        throw "NVIDIA device is present but not healthy."
    }
}

$nvidiaSmi = Find-NvidiaSmi
if ($nvidiaSmi) {
    Write-Host "Running nvidia-smi validation."
    & $nvidiaSmi
    if ($LASTEXITCODE -ne 0) {
        throw "nvidia-smi failed with exit code $LASTEXITCODE."
    }
} elseif ($requireNvidiaDriver) {
    throw "nvidia-smi.exe was not found after NVIDIA driver installation."
}

Write-Host "Runtime-only component provisioning completed."
