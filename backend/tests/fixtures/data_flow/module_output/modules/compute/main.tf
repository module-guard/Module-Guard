resource "aws_instance" "web" { ami = "example" }
output "instance_id" { value = aws_instance.web.id }
