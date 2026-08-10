# EFS: shared /data for fax PDFs, rendered replies, gallery images.
# (The DB is RDS — sqlite doesn't survive NFS locking, so it stays off EFS.)

resource "aws_efs_file_system" "data" {
  creation_token = "${var.app_name}-data"
  encrypted      = true

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = { Name = "${var.app_name}-data" }
}

resource "aws_efs_mount_target" "data" {
  count           = 2
  file_system_id  = aws_efs_file_system.data.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "data" {
  file_system_id = aws_efs_file_system.data.id

  posix_user {
    uid = 0
    gid = 0
  }
  root_directory {
    path = "/${var.app_name}"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "755"
    }
  }
}

# --- RDS Postgres ---

resource "aws_db_subnet_group" "main" {
  name       = "${var.app_name}-db"
  subnet_ids = local.subnet_ids
}

resource "random_password" "db" {
  length  = 32
  special = false # keeps the DATABASE_URL free of URL-escaping headaches
}

locals {
  # Postgres identifiers can't contain hyphens
  db_safe_name = replace(var.app_name, "-", "_")
}

resource "aws_db_instance" "main" {
  identifier              = var.app_name
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = "db.t4g.micro"
  allocated_storage       = 20
  storage_type            = "gp3"
  db_name                 = local.db_safe_name
  username                = local.db_safe_name
  password                = random_password.db.result
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  publicly_accessible     = false
  backup_retention_period = 7
  # This DB holds phone numbers and correspondence: encrypted at rest, and
  # protected from accidental terraform/console deletion.
  storage_encrypted   = true
  deletion_protection = true
  skip_final_snapshot = true
  apply_immediately   = true
}

# The composed DATABASE_URL. random_password already lives in TF state, so
# this parameter adds no new exposure; app-level secrets stay out of state.
resource "aws_ssm_parameter" "database_url" {
  name  = "/${var.app_name}/DATABASE_URL"
  type  = "SecureString"
  value = "postgresql+psycopg://${local.db_safe_name}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${local.db_safe_name}"
}
