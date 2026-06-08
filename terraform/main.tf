# ─────────────────────────────────────────────────────────
# TRIADA Trading System — Terraform AWS Deployment
# Region: ap-southeast-1 (Singapore)
# ─────────────────────────────────────────────────────────

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.0"
}

provider "aws" {
  region = var.aws_region
}

# ── Data Sources ──
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ── VPC (use default) ──
data "aws_vpc" "default" {
  default = true
}

# ── Security Group ──
resource "aws_security_group" "triada" {
  name        = "triada-sg"
  description = "Security Group for TRIADA Trading System"
  vpc_id      = data.aws_vpc.default.id

  # SSH (your IP only - replace with your actual IP)
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.your_ip_cidr]
    description = "SSH access"
  }

  # Grafana
  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Grafana UI"
  }

  # Prometheus (bot metrics)
  ingress {
    from_port   = 9090
    to_port     = 9090
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Bot Prometheus metrics"
  }

  # Prometheus (arb metrics)
  ingress {
    from_port   = 9091
    to_port     = 9091
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Arb Prometheus metrics"
  }

  # Prometheus UI
  ingress {
    from_port   = 9092
    to_port     = 9092
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Prometheus UI"
  }

  # Outbound
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "triada-sg"
  }
}

# ── EC2 Instance ──
resource "aws_instance" "triada" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.instance_type
  key_name      = var.key_name
  subnet_id     = tolist(data.aws_subnet.default_subnets)[0].id

  # Root volume (GP3 SSD)
  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size
    delete_on_termination = true
    encrypted             = true
    throughput            = 125
    iops                  = 3000
  }

  # ENA is required for c6i instances (enabled by default)
  ena_support = true

  # Security groups
  vpc_security_group_ids = [aws_security_group.triada.id]

  # User data for initial system tuning
  user_data = <<-EOF
              #!/bin/bash
              # Set MTU 9000 for Jumbo Frames
              ip link set dev eth0 mtu 9000
              # Kernel tuning for low latency
              sysctl -w net.core.busy_poll=50
              sysctl -w net.core.busy_read=50
              sysctl -w net.ipv4.tcp_fastopen=3
              sysctl -w net.core.rmem_max=134217728
              sysctl -w net.core.wmem_max=134217728
              # Install Docker
              apt-get update
              apt-get install -y docker.io docker-compose-v2
              systemctl enable --now docker
              EOF

  tags = {
    Name        = "triada-server"
    Environment = var.environment
    Project     = "TRIADA"
  }

  # Wait for instance to be ready
  depends_on = [aws_security_group.triada]
}

# ── Default Subnet Data ──
data "aws_subnet" "default_subnets" {
  vpc_id = data.aws_vpc.default.id
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# ── Outputs ──
output "instance_public_ip" {
  description = "Public IP of TRIADA server"
  value       = aws_instance.triada.public_ip
}

output "instance_id" {
  description = "Instance ID"
  value       = aws_instance.triada.id
}

output "ssh_command" {
  description = "SSH command to connect"
  value       = "ssh -i ${var.key_name}.pem ubuntu@${aws_instance.triada.public_ip}"
}
