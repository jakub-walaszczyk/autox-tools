#!/bin/bash

set -xe


# Clean up temp files on exit
trap 'rm -f openshift-config.pull-secret.json.tmp openshift-config.pull-secret.json_v2.tmp openshift-config.pull-secret.json' EXIT

# Function to validate registry credentials
validate_registry_credentials() {
    local registry_auth_config="$HOME/.docker/config.json"
    local instructions="https://gitlab.com/redhat/ai/midstream-integration/devtools/-/blob/main/RHOAI-on-ROSA/install-rhoai/README-rhoai-setup.md"

    # Check if registry auth config file exists
    if [[ ! -f "$registry_auth_config" ]]; then
        echo "ERROR: Registry auth config file not found at $registry_auth_config"
        echo "Please ensure you are logged in to the required registries using 'docker login' or 'podman login'"
        exit 1
    fi

    # Check if jq is available
    if ! command -v jq &> /dev/null; then
        echo "ERROR: jq is required but not installed"
        exit 1
    fi

    # check if podman is available
    if ! command -v podman &> /dev/null; then
        echo "ERROR: podman is required but not installed"
        exit 1
    fi

        # Check if yq is available
    if ! command -v yq &> /dev/null; then
        echo "ERROR: yq is required but not installed"
        exit 1
    fi

    # Check if the config file is valid JSON
    if ! jq empty "$registry_auth_config" 2>/dev/null; then
        echo "ERROR: Invalid JSON in registry auth config file: $registry_auth_config"
        echo "Please see example at $instructions"
        exit 1
    fi

    # Check if auths section exists
    if ! jq -e '.auths' "$registry_auth_config" >/dev/null 2>&1; then
        echo "ERROR: No 'auths' section found in registry auth config file"
        echo "Please login to the required registries using 'docker login' or 'podman login'"
        echo "and reference $instructions for further instructions"
        exit 1
    fi

    # Check for quay.io credentials
    if ! jq -e '.auths."quay.io"' "$registry_auth_config" >/dev/null 2>&1; then
        echo "ERROR: quay.io credentials not found in registry auth config"
        echo "Please login to quay.io using: docker login quay.io (or podman login quay.io)"
        echo "and reference $instructions for further instructions"
        exit 1
    fi

    # Check for registry.redhat.io credentials
    if ! jq -e '.auths."registry.redhat.io"' "$registry_auth_config" >/dev/null 2>&1; then
        echo "ERROR: registry.redhat.io credentials not found in registry auth config"
        echo "Please login to registry.redhat.io using: docker login registry.redhat.io (or podman login registry.redhat.io)"
        echo "and reference $instructions for further instructions"
        exit 1
    fi

    # Validate that auth tokens are not empty
    local quay_auth=$(jq -r '.auths."quay.io".auth // empty' "$registry_auth_config")
    local rh_auth=$(jq -r '.auths."registry.redhat.io".auth // empty' "$registry_auth_config")

    if [[ -z "$quay_auth" ]]; then
        echo "ERROR: quay.io auth token is empty"
        echo "Please login to quay.io using: docker login quay.io (or podman login quay.io)"
        echo "and reference $instructions for further instructions"
        exit 1
    fi

    if [[ -z "$rh_auth" ]]; then
        echo "ERROR: registry.redhat.io auth token is empty"
        echo "Please login to registry.redhat.io using: docker login registry.redhat.io (or podman login registry.redhat.io)"
        echo "and reference $instructions for further instructions"
        exit 1
    fi

    # Verify quay.io credentials can access rhoai organization
    echo "Verifying quay.io credentials for rhoai organization..."
    
    # start podman to be able to validate credentials
    podman machine start || true
    if ! podman info &> /dev/null; then
        echo "Failed to start Podman automatically. Please run 'podman machine init' or check your installation."
        exit 1
    fi

    # Check if podman is available
    if command -v podman &> /dev/null; then
        if ! podman search --authfile "$registry_auth_config" --limit 1 quay.io/rhoai/ &>/dev/null; then
            echo "ERROR: Unable to access quay.io/rhoai organization with provided credentials"
            echo "Please ensure your quay.io credentials have access to the rhoai organization"
            echo "You can verify access at: https://quay.io/organization/rhoai"
            exit 1
        fi
        echo "Successfully verified access to quay.io/rhoai organization"
    else
        echo "WARNING: podman not found, skipping rhoai organization access verification"
        echo "Please ensure your quay.io credentials have access to the rhoai organization"
    fi
    
    echo "Registry credentials validation passed"
}

