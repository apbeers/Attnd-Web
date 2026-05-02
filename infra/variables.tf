variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (production, staging)"
  type        = string
  default     = "production"
}

variable "app_name" {
  description = "Short application name used to prefix resource names"
  type        = string
  default     = "attnd"
}

variable "domain_name" {
  description = "Primary domain for the app (e.g. attnd.app)"
  type        = string
  default     = "attnd.app"
}

# ── Networking ────────────────────────────────────────────────────────────────

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "AZs to spread resources across"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

# ── ECS / App ─────────────────────────────────────────────────────────────────

variable "app_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)"
  type        = number
  default     = 256
}

variable "app_memory" {
  description = "Fargate task memory in MB"
  type        = number
  default     = 512
}

variable "app_desired_count" {
  description = "Number of running ECS tasks"
  type        = number
  default     = 1
}

variable "app_port" {
  description = "Port the Django/gunicorn process listens on inside the container"
  type        = number
  default     = 8000
}

# ── Database ──────────────────────────────────────────────────────────────────

variable "db_instance_class" {
  description = "RDS instance type"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_name" {
  description = "Name of the PostgreSQL database"
  type        = string
  default     = "attnd"
}

variable "db_username" {
  description = "Master DB username"
  type        = string
  default     = "attnd"
}

variable "db_allocated_storage" {
  description = "Initial storage in GB"
  type        = number
  default     = 20
}
