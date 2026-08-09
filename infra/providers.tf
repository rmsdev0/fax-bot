terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  # Local state for now (gitignored). Move to your org's S3 state bucket when
  # this stops being a funsize project: add a backend "s3" block here.
}

provider "aws" {
  region  = var.region
  profile = var.profile != "" ? var.profile : null
}

data "aws_caller_identity" "current" {}
