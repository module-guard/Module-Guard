data "aws_secretsmanager_secret_version" "secret" { secret_id = "app" }
resource "aws_lambda_function" "app" {
 environment { variables = { PASSWORD = data.aws_secretsmanager_secret_version.secret.secret_string } }
}
