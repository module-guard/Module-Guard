module "policy_source" { source = "git::https://github.com/example/policy.git?ref=main" }
data "aws_ssm_parameter" "secret" { name = "/app/password" }
resource "aws_iam_role_policy" "admin" { policy = "{}" }
