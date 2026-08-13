variable "app_name" {
  description = "Name prefix for all resources"
  type        = string
  default     = "fax-bot"
}

variable "region" {
  description = "AWS region (match where your other projects live)"
  type        = string
}

variable "profile" {
  description = "AWS CLI profile to use (empty = default credential chain)"
  type        = string
  default     = ""
}

variable "domain" {
  description = "Fully qualified public domain for the app, e.g. fax-bot.example.com"
  type        = string
}

variable "route53_zone_name" {
  description = "Route53 hosted zone the domain lives in, e.g. example.com"
  type        = string
}

variable "telnyx_connection_id" {
  description = "Telnyx Fax application (connection) id used for POST /v2/faxes"
  type        = string
}

variable "fax_bot_number" {
  description = "Public fax number replies are sent from, E.164"
  type        = string
}

variable "test_fax_number" {
  description = "Loop-guard test number; inbound faxes addressed to it are ignored"
  type        = string
  default     = ""
}

variable "task_cpu" {
  type    = number
  default = 512
}

variable "task_memory" {
  type = number
  # Chromium rendering (worker replies, gallery seeding) inside the shared
  # task OOM-killed the web process at 1024 during launch seeding.
  default = 2048
}

locals {
  # App secrets that live in SSM under /<app>/... — created OUTSIDE Terraform
  # (scripts/put_aws_secrets.sh) so the values never touch Terraform state.
  app_secret_names = ["TELNYX_API_KEY", "TELNYX_PUBLIC_KEY", "ANTHROPIC_API_KEY", "ADMIN_TOKEN"]
  ssm_prefix       = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.app_name}"
}
