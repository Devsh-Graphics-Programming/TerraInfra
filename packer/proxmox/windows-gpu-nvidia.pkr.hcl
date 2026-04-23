source "proxmox-clone" "windows_gpu_nvidia" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_username
  token                    = var.proxmox_token
  insecure_skip_tls_verify = var.insecure_skip_tls_verify
  node                     = var.node
  pool                     = var.template_pool
  task_timeout             = "45m"

  clone_vm             = var.windows_base_template_name
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
  winrm_insecure = true
  winrm_timeout  = "2h"
}

build {
  name    = "windows-gpu-nvidia"
  sources = ["source.proxmox-clone.windows_gpu_nvidia"]
}
