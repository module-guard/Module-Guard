module "policy_source" { source = "git::https://github.com/example/policy.git?ref=0123456789abcdef0123456789abcdef01234567" }
resource "aws_iam_role_policy" "admin" { policy = module.policy_source.policy }
