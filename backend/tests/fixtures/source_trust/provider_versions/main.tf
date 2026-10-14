terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
    random = { source = "hashicorp/random" }
    custom = { source = "customcorp/custom", version = "*" }
    azurerm = { source = "hashicorp/azurerm", version = ">= 1.0" }
    legacy = { version = "1.2.3" }
  }
}
