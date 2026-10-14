variable "input" {}
module "a" {
 source = "./a"
 input = var.input
}
output "result" { value = module.a.result }
