terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}
provider "aws" {
  region = var.region
}
provider "aws" {
  alias  = "west"
  region = "us-west-2"
}
variable "region" {
  default = "ap-northeast-2"
}
locals {
  name = "example-${var.region}"
}
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"
  name = local.name
  providers = { aws = aws.west }
}
data "aws_ami" "ubuntu" {
  most_recent = true
  owners = ["099720109477"]
}
resource "aws_security_group" "web" {
  name = local.name
}
resource "aws_instance" "web" {
  ami = data.aws_ami.ubuntu.id
  subnet_id = module.vpc.private_subnets[0]
  provider = aws.west
  depends_on = [module.vpc, aws_security_group.web]
  tags = { literal = "var.not_a_reference", name = local.name }
  user_data = unknown_function(var.not_declared)
}
output "instance_id" {
  value = aws_instance.web.id
}
