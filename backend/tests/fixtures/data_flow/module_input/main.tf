variable "bootstrap" {}
locals { encoded = base64encode(var.bootstrap) }
module "compute" {
 source = "./modules/compute"
 user_data = local.encoded
}
