source "proxmox-clone" "windows_gpu_nvidia" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_username
  token                    = var.proxmox_token
  password                 = var.proxmox_password
  insecure_skip_tls_verify = var.insecure_skip_tls_verify
  node                     = var.node
  pool                     = var.template_pool
  task_timeout             = "45m"

  clone_vm             = var.windows_base_template_name
  full_clone           = false
  vm_id                = var.windows_gpu_template_vmid
  vm_name              = "windows-gpu-nvidia-build"
  template_name        = var.windows_gpu_template_name
  template_description = "Windows GPU template for the TerraInfra runner platform."
  tags                 = "runnerctl;template;windows;gpu;nvidia"

  os              = "win10"
  bios            = "ovmf"
  machine         = "q35"
  qemu_agent      = true
  scsi_controller = "virtio-scsi-single"
  cpu_type        = "host"
  cores           = var.windows_gpu_cores
  sockets         = 1
  memory          = var.windows_gpu_memory_mb
  onboot          = false

  network_adapters {
    model    = "e1000"
    bridge   = var.bridge
    vlan_tag = var.vlan_tag
    firewall = true
  }

  pci_devices {
    host  = var.gpu_pci_host
    pcie  = true
    x_vga = true
  }

  vga {
    type = "none"
  }

  communicator   = "winrm"
  winrm_username = var.winrm_username
  winrm_password = var.winrm_password
  winrm_host     = var.winrm_host
  winrm_port     = var.winrm_port
  winrm_insecure = true
  winrm_timeout  = "2h"
}

build {
  name    = "windows-gpu-nvidia"
  sources = ["source.proxmox-clone.windows_gpu_nvidia"]

  provisioner "powershell" {
    elevated_user     = var.winrm_username
    elevated_password = var.winrm_password
    environment_vars = [
      "RUNTIME_ARTIFACT_DIR=C:\\Windows\\Temp\\packer-runtime-artifacts",
      "NVIDIA_DRIVER_URL=${var.nvidia_driver_url}",
      "NVIDIA_DRIVER_ARGS=${var.nvidia_driver_args}",
      "NVIDIA_DRIVER_TIMEOUT_MINUTES=${var.nvidia_driver_timeout_minutes}",
      "REQUIRE_NVIDIA_DRIVER=${var.require_nvidia_driver}",
      "VC_REDIST_X64_URL=${var.vc_redist_x64_url}",
      "VC_REDIST_X64_ARGS=${var.vc_redist_x64_args}",
      "JAVA_RUNTIME_URL=${var.java_runtime_url}",
      "JAVA_RUNTIME_ARGS=${var.java_runtime_args}",
      "JAVA_RUNTIME_FILE_EXTENSION=${var.java_runtime_file_extension}",
      "GIT_CLIENT_URL=${var.git_client_url}",
      "GIT_CLIENT_ARGS=${var.git_client_args}",
      "GIT_CLIENT_FILE_EXTENSION=${var.git_client_file_extension}",
      "RUNTIME_COMPONENT_TIMEOUT_MINUTES=${var.runtime_component_timeout_minutes}",
      "VULKAN_RUNTIME_URL=${var.vulkan_runtime_url}",
      "VULKAN_RUNTIME_ARGS=${var.vulkan_runtime_args}",
    ]
    script  = "${abspath(path.root)}/scripts/windows-gpu-nvidia/install-runtime-components.ps1"
    timeout = "45m"
  }

  provisioner "windows-restart" {
    restart_timeout = "20m"
  }

  provisioner "powershell" {
    elevated_user     = var.winrm_username
    elevated_password = var.winrm_password
    environment_vars  = ["PACKER_WINRM_PASSWORD=${var.winrm_password}"]
    pause_after       = "2m"
    script            = "${abspath(path.root)}/scripts/windows-base/start-sysprep.ps1"
    valid_exit_codes  = [0, 267014]
  }
}