# Validate registry credentials before proceeding
validate_registry_credentials

# get the current pull secret
oc get -n openshift-config secret/pull-secret -o yaml | yq .data[.dockerconfigjson] | base64 -d | jq . > openshift-config.pull-secret.json
# add quay.io to the pull secret from docker config
#jq ".auths[\"quay.io/rhoai\"] = {\"auth\": \"$(jq -cr '.auths.["quay.io/rhoai"].auth' ~/.docker/config.json)\"}" openshift-config.pull-secret.json > openshift-config.pull-secret.json.tmp
jq ".auths[\"quay.io\"] = {\"auth\": \"$(jq -cr '.auths.["quay.io"].auth' ~/.docker/config.json)\"}" openshift-config.pull-secret.json > openshift-config.pull-secret.json.tmp
jq ".auths[\"registry.stage.redhat.io\"] = {\"auth\": \"$(jq -cr '.auths.["registry.stage.redhat.io"].auth' ~/.docker/config.json)\"}" openshift-config.pull-secret.json.tmp > openshift-config.pull-secret.json_v2.tmp
jq ".auths[\"registry.redhat.io\"] = {\"auth\": \"$(jq -cr '.auths.["registry.redhat.io"].auth' ~/.docker/config.json)\"}" openshift-config.pull-secret.json_v2.tmp > openshift-config.pull-secret.json

# Wait for nodes to be available in fresh installations
sleep 10

# if there are less then two nodes, assume it is local cluster i.e we can update the pull secret
# and the image mirrors
if [ $(oc get nodes | grep -v NAME | wc -l) -lt 2 ]; then
  oc set data secret/pull-secret -n openshift-config --from-file=.dockerconfigjson=openshift-config.pull-secret.json
  oc apply -f - <<EOF
apiVersion: operator.openshift.io/v1alpha1
kind: ImageContentSourcePolicy
metadata:
  name: quaysource
spec:
  repositoryDigestMirrors:
  - mirrors:
    - quay.io/rhoai
    source: registry.redhat.io/rhoai
EOF

else

  # create the new secret
  oc delete -n openshift-config secret/pull-secret-brew || true
  oc create secret generic pull-secret-brew -n openshift-config --from-file=.dockerconfigjson=openshift-config.pull-secret.json --type=kubernetes.io/dockerconfigjson || true
  rm openshift-config.pull-secret.json.tmp openshift-config.pull-secret.json_v2.tmp openshift-config.pull-secret.json

 echo "Installing Kyverno..."
  if oc create -f https://github.com/kyverno/kyverno/releases/download/v1.16.4/install.yaml 2>/dev/null; then
    echo "Kyverno installed successfully"
  else
    echo "Kyverno installation completed (some resources may already exist)"
  fi

  # Wait for Kyverno to be ready and webhook to be active
  echo "Waiting for Kyverno to be ready..."
  if ! oc wait --for=condition=available --timeout=300s deployment/kyverno-admission-controller -n kyverno 2>/dev/null; then
    echo "Warning: Kyverno admission controller may not be ready yet"
  fi
  if ! oc wait --for=condition=available --timeout=300s deployment/kyverno-background-controller -n kyverno 2>/dev/null; then
    echo "Warning: Kyverno background controller may not be ready yet"
  fi

  # Temporarily modify webhook failure policy to avoid circular dependency during policy creation
  echo "Temporarily modifying Kyverno webhook configuration..."
  if oc get mutatingwebhookconfiguration kyverno-policy-mutating-webhook-cfg >/dev/null 2>&1; then
    oc patch mutatingwebhookconfiguration kyverno-policy-mutating-webhook-cfg \
      --type=json -p='[{"op": "replace", "path": "/webhooks/0/failurePolicy", "value": "Ignore"}]' || true
    WEBHOOK_MODIFIED=true
  else
    echo "Warning: Kyverno webhook configuration not found, proceeding without modification"
    WEBHOOK_MODIFIED=false
  fi

  # Create Kyverno cluster role to be able to mutate:
  # secrets, service accounts and pods
  oc apply -f - <<EOF
  apiVersion: rbac.authorization.k8s.io/v1
  kind: ClusterRole
  metadata:
    name: kyverno:admission-controller:custom-secrets
    labels:
      app.kubernetes.io/part-of: kyverno
  rules:
    - apiGroups:
        - ""
      resources:
        - secrets
        - serviceaccounts
        - pods
      verbs:
        - get
        - list
        - watch
        - create
        - update
        - patch
        - delete
    - apiGroups: 
        - apps
      resources: 
        - deployments
        - replicasets
        - statefulsets
        - daemonsets
      verbs: 
        - get
        - list
        - watch
        - update
        - patch
