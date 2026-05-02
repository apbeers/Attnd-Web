# ── DB password ───────────────────────────────────────────────────────────────
resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${var.app_name}/db-password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = var.db_password
}

variable "db_password" {
  description = "Master database password — set via TF_VAR_db_password or tfvars (never commit)"
  type        = string
  sensitive   = true
}

# ── App secrets (Django SECRET_KEY, API keys, etc.) ──────────────────────────
# One JSON secret; the ECS task reads individual keys from it.
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.app_name}/app"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    SECRET_KEY                = var.django_secret_key
    APPLE_APP_BUNDLE_ID       = var.apple_app_bundle_id
    APPLE_TEAM_ID             = var.apple_team_id
    ANDROID_PACKAGE_NAME      = var.android_package_name
    ATTESTATION_JWT_SECRET    = var.attestation_jwt_secret
    ATTND_API_KEY             = var.attnd_api_key
    OPENAI_API_KEY            = var.openai_api_key
  })
}

variable "django_secret_key" {
  description = "Django SECRET_KEY"
  type        = string
  sensitive   = true
}

variable "apple_app_bundle_id" {
  description = "Apple app bundle ID for attestation"
  type        = string
  default     = "com.arcu.attnd-ios2"
}

variable "apple_team_id" {
  description = "Apple developer team ID"
  type        = string
  default     = "8T487X8VY7"
}

variable "android_package_name" {
  description = "Android package name"
  type        = string
  default     = "com.arcu.attnd"
}

variable "attestation_jwt_secret" {
  description = "JWT secret for attestation tokens"
  type        = string
  sensitive   = true
}

variable "attnd_api_key" {
  description = "Static API key for iOS app"
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key"
  type        = string
  sensitive   = true
  default     = ""
}
