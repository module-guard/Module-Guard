variable "input" {}
module "b" {
 source = "./b"
 input = var.input
}
output "result" { value = module.b.result }
