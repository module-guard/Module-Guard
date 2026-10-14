variable "bootstrap" {}
locals { encoded = base64encode(var.bootstrap) }
resource "aws_instance" "web" { user_data = local.encoded }
