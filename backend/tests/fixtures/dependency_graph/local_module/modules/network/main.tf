variable "region" {}
module "subnet" {
  source = "./subnet"
  cidr = "10.0.0.0/24"
}
output "subnet_id" {
  value = module.subnet.id
}
