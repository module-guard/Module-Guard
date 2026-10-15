terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "5.1.0" }
    random = { source = "registry.terraform.io/hashicorp/random", version = "~> 3.0" }
    custom = { source = "community/custom", version = "= 1.2.3" }
    private = { source = "registry.example.com/hashicorp/private", version = ">= 1.0" }
    legacy = {}
  }
}
