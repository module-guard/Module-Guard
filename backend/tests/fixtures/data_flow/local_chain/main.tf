variable "input" {}
locals {
 first = var.input
 second = local.first
}
resource "aws_instance" "web" { user_data = local.second }
