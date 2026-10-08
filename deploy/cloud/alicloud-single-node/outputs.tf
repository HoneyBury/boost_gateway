output "instance_id" {
  value = alicloud_instance.production.id
}

output "private_ip" {
  value = alicloud_instance.production.private_ip
}

output "public_ip" {
  description = "Egress address only; the security group has no public ingress rule."
  value       = alicloud_instance.production.public_ip
}

output "data_disk_id" {
  value = alicloud_ecs_disk.production_data.id
}

output "security_group_id" {
  value = alicloud_security_group.production.id
}
