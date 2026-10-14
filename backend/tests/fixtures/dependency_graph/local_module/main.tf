module "network" {
  source = "./modules/network"
  region = "ap-northeast-2"
}
module "copy" {
  source = "./modules/network"
  region = "us-west-2"
}
output "subnet_id" {
  value = module.network.subnet_id
}
