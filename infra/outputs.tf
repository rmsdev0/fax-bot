output "app_url" {
  value = "https://${var.domain}"
}

output "webhook_url" {
  value = "https://${var.domain}/webhooks/telnyx"
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service" {
  value = aws_ecs_service.app.name
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "nameservers" {
  description = "Set these as the domain's nameservers at GoDaddy"
  value       = aws_route53_zone.main.name_servers
}
