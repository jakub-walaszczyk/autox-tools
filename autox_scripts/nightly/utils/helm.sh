#!/usr/bin/env bash

DEFAULT_HELM_RELEASE="odh"
DEFAULT_HELM_NAMESPACE="opendatahub-gitops"
DEFAULT_REPO_NAME="${REPO_NAME:-odh-gitops}"

INSTALL_WAIT_TIME="${INSTALL_WAIT_TIME:-60}"

helm_install() {
  local helm_release="${1:-${DEFAULT_HELM_RELEASE}}"
  local helm_namespace="${2:-${DEFAULT_HELM_NAMESPACE}}"
  local values_file="${3}"
  local repo_name="${4:-${DEFAULT_REPO_NAME}}"
  local skip_monitoring="${5:-false}"
  local custom_helm_values_file="${6}"
  shift 6
  local custom_set_values=("$@")

  local chart_path="${repo_name}/chart"

  if [ ! -d "${chart_path}" ]; then
    echo "Error: Helm chart not found at ${chart_path}"
    return 1
  fi

  echo "** Installing via Helm chart **"
  echo "   Release: ${helm_release}"
  echo "   Namespace: ${helm_namespace}"
  if [ -n "${values_file}" ]; then
    echo "   Default values file: ${values_file}"
  fi
  if [ -n "${custom_helm_values_file}" ]; then
    echo "   Additional custom values file: ${custom_helm_values_file}"
  fi

  local monitoring_args=""
  if [ "${skip_monitoring}" == "true" ]; then
    echo "   Monitoring: disabled"
    monitoring_args="--set services.monitoring.dsci.managementState=Removed"
    monitoring_args="${monitoring_args} --set dependencies.clusterObservability.enabled=false"
    monitoring_args="${monitoring_args} --set dependencies.opentelemetry.enabled=false"
    monitoring_args="${monitoring_args} --set dependencies.tempo.enabled=false"
  fi

  local custom_args=""
  if [ ${#custom_set_values[@]} -gt 0 ]; then
    echo "   Custom values:"
    for value in "${custom_set_values[@]}"; do
      echo "     - ${value}"
      custom_args="${custom_args} --set ${value}"
    done
  fi
  echo ""

  local custom_values_file_args=""
  if [ -n "${custom_helm_values_file}" ]; then
    custom_values_file_args="-f ${custom_helm_values_file}"
  fi

  echo "** Installation in progress, will repeat installation command 5 times with ${INSTALL_WAIT_TIME} seconds sleep between each iteration... **"
  for i in {1..5}; do
    echo "Setup iteration ${i} in progress..."
    helm upgrade --install "${helm_release}" "${chart_path}" -n "${helm_namespace}" --create-namespace -f "${values_file}" ${custom_values_file_args} ${monitoring_args} ${custom_args} > /dev/null
    sleep ${INSTALL_WAIT_TIME}
  done

  echo ""
  echo "** Setting up Authorino TLS **"
  setup_authorino_tls_helm "${repo_name}"

  echo ""
  echo "Helm installation completed successfully!"
}

setup_authorino_tls_helm() {
  local repo_name="${1:-${DEFAULT_REPO_NAME}}"

  local authorino_script="${repo_name}/scripts/prepare-authorino-tls.sh"

  if [ ! -f "${authorino_script}" ]; then
    echo "ERROR: Authorino TLS setup script not found at ${authorino_script}"
    echo "  This may indicate the odh-gitops repository is missing or has a different structure."
    exit 1
  fi

  echo "Attempting to configure Authorino TLS..."
  if K8S_CLI=oc KUSTOMIZE_MODE=false bash "${authorino_script}"; then
    echo "Authorino TLS configured successfully"
  else
    echo "ERROR: Authorino TLS setup failed"
    exit 1
  fi
}

helm_uninstall() {
  local helm_release="${1:-${DEFAULT_HELM_RELEASE}}"
  local helm_namespace="${2:-${DEFAULT_HELM_NAMESPACE}}"
  local repo_name="${3:-${DEFAULT_REPO_NAME}}"

  local uninstall_script="${repo_name}/scripts/uninstall-helm-chart.sh"

  if [ ! -f "${uninstall_script}" ]; then
    echo "ERROR: Uninstall script not found at ${uninstall_script}"
    echo "  This may indicate the odh-gitops repository is missing or has a different structure."
    exit 1
  fi

  echo "Uninstalling Helm release ${helm_release} from namespace ${helm_namespace}..."

  HELM_RELEASE="${helm_release}" HELM_NAMESPACE="${helm_namespace}" \
    bash "${uninstall_script}"

  echo "Helm uninstallation completed!"
}
