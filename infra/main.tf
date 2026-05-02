terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state — create the S3 bucket + DynamoDB table manually first,
  # or comment this out for a local state during initial setup.
  backend "s3" {
    bucket         = "attnd-terraform-state"
    key            = "attnd-web/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "attnd-terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "attnd"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# Grab the current account ID and region for use in ARNs
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
