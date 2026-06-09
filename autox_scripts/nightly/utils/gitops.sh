#!/usr/bin/env bash

DEFAULT_GITOPS_CLI_REPO_URL="https://github.com/opendatahub-io/odh-gitops.git"
DEFAULT_GITOPS_CLI_REPO_BRANCH="main"
REPO_NAME="odh-gitops"

git_clone() {
  local repo_url="${1}"
  local repo_branch="${2}"

  if [ ! -d "${REPO_NAME}" ]; then
    echo "Cloning repository ${REPO_NAME}..."
    rm -rf "${REPO_NAME}"
    git clone --depth 1 -b ${repo_branch} ${repo_url} ${REPO_NAME}
    return
  fi

  local existing_remote
  existing_remote=$(git -C "${REPO_NAME}" remote get-url origin 2>/dev/null || echo "")

  if [ "${existing_remote}" == "${repo_url}" ]; then
    echo "Repository ${REPO_NAME} already exists with correct remote, pulling latest changes"
    git -C "${REPO_NAME}" fetch origin "${repo_branch}"
    git -C "${REPO_NAME}" checkout "${repo_branch}"
    # Discard any local changes to ensure a clean state
    git -C "${REPO_NAME}" reset --hard "origin/${repo_branch}"
  else
    echo "Repository ${REPO_NAME} exists but has different remote (${existing_remote}), removing and re-cloning"
    rm -rf "${REPO_NAME}"
    git clone --depth 1 -b ${repo_branch} ${repo_url} ${REPO_NAME}
  fi
}

run_gitops_make_command() {
  local command="${1}"

  make -C ${REPO_NAME} ${command}
}

setup_authorino_tls() {
  # The prepare-authorino-tls command will patch the kustomize manifests
  # to include the necessary TLS resources for Authorino.
  K8S_CLI=oc run_gitops_make_command "prepare-authorino-tls"
  # Apply again to ensure the TLS resources are applied.
  run_gitops_make_command "apply-and-verify-dependencies"
}

apply_and_verify_dependencies() {
  run_gitops_make_command "apply-and-verify-dependencies"
  setup_authorino_tls
}

remove_all_dependencies() {
  run_gitops_make_command "remove-all-dependencies"
}

remove_monitoring_from_kustomization() {
  local kustomization_file="${REPO_NAME}/dependencies/operators/kustomization.yaml"
  local verify_dependencies_file="${REPO_NAME}/scripts/verify-dependencies.sh"

  if [ ! -f "${kustomization_file}" ]; then
    echo "Error: Kustomization file not found at ${kustomization_file}"
    return 1
  fi

  echo "Removing monitoring operators from ${kustomization_file}..."

  # Remove lines containing monitoring operators:
  # - cluster-observability-operator
  # - opentelemetry-product
  # - tempo-product
  sed -i.bak \
    -e '/cluster-observability-operator/d' \
    -e '/opentelemetry-product/d' \
    -e '/tempo-product/d' \
    "${kustomization_file}"

  # Remove backup file
  rm -f "${kustomization_file}.bak"

  if [ -f "${verify_dependencies_file}" ]; then
    echo "Removing monitoring operators from ${verify_dependencies_file}..."
    sed -i.bak \
      -e '/cluster-observability-operator]=/d' \
      -e '/opentelemetry-product]=/d' \
      -e '/tempo-product]=/d' \
      "${verify_dependencies_file}"

    # Remove backup file
    rm -f "${verify_dependencies_file}.bak"
  fi

  echo "Monitoring operators removed from kustomization"
}
