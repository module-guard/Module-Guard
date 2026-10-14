variable "user_data" {}
resource "aws_instance" "web" { user_data = var.user_data }
