# -----------------------------------------------------------------------------
# EXAMPLE ONLY — this Terraform is never applied by CI. It is scanned by
# Checkov (security.yml) to demonstrate infrastructure-as-code security.
# -----------------------------------------------------------------------------
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Remote state example (encrypted S3 backend with native lockfile).
  # backend "s3" {
  #   bucket       = "my-tf-state-bucket"
  #   key          = "healthcare-readmission/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.aws_region

  # Credentials come from the environment / OIDC role — never hardcoded here.
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      DataClass   = "synthetic"
    }
  }
}
