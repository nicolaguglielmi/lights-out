output "workload_identity_provider" {
  value       = google_iam_workload_identity_pool_provider.github_oidc.name
  description = "Workload Identity Provider resource name for OIDC workflows"
}

output "deployer_service_account" {
  value       = google_service_account.deployer.email
  description = "Least-privilege deployer service account email"
}

output "cloud_run_url" {
  value       = google_cloud_run_v2_service.app.uri
  description = "Cloud Run service URL"
}

output "delivery_pipeline" {
  value       = google_clouddeploy_delivery_pipeline.pipeline.name
  description = "Cloud Deploy delivery pipeline name"
}
