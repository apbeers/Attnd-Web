output "alb_dns_name" {
  description = "Point your domain CNAME here"
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "rds_endpoint" {
  value     = aws_db_instance.main.address
  sensitive = true
}

output "rds_port" {
  value = aws_db_instance.main.port
}

output "media_bucket" {
  value = aws_s3_bucket.media.bucket
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service" {
  value = aws_ecs_service.app.name
}

output "app_secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "acm_validation_records" {
  description = "Add these DNS records to validate the TLS certificate"
  value       = aws_acm_certificate.main.domain_validation_options
}
