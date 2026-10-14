variable "use_private" {}
module "vpc" { source = "org/vpc/aws" }
locals {
 subnet = var.use_private ? module.vpc.private_subnets[0] : module.vpc.public_subnets[0]
 subnets = [module.vpc.private_subnets[0], module.vpc.private_subnets[1]]
}
