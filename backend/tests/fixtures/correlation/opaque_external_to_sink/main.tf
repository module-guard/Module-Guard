module "external" { source = "example/config/aws"
 version = "1.0.0" }
resource "aws_lambda_function" "app" {
 environment { variables = module.external.environment }
}
