variable "script" {
  type = string
}

variable "cidr" {
  type = string
}

variable "public" {
  type = bool
}

variable "policy" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

# CODE_EXECUTION
resource "aws_launch_configuration" "boot" {
  image_id      = "ami-123456"
  instance_type = "t3.micro"
  user_data     = var.script
}

resource "aws_instance" "encoded" {
  ami                         = "ami-123456"
  instance_type               = "t3.micro"
  user_data_base64            = base64encode(var.script)
  associate_public_ip_address = var.public
}

resource "google_compute_instance" "vm" {
  name                    = "vm"
  machine_type            = "e2-small"
  zone                    = "us-central1-a"
  metadata_startup_script = var.script
}

resource "azurerm_linux_virtual_machine" "vm" {
  name                = "vm"
  resource_group_name = "rg"
  location            = "koreacentral"
  size                = "Standard_B1s"
  admin_username      = "adminuser"
  custom_data         = base64encode(var.script)
  admin_password      = var.db_password
}

# NETWORK
resource "aws_security_group" "web" {
  name = "web"

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.cidr]
  }
}

resource "aws_security_group_rule" "ssh" {
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  security_group_id = aws_security_group.web.id
  cidr_blocks       = [var.cidr]
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.web.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = var.cidr
}

resource "aws_db_instance" "db" {
  engine              = "postgres"
  instance_class      = "db.t3.micro"
  allocated_storage   = 20
  username            = "app"
  password            = var.db_password
  publicly_accessible = var.public
}

resource "google_compute_firewall" "web" {
  name          = "web"
  network       = "default"
  source_ranges = [var.cidr]
}

resource "azurerm_network_security_rule" "web" {
  name                        = "web"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  destination_address_prefix  = "*"
  resource_group_name         = "rg"
  network_security_group_name = "nsg"
  source_address_prefix       = var.cidr
}

# CREDENTIAL
resource "aws_rds_cluster" "db" {
  cluster_identifier = "db"
  engine             = "aurora-postgresql"
  master_username    = "app"
  master_password    = var.db_password
}

resource "aws_docdb_cluster" "db" {
  cluster_identifier = "docdb"
  master_username    = "app"
  master_password    = var.db_password
}

resource "aws_redshift_cluster" "db" {
  cluster_identifier = "redshift"
  node_type          = "dc2.large"
  master_username    = "app"
  master_password    = var.db_password
}

resource "aws_elasticache_replication_group" "cache" {
  replication_group_id = "cache"
  description          = "cache"
  auth_token           = var.db_password
}

resource "google_sql_user" "app" {
  name     = "app"
  instance = "db"
  password = var.db_password
}

# IAM_POLICY
resource "aws_iam_role" "app" {
  name               = "app"
  assume_role_policy = var.policy
}

resource "aws_iam_user_policy" "app" {
  name   = "app"
  user   = "app"
  policy = var.policy
}

resource "aws_iam_group_policy" "app" {
  name   = "app"
  group  = "app"
  policy = var.policy
}

resource "aws_s3_bucket_policy" "app" {
  bucket = "app"
  policy = var.policy
}

resource "aws_kms_key" "app" {
  description = "app"
  policy      = var.policy
}

# SECRET_STORAGE
resource "aws_secretsmanager_secret_version" "app" {
  secret_id     = "app"
  secret_string = var.db_password
}

# Not a sensitive sink.
resource "aws_s3_bucket" "logs" {
  bucket = var.policy
}
