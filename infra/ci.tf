# GitHub Actions -> AWS via OIDC: CI builds the image and rolls the service.
# No long-lived AWS credentials anywhere; the role trusts only this repo.

variable "github_repo" {
  description = "GitHub repo allowed to assume the CI role (org/name)"
  type        = string
  default     = "rmsdev0/fax-bot"
}

# The account already has the GitHub OIDC provider (other projects use it).
# Reference it read-only — never manage or destroy it from this module.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "ci_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      # GitHub's immutable subject claims embed account/repo IDs in `sub`
      # (repo:owner@OWNER_ID/name@REPO_ID:ref). Accept both formats; the
      # ID-pinned form survives repo renames and can't be spoofed by
      # name-squatting a deleted repo.
      values = [
        "repo:${var.github_repo}:*",
        "repo:rmsdev0@8891128/fax-bot@1326091328:*",
      ]
    }
  }
}

resource "aws_iam_role" "ci" {
  name               = "${var.app_name}-ci"
  assume_role_policy = data.aws_iam_policy_document.ci_assume.json
}

resource "aws_iam_role_policy" "ci" {
  name = "ecr-push-ecs-deploy"
  role = aws_iam_role.ci.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = aws_ecr_repository.app.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = aws_ecs_service.app.id
      },
    ]
  })
}

output "ci_role_arn" {
  value = aws_iam_role.ci.arn
}