EOF
  # link the cluster role with the Kyverno service accounts
  oc apply -f - <<EOF
  apiVersion: rbac.authorization.k8s.io/v1
  kind: ClusterRoleBinding
  metadata:
    name: kyverno:admission-controller:custom-secrets-binding
  subjects:
    - kind: ServiceAccount
      name: kyverno-admission-controller
      namespace: kyverno
    - kind: ServiceAccount
      name: kyverno-background-controller
      namespace: kyverno
  roleRef:
    kind: ClusterRole
    name: kyverno:admission-controller:custom-secrets
    apiGroup: rbac.authorization.k8s.io
EOF
  oc delete clusterpolicy sync-secrets || true
  oc delete clusterpolicy add-imagepullsecrets || true
  oc delete clusterpolicy replace-image-registry || true

  oc apply -f - <<EOF
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: sync-secrets
  annotations:
    policies.kyverno.io/title: Sync Secrets
    policies.kyverno.io/category: Sample
    policies.kyverno.io/subject: Secret
    policies.kyverno.io/minversion: 1.6.0
    policies.kyverno.io/description: >-
      Workaround https://issues.redhat.com/browse/OCPBUGS-23901,
      make the secret available in all namespaces.
      Based on https://kyverno.io/policies/other/sync-secrets/sync-secrets/.
spec:
  # Audit mode is intentional - pre-GA clusters may have images that don't match the replacement rules.
  # This logs violations without blocking deployments, allowing operations to continue on transitional clusters.
  validationFailureAction: Audit
  rules:
  - name: sync-image-pull-secret
    match:
      any:
      - resources:
          kinds:
          - Namespace
    generate:
      apiVersion: v1
      kind: Secret
      name: pull-secret-brew
      namespace: "{{request.object.metadata.name}}"
      synchronize: true
      generateExisting: true
      clone:
        namespace: openshift-config
        name: pull-secret-brew
EOF

## ------------ Assisted by a Claude code ------------ ##
# Below code till the end has been assisted by a Cloud code.
  oc apply -f - <<EOF
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: add-imagepullsecrets
  annotations:
    policies.kyverno.io/title: Add imagePullSecrets
    policies.kyverno.io/category: Sample
    policies.kyverno.io/subject: Pod
    policies.kyverno.io/minversion: 1.6.0
    policies.kyverno.io/description: >-
      Workaround https://issues.redhat.com/browse/OCPBUGS-23901,
      use the secret in all Pods. Note that we cannot use a global
      anchor because there are Pods that mix internal registries
      and quay.io.
      Based on https://kyverno.io/policies/other/add-imagepullsecrets/add-imagepullsecrets/.
