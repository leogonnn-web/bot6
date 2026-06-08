# ─────────────────────────────────────────────────────────
# TRIADA — Terraform Variables
# ─────────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS Region"
  type        = string
  default     = "ap-southeast-1" # Singapore
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "c6i.xlarge" # 4 vCPU, 8 GB RAM, ENA support
  
  # For production: c6i.metal (bare metal, disable Hyper-Threading manually)
}

variable "key_name" {
  description = "AWS Key Pair name (without .pem extension)"
  type        = string
  # You must create this key pair in AWS console first
}

variable "your_ip_cidr" {
  description = "Your IP address in CIDR format for SSH access (e.g., 1.2.3.4/32)"
  type        = string
  # Get your IP: curl ifconfig.me
}

variable "root_volume_size" {
  description = "Root volume size in GB"
  type        = number
  default     = 50
}

variable "environment" {
  description = "Environment tag"
  type        = string
  default     = "production"
}
