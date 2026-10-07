output "ecr_repository_url" {
  description = "Push the API image here (docker push <url>:<tag>)."
  value       = aws_ecr_repository.api.repository_url
}

output "model_bucket_name" {
  description = "S3 bucket for model artifacts (DVC remote / MLflow artifact root)."
  value       = aws_s3_bucket.models.bucket
}

output "kms_key_arn" {
  description = "KMS key encrypting ECR images and model artifacts."
  value       = aws_kms_key.artifacts.arn
}
