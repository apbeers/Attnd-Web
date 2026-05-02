resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${var.app_name}/${var.environment}/db-password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = var.db_password
}

# One JSON secret per environment; ECS reads individual keys at task startup.
# DATABASE_URL is written by the deploy pipeline (after RDS endpoint is known).
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.app_name}/${var.environment}/app"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id

  # DATABASE_URL is intentionally left as a placeholder here.
  # The deploy pipeline overwrites this secret after terraform apply
  # with the fully-constructed DATABASE_URL (see deploy workflow).
  #
  # OPENAI_API_KEY has been removed — Bedrock uses the ECS task IAM role.
  secret_string = jsonencode({
    DATABASE_URL            = "placeholder-updated-by-pipeline"
    SECRET_KEY              = var.django_secret_key
    APPLE_APP_BUNDLE_ID     = var.apple_app_bundle_id
    APPLE_TEAM_ID           = var.apple_team_id
    ANDROID_PACKAGE_NAME    = var.android_package_name
    ATTESTATION_JWT_SECRET  = var.attestation_jwt_secret
    ATTND_API_KEY           = var.attnd_api_key
    AWS_STORAGE_BUCKET_NAME = aws_s3_bucket.media.bucket
  })

  # Allow the pipeline to update DATABASE_URL without Terraform overwriting it
  lifecycle {
    ignore_changes = [secret_string]
  }
}
