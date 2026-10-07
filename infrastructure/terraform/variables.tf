variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short project name used as a resource-name prefix."
  type        = string
  default     = "healthcare-readmission"

  validation {
    condition     = can(regex("^[a-z0-9-]{3,40}$", var.project_name))
    error_message = "project_name must be 3-40 chars of lowercase letters, digits and hyphens."
  }
}

variable "environment" {
  description = "Deployment environment (staging or production)."
  type        = string
  default     = "staging"

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "image_retention_count" {
  description = "How many tagged images to keep in ECR."
  type        = number
  default     = 30
}

variable "log_retention_days" {
  description = "Days to keep S3 access logs before expiry."
  type        = number
  default     = 365
}
