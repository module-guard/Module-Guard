variable "input" {}
resource "aws_instance" "web" { user_data = var.input }
output "result" { value = var.input }
