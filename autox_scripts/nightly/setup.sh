#!/usr/bin/env bash

BASE_DIR=$(dirname $(readlink -f ${BASH_SOURCE[0]} ))

set -euo pipefail

OPENSHIFT_MARKETPLACE_NAMESPACE="openshift-marketplace"
RHODS_OPERATOR_NAMESPACE=""
DEFAULT_RHODS_OPERATOR_NAMESPACE="redhat-ods-operator"
RHODS_APPS_NAMESPACE=""
DEFAULT_RHODS_APPS_NAMESPACE="redhat-ods-applications"
RHODS_MONITORING_NAMESPACE=""
DEFAULT_RHODS_MONITORING_NAMESPACE="redhat-ods-monitoring"
DEFAULT_UPDATE_CHANNEL="fast"
DEFAULT_OPERATOR_NAME="rhods-operator"
UPDATE_CHANNEL=""
OPERATOR_NAME=""
# For example DISABLE_DSC_CONFIG
CONFIG_ENV=""
# Read script arguments from the CLI
function usage() {
  echo "Usage: $0 -t <operator|addon> [-i <image>] [-n <operator_name>] [-p <operator_namespace>] [-u <channel_name>] [-e <config_env>]" 1>&2;
  exit 1
}

while getopts ":t:u:i:n:p:a:m:e:" o; do
  case "${o}" in
    t)
      INSTALLATION_TYPE=${OPTARG}
      if [[ ! ${INSTALLATION_TYPE} =~ ^(operator|addon)$ ]]; then
        usage
      fi
      ;;
    u)
      UPDATE_CHANNEL=${OPTARG}
      ;;
    i)
      CATALOG_SOURCE_IMAGE=${OPTARG}
      ;;
    n)
      OPERATOR_NAME=${OPTARG}
      ;;
    p)
      RHODS_OPERATOR_NAMESPACE=${OPTARG}
      ;;
    a)
    RHODS_APPS_NAMESPACE=${OPTARG}
    ;;
    m)
    RHODS_MONITORING_NAMESPACE=${OPTARG}
    ;;
    e)
      CONFIG_ENV=${OPTARG}
      ;;
    *)
      usage
      ;;
  esac
done
shift $((OPTIND-1))

if [ -z "${INSTALLATION_TYPE-}" ]; then
  usage
fi

if [ -z "${UPDATE_CHANNEL}" ]; then
  UPDATE_CHANNEL="${DEFAULT_UPDATE_CHANNEL}"
fi

if [ -z "${OPERATOR_NAME}" ]; then
  OPERATOR_NAME="${DEFAULT_OPERATOR_NAME}"
fi

if [ -z "${RHODS_OPERATOR_NAMESPACE}" ]; then
  RHODS_OPERATOR_NAMESPACE="${DEFAULT_RHODS_OPERATOR_NAMESPACE}"
fi

if [ -z "${RHODS_APPS_NAMESPACE}" ]; then
  RHODS_APPS_NAMESPACE="${DEFAULT_RHODS_APPS_NAMESPACE}"
fi

if [ -z "${RHODS_MONITORING_NAMESPACE}" ]; then
  RHODS_MONITORING_NAMESPACE="${DEFAULT_RHODS_MONITORING_NAMESPACE}"
fi

# Create RHOAI operator namespaces
oc create namespace ${RHODS_OPERATOR_NAMESPACE} || true
oc config set-context --current=true --namespace=${RHODS_OPERATOR_NAMESPACE}

# Create specific configuration for RHOAI addon
if [ ${INSTALLATION_TYPE} == "addon" ]; then
  # Create addon namespaces
  oc create namespace ${RHODS_APPS_NAMESPACE} || true
  oc create namespace ${RHODS_MONITORING_NAMESPACE} || true

  # Create addon fake secrets
  oc apply -f ${INSTALLATION_TYPE}/secrets -n ${RHODS_MONITORING_NAMESPACE}
  oc apply -f ${INSTALLATION_TYPE}/secrets/addon-parameters.yaml -n ${RHODS_OPERATOR_NAMESPACE}

  # Create RHOAI admin group
  oc adm groups new dedicated-admins ${USER} || true
fi

# Install RHOAI operator
CATALOG_SOURCE_IMAGE="${CATALOG_SOURCE_IMAGE/@/\\@}"
if [ ${INSTALLATION_TYPE} == "addon" ] && [ -n "${CATALOG_SOURCE_IMAGE-}" ] ; then
     perl -i -pe"s,image: .*,image: ${CATALOG_SOURCE_IMAGE},g" \
       ${INSTALLATION_TYPE}/${INSTALLATION_TYPE}/${INSTALLATION_TYPE}-catalogsource.yaml
else
     perl -i -pe "s,image: .*,image: ${CATALOG_SOURCE_IMAGE},g" \
       ${INSTALLATION_TYPE}/${INSTALLATION_TYPE}-catalogsource.yaml

fi

SUBSCRIPTION_FILE="${INSTALLATION_TYPE}/subscription.yaml"

# Initialize subscription.yaml from a template
cp "${SUBSCRIPTION_FILE}.template" "${SUBSCRIPTION_FILE}"

if [ -n "${UPDATE_CHANNEL-}" ]; then
  perl -i -pe "s,channel: .*,channel: ${UPDATE_CHANNEL},g" \
    "${SUBSCRIPTION_FILE}"
fi

if [ -n "${OPERATOR_NAME-}" ]; then
  perl -i -pe "s,<OPERATOR_NAME>,${OPERATOR_NAME},g" \
    "${SUBSCRIPTION_FILE}"
fi

if [ -n "${CONFIG_ENV-}" ]; then
  perl -i -pe "s,<SUBSCRIPTION_CONFIG>,config:\n    env:\n      - name: ${CONFIG_ENV},g" \
    "${SUBSCRIPTION_FILE}"
else
  perl -i -ne "print unless /<SUBSCRIPTION_CONFIG>/" \
    "${SUBSCRIPTION_FILE}"
fi

if [ ${INSTALLATION_TYPE} == "addon" ]; then
  perl -i -pe "s,sourceNamespace: .*,sourceNamespace: ${RHODS_OPERATOR_NAMESPACE},g" \
    "${SUBSCRIPTION_FILE}"
else
  perl -i -pe "s,sourceNamespace: .*,sourceNamespace: ${OPENSHIFT_MARKETPLACE_NAMESPACE},g" \
    "${SUBSCRIPTION_FILE}"
fi

if [ ${INSTALLATION_TYPE} == "addon" ]; then
    oc apply -f ${INSTALLATION_TYPE} -n ${RHODS_OPERATOR_NAMESPACE}
else
    oc apply -f ${INSTALLATION_TYPE}/${INSTALLATION_TYPE}-catalogsource.yaml -n ${OPENSHIFT_MARKETPLACE_NAMESPACE}
    find ${INSTALLATION_TYPE} -name '*.yaml' \
    ! -name "${INSTALLATION_TYPE}-catalogsource.yaml" \
    | xargs -I {} oc apply -f {}
fi
