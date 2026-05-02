output "alb_dns_name" {
  description = "ALB DNS name — point your domain's CNAME here"
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL for pushing Docker images"
  value       = aws_ecr_repository.app.repository_url
}

output "rds_endpoint" {
  description = "RDS instance endpoint"
  value       = aws_db_instance.main.address
  sensitive   = true
}

output "media_bucket_name" {
  description = "S3 bucket name for user media uploads"
  value       = aws_s3_bucket.media.bucket
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "acm_certificate_validation_options" {
  description = "DNS records to add to validate the ACM certificate"
  value       = aws_acm_certificate.main.domain_validation_options
}
