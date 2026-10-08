locals {
  cloud_init = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    application_cidrs = sort(tolist(var.application_cidrs))
    management_cidrs  = sort(tolist(var.management_cidrs))
  })
}

resource "alicloud_vpc" "production" {
  vpc_name   = var.resource_name
  cidr_block = var.vpc_cidr
  tags       = var.tags
}

resource "alicloud_vswitch" "production" {
  vpc_id       = alicloud_vpc.production.id
  zone_id      = var.zone_id
  cidr_block   = var.subnet_cidr
  vswitch_name = var.resource_name
  tags         = var.tags
}

resource "alicloud_security_group" "production" {
  security_group_name = var.resource_name
  description         = "Boost Gateway private single-node production candidate"
  vpc_id              = alicloud_vpc.production.id
  inner_access_policy = "Drop"
  tags                = var.tags
}

resource "alicloud_security_group_rule" "private_ssh" {
  for_each = var.management_cidrs

  security_group_id = alicloud_security_group.production.id
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "intranet"
  policy            = "accept"
  port_range        = "22/22"
  priority          = 10
  cidr_ip           = each.value
  description       = "Private management SSH"
}

resource "alicloud_security_group_rule" "private_gateway" {
  for_each = var.application_cidrs

  security_group_id = alicloud_security_group.production.id
  type              = "ingress"
  ip_protocol       = "tcp"
  nic_type          = "intranet"
  policy            = "accept"
  port_range        = "9201/9201"
  priority          = 20
  cidr_ip           = each.value
  description       = "Private Boost Gateway application ingress"
}

resource "alicloud_instance" "production" {
  instance_name                 = var.resource_name
  host_name                     = "boost-gateway-production"
  availability_zone             = var.zone_id
  vswitch_id                    = alicloud_vswitch.production.id
  security_groups               = [alicloud_security_group.production.id]
  instance_type                 = var.instance_type
  image_id                      = var.image_id
  key_name                      = var.key_pair_name
  instance_charge_type          = "PostPaid"
  spot_strategy                 = "NoSpot"
  deletion_protection           = true
  security_enhancement_strategy = "Active"

  system_disk_category          = "cloud_essd"
  system_disk_performance_level = "PL1"
  system_disk_size              = var.system_disk_size_gib
  system_disk_encrypted         = true

  internet_charge_type       = "PayByTraffic"
  internet_max_bandwidth_out = 10
  user_data                  = local.cloud_init

  tags = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "alicloud_ecs_disk" "production_data" {
  zone_id              = var.zone_id
  disk_name            = "${var.resource_name}-data"
  description          = "Persistent Boost Gateway production data"
  category             = "cloud_essd"
  performance_level    = "PL1"
  size                 = var.data_disk_size_gib
  encrypted            = true
  delete_with_instance = false
  tags                 = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "alicloud_ecs_disk_attachment" "production_data" {
  disk_id     = alicloud_ecs_disk.production_data.id
  instance_id = alicloud_instance.production.id
}

resource "alicloud_ecs_auto_snapshot_policy" "production_data" {
  auto_snapshot_policy_name = "${var.resource_name}-daily"
  repeat_weekdays           = ["1", "2", "3", "4", "5", "6", "7"]
  time_points               = ["3"]
  retention_days            = 30
  tags                      = var.tags
}

resource "alicloud_ecs_auto_snapshot_policy_attachment" "production_data" {
  auto_snapshot_policy_id = alicloud_ecs_auto_snapshot_policy.production_data.id
  disk_id                 = alicloud_ecs_disk.production_data.id
}
