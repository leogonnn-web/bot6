# ─────────────────────────────────────────────────────────
# TRIADA — Terraform Outputs (additional)
# ─────────────────────────────────────────────────────────

output "grafana_url" {
  description = "Grafana URL"
  value       = "http://${aws_instance.triada.public_ip}:3000"
}

output "prometheus_url" {
  description = "Prometheus URL"
  value       = "http://${aws_instance.triada.public_ip}:9092"
}
