data "aws_ssm_parameter" "secret" { name = "/app/password" }
resource "aws_instance" "web" { user_data = data.aws_ssm_parameter.secret.value }
