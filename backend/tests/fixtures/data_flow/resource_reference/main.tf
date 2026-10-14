resource "aws_security_group" "web" { name = "web" }
resource "aws_instance" "web" { vpc_security_group_ids = [aws_security_group.web.id] }
