variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "app_name" {
  type    = string
  default = "attnd"
}

variable "github_repo" {
  description = "GitHub repo in owner/name format (e.g. apbeers/Attnd-Web)"
  type        = string
  default     = "apbeers/Attnd-Web"
}