spec:
  validationFailureAction: Audit
  rules:
  - name: add-imagepullsecret-containers-only
    match:
      any:
      - resources:
          kinds:
          - Pod
    preconditions:
      all:
      - key: "{{ request.object.spec.initContainers[] || \`[]\` | length(@) }}"
        operator: Equals
        value: 0
      - key: "{{ images.containers.*.registry || \`[]\` }}"
        operator: AnyIn
        value:
        - brew.registry.redhat.io
        - quay.io
        - registry.stage.redhat.io
        - registry-proxy.engineering.redhat.com
        - registry.redhat.io
    mutate:
      patchesJson6902: |-
        - path: "/spec/imagePullSecrets/-"
          op: add
          value:
            name: pull-secret-brew
        - path: "/metadata/labels/kyverno-add-imagepullsecret"
          op: add
          value: was-here
  - name: add-imagepullsecret-containers-with-init
    match:
      any:
      - resources:
          kinds:
          - Pod
    preconditions:
      all:
      - key: "{{ request.object.spec.initContainers[] || \`[]\` | length(@) }}"
        operator: GreaterThanOrEquals
        value: 1
      - key: "{{ images.containers.*.registry || \`[]\` }}"
        operator: AnyIn
        value:
        - brew.registry.redhat.io
        - quay.io
        - registry.stage.redhat.io
        - registry-proxy.engineering.redhat.com
        - registry.redhat.io
    mutate:
      patchesJson6902: |-
        - path: "/spec/imagePullSecrets/-"
          op: add
          value:
            name: pull-secret-brew
        - path: "/metadata/labels/kyverno-add-imagepullsecret"
          op: add
          value: was-here
  - name: add-imagepullsecret-initcontainers-only
    match:
      any:
      - resources:
          kinds:
          - Pod
    preconditions:
      all:
      - key: "{{ request.object.spec.initContainers[] || \`[]\` | length(@) }}"
        operator: GreaterThanOrEquals
        value: 1
      - key: "{{ images.initContainers.*.registry || \`[]\` }}"
        operator: AnyIn
        value:
        - brew.registry.redhat.io
        - quay.io
        - registry.stage.redhat.io
        - registry-proxy.engineering.redhat.com
        - registry.redhat.io
      - key: "{{ images.containers.*.registry || \`[]\` }}"
        operator: NotIn
        value:
        - brew.registry.redhat.io
        - quay.io
        - registry.stage.redhat.io
        - registry-proxy.engineering.redhat.com
        - registry.redhat.io
    mutate:
      patchesJson6902: |-
        - path: "/spec/imagePullSecrets/-"
          op: add
          value:
            name: pull-secret-brew
        - path: "/metadata/labels/kyverno-add-imagepullsecret"
          op: add
          value: was-here
EOF

  oc apply -f - <<EOF
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: replace-image-registry
  annotations:
    policies.kyverno.io/title: Replace Image Registry
    pod-policies.kyverno.io/autogen-controllers: none
    policies.kyverno.io/category: Sample
    policies.kyverno.io/severity: medium
    policies.kyverno.io/subject: Pod
    kyverno.io/kyverno-version: 1.16.4
    policies.kyverno.io/minversion: 1.6.0
    kyverno.io/kubernetes-version: "1.23"
    policies.kyverno.io/description: >-
      Force replacement of registry-proxy.engineering.redhat.com,
      registry.stage.redhat.io, and registry.redhat.io with brew.registry.redhat.io or quay.io
      Based on https://release-1-9-0.kyverno.io/policies/other/replace_image_registry/replace_image_registry/.
