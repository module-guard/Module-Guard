terraform {
 required_providers {
  aws = { source = "hashicorp/aws", version = ">= 1.0" }
 }
}
provider "aws" { alias = "west" }
resource "aws_iam_role_policy" "admin" {
 provider = aws.west
 policy = "{}"
}
