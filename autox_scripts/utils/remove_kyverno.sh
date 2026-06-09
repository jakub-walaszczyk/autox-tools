#!/bin/bash

set -xe

# remove leases related to kyverno
oc delete lease -n kyverno --all || true

# remove deployments related to kyverno
oc delete deployments -n kyverno --all || true

# remove jobs related to kyverno
oc delete jobs -n kyverno --all || true

# remove cron jobs related to kyverno
oc delete cronjobs -n kyverno --all || true

# remove CRD's related to kyverno
oc get crd -o name | grep 'kyverno.io' | xargs -r oc delete || true

# remove services related to kyverno
oc delete services -n kyverno --all || true

# remove service accounts related to kyverno
oc get sa -n kyverno -o name | grep 'kyverno' | xargs -r oc delete || true

# remove roles related to kyverno
oc get roles -n kyverno -o name | grep 'kyverno' | xargs -r oc delete || true

# remove cluster policy roles related to kyverno
oc get clusterroles -o name | grep 'kyverno' | xargs -r oc delete || true

# remove role bindings related to kyverno
oc get rolebinding -o name | grep 'kyverno' | xargs -r oc delete || true

# remove cluster role bindings related to kyverno
oc get clusterrolebinding -o name | grep 'kyverno' | xargs -r oc delete || true

# remove validatingwebhookconfigurations related to kyverno
oc get validatingwebhookconfigurations -o name | grep 'kyverno' | xargs -r oc delete || true

# remove mutatingwebhookconfigurations related to kyverno
oc get mutatingwebhookconfigurations -o name | grep 'kyverno' | xargs -r oc delete || true

# remove pods related to kyverno
oc delete pods -n kyverno --all --force --grace-period=0 || true

# remove kyverno namespace
echo "Removing kyverno namespace..."
oc patch namespace kyverno -p '{"spec":{"finalizers":[]}}' --type=merge 2>/dev/null || true
oc delete namespace kyverno --force --grace-period=0 --timeout=5s || true
oc get namespace kyverno -o json | jq '.spec.finalizers = []' | oc replace --raw /api/v1/namespaces/kyverno/finalize -f -
