terraform {
  required_version = ">= 1.5.0"

  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "~> 2.60"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.10"
    }
  }

  backend "s3" {}
}