spec:
  background: false
  validationFailureAction: Audit
  rules:
    - name: replace-image-registry-pod-containers
      match:
        any:
        - resources:
            kinds:
            - Pod
      mutate:
        foreach:
        - list: "request.object.spec.containers"
          patchStrategicMerge:
            metadata:
              labels:
                kyverno-replace-image-registry: was-here
            spec:
              containers:
              - name: "{{ element.name }}"
                image: "{{ regex_replace_all_literal('registry\\\\.redhat\\\\.io/rhoai/', '{{element.image}}', 'quay.io/rhoai/' )}}"
    - name: replace-image-registry-rhaii-to-stage-rhaii-pod-containers
      match:
        any:
        - resources:
            kinds:
            - Pod
      mutate:
        foreach:
        - list: "request.object.spec.containers"
          patchStrategicMerge:
            metadata:
              labels:
                kyverno-replace-image-registry: was-here
            spec:
              containers:
              - name: "{{ element.name }}"
                image: "{{ regex_replace_all_literal('registry\\\\.redhat\\\\.io/rhaii/', '{{element.image}}', 'registry.stage.redhat.io/rhaii/' )}}"
    - name: replace-rhaii-ea-to-stage-containers
      match:
        any:
        - resources:
            kinds:
            - Pod
      mutate:
        foreach:
        - list: "request.object.spec.containers"
          patchStrategicMerge:
            metadata:
              labels:
                kyverno-replace-image-registry: was-here
            spec:
              containers:
              - name: "{{ element.name }}"
                image: "{{ regex_replace_all_literal('registry\\\\.redhat\\\\.io/rhaii-early-access/', '{{element.image}}', 'registry.stage.redhat.io/rhaii-early-access/' )}}"
    - name: replace-image-registry-pod-initcontainers
      match:
        any:
        - resources:
            kinds:
            - Pod
      preconditions:
        all:
        - key: "{{ request.object.spec.initContainers[] || \`[]\` | length(@) }}"
          operator: GreaterThanOrEquals
          value: 1
      mutate:
        foreach:
        - list: "request.object.spec.initContainers"
          patchStrategicMerge:
            metadata:
              labels:
                kyverno-replace-image-registry: was-here
            spec:
              initContainers:
              - name: "{{ element.name }}"
                image: "{{ regex_replace_all_literal('registry\\\\.redhat\\\\.io/rhoai/', '{{element.image}}', 'quay.io/rhoai/' )}}"
    - name: replace-image-registry-rhaii-to-stage-rhaii-pod-init-containers
      match:
        any:
        - resources:
            kinds:
            - Pod
      preconditions:
        all:
        - key: "{{ request.object.spec.initContainers[] || \`[]\` | length(@) }}"
          operator: GreaterThanOrEquals
          value: 1
      mutate:
        foreach:
        - list: "request.object.spec.initContainers"
          patchStrategicMerge:
            metadata:
              labels:
                kyverno-replace-image-registry: was-here
            spec:
              initContainers:
              - name: "{{ element.name }}"
                image: "{{ regex_replace_all_literal('registry\\\\.redhat\\\\.io/rhaii/', '{{element.image}}', 'registry.stage.redhat.io/rhaii/' )}}"
    - name: replace-rhaii-ea-to-stage-init-containers
      match:
        any:
        - resources:
            kinds:
            - Pod
      preconditions:
        all:
        - key: "{{ request.object.spec.initContainers[] || \`[]\` | length(@) }}"
          operator: GreaterThanOrEquals
          value: 1
      mutate:
        foreach:
        - list: "request.object.spec.initContainers"
          patchStrategicMerge:
            metadata:
              labels:
                kyverno-replace-image-registry: was-here
            spec:
              initContainers:
              - name: "{{ element.name }}"
                image: "{{ regex_replace_all_literal('registry\\\\.redhat\\\\.io/rhaii-early-access/', '{{element.image}}', 'registry.stage.redhat.io/rhaii-early-access/' )}}"
EOF

  oc apply -f - <<EOF
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: add-hf-token-to-predictor-pods
  annotations:
    policies.kyverno.io/title: Add HF_TOKEN to Predictor Pods
    policies.kyverno.io/category: Sample
    policies.kyverno.io/subject: Pod
    policies.kyverno.io/minversion: 1.6.0
    policies.kyverno.io/description: >-
      Add HF_TOKEN environment variable to pods containing "predictor" in their names.
spec:
  validationFailureAction: Audit
  rules:
  - name: add-hf-token-env-var-containers
    match:
      any:
      - resources:
          kinds:
          - Pod
          names:
          - "*predictor*"
    mutate:
      foreach:
      - list: "request.object.spec.containers"
        patchStrategicMerge:
          metadata:
            labels:
              kyverno-add-hf-token: was-here
          spec:
            containers:
            - name: "{{ element.name }}"
              env:
              - name: HF_TOKEN
                valueFrom:
                  secretKeyRef:
                    name: hf-token-secret
                    key: token
  - name: add-hf-token-env-var-initcontainers
    match:
      any:
      - resources:
          kinds:
          - Pod
          names:
          - "*predictor*"
    preconditions:
      all:
      - key: "{{ request.object.spec.initContainers[] || \`[]\` | length(@) }}"
        operator: GreaterThanOrEquals
        value: 1
    mutate:
      foreach:
      - list: "request.object.spec.initContainers"
        patchStrategicMerge:
          metadata:
            labels:
              kyverno-add-hf-token: was-here
          spec:
            initContainers:
            - name: "{{ element.name }}"
              env:
              - name: HF_TOKEN
                valueFrom:
                  secretKeyRef:
                    name: hf-token-secret
                    key: token
EOF

fi
