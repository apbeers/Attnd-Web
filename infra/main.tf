terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # Backend config is supplied at init time via -backend-config=backends/<env>.hcl
  # so the same code drives both dev and prod with separate state files.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = var.app_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # Every resource is named <app>-<env>-* so dev and prod are fully isolated.
  prefix = "${var.app_name}-${var.environment}"
}
