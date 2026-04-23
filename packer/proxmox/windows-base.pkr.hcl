source "proxmox-iso" "windows_base" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_username
  token                    = var.proxmox_token
  insecure_skip_tls_verify = var.insecure_skip_tls_verify
  node                     = var.node
  pool                     = var.template_pool
  task_timeout             = "45m"

  vm_id                = var.windows_base_template_vmid
  vm_name              = "windows-base-build"
  template_name        = var.windows_base_template_name
  template_description = "Windows base template for the TerraInfra runner platform."
  tags                 = "runnerctl;template;windows;base"

  os              = "win10"
  bios            = "ovmf"
  machine         = "q35"
  qemu_agent      = true
  scsi_controller = "virtio-scsi-single"
  cpu_type        = "host"
  cores           = var.windows_base_cores
  sockets         = 1
  memory          = var.windows_base_memory_mb
  onboot          = false

  boot_iso {
    type     = "scsi"
    iso_file = var.windows_iso
    unmount  = true
  }

  additional_iso_files {
    type     = "scsi"
    iso_file = var.virtio_iso
    unmount  = true
  }

  efi_config {
    efi_storage_pool  = var.storage_pool
    efi_type          = "4m"
    pre_enrolled_keys = true
  }

  tpm_config {
    tpm_storage_pool = var.storage_pool
  }

  network_adapters {
    model    = "e1000"
    bridge   = var.bridge
    vlan_tag = var.vlan_tag
    firewall = true
  }

  disks {
    type         = "ide"
    storage_pool = var.storage_pool
    disk_size    = var.windows_base_disk_size_gb
    format       = "raw"
    cache_mode   = "writeback"
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
  name    = "windows-base"
  sources = ["source.proxmox-iso.windows_base"]
}
