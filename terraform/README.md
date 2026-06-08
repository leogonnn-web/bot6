# TRIADA — Terraform AWS Deployment

## Prerequisites

1. **AWS CLI installed and configured:**
   ```bash
   aws configure
   # Enter your access key, secret key, region: ap-southeast-1
   ```

2. **Terraform installed:**
   - Download from https://terraform.io/downloads
   - Or: `choco install terraform` (Windows)

3. **Create Key Pair in AWS Console:**
   - EC2 → Key Pairs → Create Key Pair
   - Name: `triada-key` (or your preferred name)
   - Type: RSA
   - Format: .pem
   - Save the .pem file securely

4. **Get your public IP:**
   ```bash
   curl ifconfig.me
   ```

## Deployment Steps

### 1. Configure variables

Copy the example and fill in your values:
```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:
```hcl
aws_region     = "ap-southeast-1"
instance_type  = "c6i.xlarge"
key_name       = "triada-key"  # Your key pair name (without .pem)
your_ip_cidr   = "YOUR.IP.ADDRESS/32"  # Your public IP
root_volume_size = 50
environment    = "production"
```

### 2. Initialize Terraform

```bash
cd terraform
terraform init
```

### 3. Review the plan

```bash
terraform plan
```

### 4. Deploy

```bash
terraform apply
# Type 'yes' when prompted
```

### 5. Get connection details

After deployment, Terraform will output:
- Instance public IP
- SSH command
- Grafana URL
- Prometheus URL

## Post-Deployment

### 1. Connect to server

```bash
ssh -i triada-key.pem ubuntu@<PUBLIC_IP>
```

### 2. Upload project code

From your local machine:
```bash
scp -i triada-key.pem -r ..\* ubuntu@<PUBLIC_IP>:/home/ubuntu/triada
```

### 3. Start containers

On the server:
```bash
cd /home/ubuntu/triada
sudo docker compose up -d --build
```

### 4. Check status

```bash
sudo docker compose ps
sudo docker compose logs -f
```

## Access Monitoring

- **Grafana:** http://<PUBLIC_IP>:3000
  - Default password: `triada2024`
- **Prometheus:** http://<PUBLIC_IP>:9092

## Cleanup

To destroy all resources:
```bash
terraform destroy
```

## Instance Types

- **c6i.xlarge** (default): 4 vCPU, 8 GB RAM, ~$0.17/hour
- **c6i.metal** (production): Bare metal, disable Hyper-Threading manually
  ```bash
  # On metal instance:
  sudo echo 0 > /sys/devices/system/cpu/smt/control
  ```

## Cost Estimate

- c6i.xlarge: ~$123/month (Singapore region)
- 50 GB GP3 storage: ~$4.50/month
- Data transfer: minimal (metrics only)
- **Total: ~$130/month**
