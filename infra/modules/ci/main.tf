data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  subjects = {
    images = "repo:${var.github_repository}:ref:refs/heads/master"
    plan   = "repo:${var.github_repository}:pull_request"
    apply  = "repo:${var.github_repository}:environment:aws"
  }
}

data "aws_iam_policy_document" "github_assume" {
  for_each = local.subjects
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [each.value]
    }
  }
}

resource "aws_iam_role" "github" {
  for_each           = local.subjects
  name               = "${var.name}-github-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.github_assume[each.key].json
}

data "aws_iam_policy_document" "push_images" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
    ]
    resources = ["arn:aws:ecr:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:repository/${var.name}/*"]
  }
}

resource "aws_iam_role_policy" "images" {
  name   = "push-images"
  role   = aws_iam_role.github["images"].id
  policy = data.aws_iam_policy_document.push_images.json
}

resource "aws_iam_role_policy_attachment" "plan" {
  role       = aws_iam_role.github["plan"].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "state_lock" {
  statement {
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/*.tflock"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "state-lock"
  role   = aws_iam_role.github["plan"].id
  policy = data.aws_iam_policy_document.state_lock.json
}

resource "aws_iam_role_policy_attachment" "apply" {
  role       = aws_iam_role.github["apply"].name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
