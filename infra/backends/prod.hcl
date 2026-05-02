bucket         = "attnd-terraform-state"
key            = "prod/terraform.tfstate"
region         = "us-east-1"
dynamodb_table = "attnd-terraform-locks"
encrypt        = true
