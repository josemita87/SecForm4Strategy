output "data_bucket" {
  description = "Bucket holding the Iceberg warehouse and artifacts."
  value       = aws_s3_bucket.data.bucket
}

output "warehouse" {
  description = "Iceberg warehouse location."
  value       = aws_glue_catalog_database.lakehouse.location_uri
}

output "glue_database" {
  description = "Glue database holding the Iceberg tables."
  value       = aws_glue_catalog_database.lakehouse.name
}

output "athena_workgroup" {
  description = "Athena workgroup for querying the tables."
  value       = aws_athena_workgroup.lakehouse.name
}

output "ecr_repositories" {
  description = "Repository URL per service. "
  value = {
    for service, repo in aws_ecr_repository.service : service => repo.repository_url
  }
}
