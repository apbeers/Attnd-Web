variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  description = "dev or prod"
  type        = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be 'dev' or 'prod'."
  }
}

variable "app_name" {
  type    = string
  default = "attnd"
}

variable "domain_name" {
  description = "Full domain for this environment (e.g. dev.attnd.app or attnd.app)"
  type        = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "availability_zones" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b"]
}

variable "app_port" {
  type    = number
  default = 8000
}

variable "app_cpu" {
  type    = number
  default = 256
}

variable "app_memory" {
  type    = number
  default = 512
}

variable "app_desired_count" {
  type    = number
  default = 1
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_name" {
  type    = string
  default = "attnd"
}

variable "db_username" {
  type    = string
  default = "attnd"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

# ── Secrets (set via TF_VAR_* in CI, never committed) ────────────────────────

variable "db_password" {
  type      = string
  sensitive = true
}

variable "django_secret_key" {
  type      = string
  sensitive = true
}

variable "attestation_jwt_secret" {
  type      = string
  sensitive = true
}

variable "attnd_api_key" {
  type      = string
  sensitive = true
}

variable "apple_app_bundle_id" {
  type    = string
  default = "com.arcu.attnd-ios2"
}

variable "apple_team_id" {
  type    = string
  default = "8T487X8VY7"
}

variable "android_package_name" {
  type    = string
  default = "com.arcu.attnd"
}

variable "alert_email" {
  description = "Email address to receive CloudWatch alarm notifications (leave empty to skip)"
  type        = string
  default     = ""
}
