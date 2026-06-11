#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
AUTOX_SCRIPTS_DIR=$(dirname "${SCRIPT_DIR}")
OPERATOR_NAMESPACE="redhat-ods-operator"
APPS_NAMESPACE="redhat-ods-applications"
OPERATOR_CSV_PREFIX="rhods-operator"
PROFILES="$SCRIPT_DIR/profiles"

function delete_stuck_olm_resources() {
  local ns="${1}"
  local operator_name="${2}"
  local installplan csv_name

  if ! oc get namespace "${ns}" >/dev/null 2>&1; then
    echo "Namespace ${ns} not found, skipping OLM cleanup"
    return 0
  fi

  echo "InstallPlans in ${ns}:"
  oc get installplan -n "${ns}" 2>/dev/null || true

  while IFS= read -r installplan; do
    [[ -z "${installplan}" ]] && continue
    echo "Deleting installplan ${installplan}"
    oc delete installplan "${installplan}" -n "${ns}" --ignore-not-found || true
  done < <(oc get installplan -n "${ns}" -o json \
    | jq -r --arg name "${operator_name}" '.items[] | select(.spec.clusterServiceVersionNames[]? | test($name)) | .metadata.name')

  echo "CSVs in ${ns}:"
  oc get csv -n "${ns}" 2>/dev/null || true

  while IFS= read -r csv_name; do
    [[ -z "${csv_name}" ]] && continue
    echo "Deleting csv ${csv_name}"
    oc delete csv "${csv_name}" -n "${ns}" --ignore-not-found || true
  done < <(oc get csv -n "${ns}" -o json \
    | jq -r --arg name "${operator_name}" '.items[] | select(.metadata.name | test($name)) | .metadata.name')
}

function usage() {
  cat <<EOF >&2
Usage: $0 -u <channel> -i <catalog_image>

Rebuild a nightly RHOAI operator installation on the current OpenShift cluster.

  -u <channel>       Operator update channel (e.g. beta)
  -i <catalog_image> CatalogSource image (e.g. quay.io/rhoai/rhoai-fbc-fragment:...@sha256:...)
EOF
  exit 1
}

UPDATE_CHANNEL=""
CATALOG_SOURCE_IMAGE=""

while getopts ":u:i:" o; do
  case "${o}" in
    u)
      UPDATE_CHANNEL=${OPTARG}
      ;;
    i)
      CATALOG_SOURCE_IMAGE=${OPTARG}
      ;;
    *)
      usage
      ;;
  esac
done

if [[ -z "${UPDATE_CHANNEL}" || -z "${CATALOG_SOURCE_IMAGE}" ]]; then
  usage
fi

echo "=== Cluster status ==="
oc get nodes

echo "=== Checking network cluster operator ==="
if oc get clusteroperator network >/dev/null 2>&1; then
  network_degraded=$(oc get clusteroperator network -o jsonpath='{.status.conditions[?(@.type=="Degraded")].status}')
  if [[ "${network_degraded}" == "True" ]]; then
    echo "network cluster operator is Degraded, removing it"
    oc delete clusteroperator network --ignore-not-found || true
  else
    echo "network cluster operator is not Degraded (status=${network_degraded:-unknown}), skipping delete"
  fi
else
  echo "network cluster operator not found, skipping delete"
fi

function copy_default_profiles() {
    local PROFILES_PATH=$1
    
    mkdir -p $PROFILES_PATH

    for profile in $(oc get hardwareprofile -n redhat-ods-applications -o jsonpath='{.items[*].metadata.name}'); do
      oc get hardwareprofile "$profile" -n redhat-ods-applications -o yaml | \
      yq 'del(
          .metadata.uid,
          .metadata.resourceVersion,
          .metadata.creationTimestamp,
          .metadata.generation,
          .metadata.annotations,
          .metadata.ownerReferences,
          .status
      )' > "$PROFILES_PATH/${profile}.yaml"
    done
}

on_success_only() {
    # CRITICAL: Capture the exit code immediately as the first line
    local exit_code=$?
    
    if [ "$exit_code" -eq 0 ]; then
        rm -rf $PROFILES
    else
        echo "❌ Script failed with exit code $exit_code. Skipping removal of $PROFILES folder."
    fi
}

trap on_success_only EXIT

echo "=== Removing Kyverno ==="
bash "${AUTOX_SCRIPTS_DIR}/utils/remove_kyverno.sh"

echo "=== Cleaning stuck OLM resources in ${OPERATOR_NAMESPACE} ==="
delete_stuck_olm_resources "${OPERATOR_NAMESPACE}" "${OPERATOR_CSV_PREFIX}"

if [ ! -e "$PROFILES" ]; then
    copy_default_profiles $PROFILES
fi

echo "=== Running operator cleanup (keeping user resources) ==="
bash "${SCRIPT_DIR}/cleanup.sh" -K -t operator

echo "=== Installing Kyverno ==="
bash "${AUTOX_SCRIPTS_DIR}/utils/install_kyverno.sh"

echo "=== Installing RHOAI operator (channel=${UPDATE_CHANNEL}) ==="
bash "${SCRIPT_DIR}/setup.sh" -t operator -u "${UPDATE_CHANNEL}" -i "${CATALOG_SOURCE_IMAGE}"

oc apply -f $PROFILES

echo "=== Verification ==="
oc get pods -n "${OPERATOR_NAMESPACE}"
oc get pods -n "${APPS_NAMESPACE}"
