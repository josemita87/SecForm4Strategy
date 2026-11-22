output "url" {
  description = "The site; the browser asks for the user and password (scripts/set_site_password.sh)."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "bucket" {
  description = "Bucket holding the static files and the publisher's data/*.json."
  value       = aws_s3_bucket.site.bucket
}

output "distribution_id" {
  description = "CloudFront distribution serving the site."
  value       = aws_cloudfront_distribution.site.id
}
