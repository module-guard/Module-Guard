module "policy_source" { source = "git::https://github.com/example/policy.git?ref=main" }
locals {
 first = module.policy_source.policy
 second = module.policy_source.policy
}
resource "aws_iam_role_policy" "admin" { policy = "${local.first}${local.second}" }
