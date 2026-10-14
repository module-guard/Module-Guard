variable "config" {}
module "external" {
 source = "git::https://github.com/example/module.git?ref=main"
 config = var.config
}
resource "aws_instance" "web" { subnet_id = module.external.subnet_id }
