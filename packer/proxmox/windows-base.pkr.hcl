source "proxmox-iso" "windows_base" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_username
  token                    = var.proxmox_token
  password                 = var.proxmox_password
  insecure_skip_tls_verify = var.insecure_skip_tls_verify
  node                     = var.node
  pool                     = var.template_pool
  task_timeout             = "45m"
  boot_wait                = "1s"
  boot                     = "order=sata1;sata0;net0"
  boot_command             = ["<enter><wait><enter><wait><enter>"]

  vm_id                = var.windows_base_template_vmid
  vm_name              = "windows-base-build"
  template_name        = var.windows_base_template_name
  template_description = "Windows Server base template for the TerraInfra runner platform."
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
    type         = "sata"
    iso_file     = var.windows_iso
    iso_checksum = var.windows_iso_checksum
    unmount      = true
  }

  additional_iso_files {
    type         = "sata"
    iso_file     = var.virtio_iso
    iso_checksum = var.virtio_iso_checksum
    unmount      = true
  }

  additional_iso_files {
    type             = "ide"
    iso_storage_pool = var.iso_storage_pool
    unmount          = true
    cd_label         = "AUTOUNATTEND"
    cd_content = {
      "Autounattend.xml" = templatefile("${abspath(path.root)}/answer-files/windows-server-2025/Autounattend.pkrtpl.hcl", {
        image_index            = var.windows_base_image_index
        administrator_password = var.winrm_password
      })
      "FirstLogon.ps1" = file("${abspath(path.root)}/scripts/windows-base/first-logon.ps1")
    }
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
    type         = "sata"
    storage_pool = var.storage_pool
    disk_size    = var.windows_base_disk_size_gb
    format       = "raw"
    cache_mode   = "writeback"
  }

  vga {
    type = "std"
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
  name    = "windows-base"
  sources = ["source.proxmox-iso.windows_base"]

  provisioner "powershell" {
    elevated_user     = var.winrm_username
    elevated_password = var.winrm_password
    script            = "${abspath(path.root)}/scripts/windows-base/install-qemu-agent.ps1"
  }

  provisioner "powershell" {
    elevated_user     = var.winrm_username
    elevated_password = var.winrm_password
    environment_vars  = ["PACKER_WINRM_PASSWORD=${var.winrm_password}"]
    pause_after       = "2m"
    script            = "${abspath(path.root)}/scripts/windows-base/start-sysprep.ps1"
    valid_exit_codes  = [0, 267014, 16001]
  }
}
