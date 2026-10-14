module "policy_source" { source = "git::https://github.com/example/policy.git?ref=main" }
locals { policy = module.policy_source.policy }
resource "aws_iam_role_policy" "admin" {
 role = "example"
 policy = local.policy
}
