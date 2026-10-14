module "bootstrap" { source = "git::https://github.com/example/bootstrap.git?ref=main" }
locals { script = module.bootstrap.script }
resource "aws_instance" "web" { user_data = local.script }
