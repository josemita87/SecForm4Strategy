output "role_arns" {
  description = "Role each workflow assumes: images, plan, apply."
  value       = { for key, role in aws_iam_role.github : key => role.arn }
}
