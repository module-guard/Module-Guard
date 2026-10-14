data "aws_ssm_parameter" "secret" { name = "/app/password" }
locals { script = base64encode(data.aws_ssm_parameter.secret.value) }
resource "aws_instance" "web" { user_data = local.script }
