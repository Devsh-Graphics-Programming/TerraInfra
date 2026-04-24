packer {
  required_plugins {
    proxmox = {
      source  = "github.com/hashicorp/proxmox"
      version = ">= 1.2.3"
    }
  }
}

variable "proxmox_url" {
  type = string
}

variable "proxmox_username" {
  type = string
}

variable "proxmox_token" {
  type      = string
  sensitive = true
}

variable "proxmox_password" {
  type      = string
  default   = null
  sensitive = true
}

variable "insecure_skip_tls_verify" {
  type    = bool
  default = true
}

variable "node" {
  type = string
}

variable "template_pool" {
  type = string
}

variable "runtime_pool" {
  type = string
}

variable "storage_pool" {
  type = string
}

variable "iso_storage_pool" {
  type = string
}

variable "bridge" {
  type = string
}

variable "vlan_tag" {
  type = string
}

variable "winrm_username" {
  type = string
}

variable "winrm_password" {
  type      = string
  sensitive = true
}

variable "winrm_host" {
  type    = string
  default = null
}

variable "winrm_port" {
  type    = number
  default = 5985
}

variable "windows_iso" {
  type = string
}

variable "windows_iso_checksum" {
  type = string
}

variable "virtio_iso" {
  type = string
}

variable "virtio_iso_checksum" {
  type = string
}

variable "windows_base_template_vmid" {
  type = number
}

variable "windows_base_template_name" {
  type = string
}

variable "windows_base_image_index" {
  type    = number
  default = 2
}

variable "windows_base_memory_mb" {
  type    = number
  default = 12288
}

variable "windows_base_cores" {
  type    = number
  default = 7
}

variable "windows_base_disk_size_gb" {
  type    = string
  default = "340G"
}

variable "windows_gpu_template_vmid" {
  type = number
}

variable "windows_gpu_template_name" {
  type = string
}

variable "windows_gpu_memory_mb" {
  type    = number
  default = 12288
}

variable "windows_gpu_cores" {
  type    = number
  default = 7
}

variable "gpu_pci_host" {
  type = string
}

variable "nvidia_driver_url" {
  type      = string
  default   = ""
  sensitive = true
}

variable "nvidia_driver_args" {
  type    = string
  default = "-s"
}

variable "nvidia_driver_timeout_minutes" {
  type    = number
  default = 30
}

variable "require_nvidia_driver" {
  type    = bool
  default = false
}

variable "vc_redist_x64_url" {
  type    = string
  default = ""
}

variable "vc_redist_x64_args" {
  type    = string
  default = "/install /quiet /norestart"
}

variable "runtime_component_timeout_minutes" {
  type    = number
  default = 10
}

variable "vulkan_runtime_url" {
  type    = string
  default = ""
}

variable "vulkan_runtime_args" {
  type    = string
  default = "/S"
}
