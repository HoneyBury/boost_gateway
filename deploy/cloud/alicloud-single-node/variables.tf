variable "region_id" {
  description = "Alibaba Cloud region frozen in cloud-target-contract.json."
  type        = string
}

variable "zone_id" {
  description = "Availability zone frozen in cloud-target-contract.json."
  type        = string
}

variable "instance_type" {
  description = "ECS SKU with at least 12 vCPU and 16 GB memory."
  type        = string
}

variable "image_id" {
  description = "Immutable Ubuntu 24.04 x86_64 public image ID."
  type        = string
}

variable "key_pair_name" {
  description = "Existing ECS key pair used only over the private management path."
  type        = string
}

variable "management_cidrs" {
  description = "Private CIDRs allowed to reach SSH."
  type        = set(string)

  validation {
    condition = length(var.management_cidrs) > 0 && alltrue([
      for cidr in var.management_cidrs :
      can(cidrnetmask(cidr)) && can(regex(
        "^(10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)",
        cidr,
      ))
    ])
    error_message = "management_cidrs must contain explicit private IPv4 networks."
  }
}

variable "application_cidrs" {
  description = "Private CIDRs allowed to reach gateway TCP 9201 during the pilot."
  type        = set(string)

  validation {
    condition = length(var.application_cidrs) > 0 && alltrue([
      for cidr in var.application_cidrs :
      can(cidrnetmask(cidr)) && can(regex(
        "^(10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)",
        cidr,
      ))
    ])
    error_message = "application_cidrs must contain explicit private IPv4 networks."
  }
}

variable "vpc_cidr" {
  type = string

  validation {
    condition = can(cidrnetmask(var.vpc_cidr)) && can(regex(
      "^(10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)",
      var.vpc_cidr,
    ))
    error_message = "vpc_cidr must be an RFC1918 IPv4 network."
  }
}

variable "subnet_cidr" {
  type = string

  validation {
    condition = can(cidrnetmask(var.subnet_cidr)) && can(regex(
      "^(10\\.|192\\.168\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.)",
      var.subnet_cidr,
    ))
    error_message = "subnet_cidr must be an RFC1918 IPv4 network."
  }
}

variable "system_disk_size_gib" {
  type    = number
  default = 512

  validation {
    condition     = var.system_disk_size_gib >= 512
    error_message = "The root filesystem must preserve the 512 GB operations-host floor."
  }
}

variable "data_disk_size_gib" {
  type    = number
  default = 512

  validation {
    condition     = var.data_disk_size_gib >= 512
    error_message = "The persistent data disk must be at least 512 GB."
  }
}

variable "resource_name" {
  type    = string
  default = "boost-gateway-production-candidate"
}

variable "tags" {
  type = map(string)
  default = {
    Application = "boost-gateway"
    Environment = "production-candidate"
    ManagedBy   = "terraform"
  }
}
