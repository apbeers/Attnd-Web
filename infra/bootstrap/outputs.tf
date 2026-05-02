output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "dynamodb_table" {
  value = aws_dynamodb_table.locks.name
}

output "dev_deploy_role_arn" {
  description = "Add as GitHub secret: AWS_DEV_ROLE_ARN"
  value       = aws_iam_role.deploy_dev.arn
}

output "prod_deploy_role_arn" {
  description = "Add as GitHub secret: AWS_PROD_ROLE_ARN"
  value       = aws_iam_role.deploy_prod.arn
}
