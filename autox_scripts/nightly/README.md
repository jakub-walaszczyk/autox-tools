# RHOAI Nightly Build Scripts

Automated scripts for rebuilding RHOAI (Red Hat OpenShift AI) operator installations on OpenShift clusters. The main entrypoint is `nightly_build.sh`, which orchestrates the complete rebuild process.

## Overview

The nightly build process performs the following steps:

1. **Pre-cleanup**: Removes Kyverno and stuck OLM resources
2. **Profile Backup**: Exports existing hardware profiles for restoration
3. **Cleanup**: Uninstalls the existing RHOAI operator (preserving user resources)
4. **Kyverno Installation**: Reinstalls Kyverno
5. **Operator Installation**: Deploys the RHOAI operator with the specified catalog image
6. **Profile Restoration**: Reapplies saved hardware profiles
7. **Verification**: Displays pod status in operator and applications namespaces

## Prerequisites

- OpenShift cluster access with `oc` CLI configured
- Cluster admin permissions
- Required tools: `bash`, `yq`, `jq`, `oc`

## Entrypoint: `nightly_build.sh`

### Usage

```bash
./nightly_build.sh -u <channel> -i <catalog_image>
```

### Parameters

| Parameter | Flag | Required | Description | Example |
|-----------|------|----------|-------------|---------|
| **Update Channel** | `-u` | ✅ Yes | Operator update channel to use for installation | `beta`, `fast`, `stable` |
| **Catalog Image** | `-i` | ✅ Yes | Full CatalogSource image reference with digest | `quay.io/rhoai/rhoai-fbc-fragment:v3.5@sha256:abc123...` |

### Examples

**Standard nightly build with beta channel:**
```bash
./nightly_build.sh -u beta -i quay.io/rhoai/rhoai-fbc-fragment:v3.5-EA2@sha256:1234567890abcdef
```

**Using fast channel:**
```bash
./nightly_build.sh -u fast -i quay.io/rhoai/rhoai-fbc-fragment:latest@sha256:fedcba0987654321
```

## Supporting Scripts

### `setup.sh`

Installs RHOAI operator or addon.

**Usage:**
```bash
./setup.sh -t <operator|addon> [-i <image>] [-n <operator_name>] [-p <operator_namespace>] [-u <channel_name>] [-e <config_env>]
```

**Key Parameters:**
- `-t`: Installation type (`operator` or `addon`)
- `-i`: Catalog source image
- `-u`: Update channel
- `-n`: Operator name (default: `rhods-operator`)
- `-p`: Operator namespace (default: `redhat-ods-operator`)
- `-e`: Environment config variable (e.g., `DISABLE_DSC_CONFIG`)

### `cleanup.sh`

Uninstalls RHOAI from the cluster.

**Usage:**
```bash
./cleanup.sh -t <operator|addon|helm|gitops-cli-dependencies> [-g] [-k] [-K] [-r] [-a <operators>]
```

**Key Parameters:**
- `-t`: Installation type to remove
- `-g`: Graceful uninstall (allows finalizers to run)
- `-k`: Keep CRDs
- `-K`: Keep user resources and CRDs (used by nightly_build.sh)
- `-a`: Additional operators to uninstall

## Configuration

### Default Namespaces

The scripts use these default namespaces:

```bash
OPERATOR_NAMESPACE="redhat-ods-operator"
APPS_NAMESPACE="redhat-ods-applications"
```

### Hardware Profiles

Hardware profiles are automatically:
- **Backed up** to `./profiles/` directory before cleanup
- **Restored** after operator installation
- **Cleaned** on successful completion (kept on failure for debugging)

## Workflow Details

### 1. Cluster Health Check
```bash
oc get nodes
oc get clusteroperator network
```
- Verifies cluster accessibility
- Removes degraded network cluster operator if needed

### 2. Kyverno Management
```bash
bash ../utils/remove_kyverno.sh
bash ../utils/install_kyverno.sh
```
- Removes existing Kyverno installation
- Reinstalls clean Kyverno instance

### 3. OLM Cleanup
- Deletes stuck InstallPlans matching the operator CSV prefix
- Removes existing CSVs for `rhods-operator`
- Ensures clean slate for new installation

### 4. Profile Management
```bash
oc get hardwareprofile -n redhat-ods-applications -o yaml
```
- Exports all hardware profiles with metadata stripped:
  - `uid`, `resourceVersion`, `creationTimestamp`
  - `generation`, `annotations`, `ownerReferences`
  - `status`

### 5. Operator Installation
```bash
bash ./setup.sh -t operator -u ${UPDATE_CHANNEL} -i ${CATALOG_SOURCE_IMAGE}
```
- Creates operator namespace
- Applies catalog source
- Creates subscription and operator group
- Waits for operator deployment

## Exit Behavior

The script uses a trap to handle cleanup on exit:

- **On Success** (exit code 0): Removes `./profiles/` directory
- **On Failure** (non-zero exit): Keeps `./profiles/` for debugging

## Error Handling

The script uses `set -euo pipefail` for strict error handling:
- `-e`: Exit on any command failure
- `-u`: Error on undefined variables
- `-o pipefail`: Fail on pipe errors

## Troubleshooting

### Profiles Not Restored

If hardware profiles aren't restored, check:
```bash
ls -la ./profiles/
oc get hardwareprofile -n redhat-ods-applications
```

### Installation Stuck

Check operator logs:
```bash
oc get pods -n redhat-ods-operator
oc logs -n redhat-ods-operator <operator-pod-name>
```

### OLM Resources Not Cleaned

Manually verify:
```bash
oc get installplan -n redhat-ods-operator
oc get csv -n redhat-ods-operator
```

### Network Operator Issues

If the network cluster operator is degraded:
```bash
oc get clusteroperator network -o yaml
```
The script automatically attempts to delete degraded network operators.

## Directory Structure

```
nightly/
├── README.md                 # This file
├── nightly_build.sh          # Main entrypoint
├── setup.sh                  # Installation script
├── cleanup.sh                # Cleanup script
├── operator/                 # Operator manifests
│   ├── kustomization.yaml
│   ├── operatorgroup.yaml
│   ├── subscription.yaml.template
│   └── operator-catalogsource.yaml
├── addon/                    # Addon manifests
└── profiles/                 # Temporary profile backup (auto-created)
```

## CI/CD Integration

This script is designed for automated nightly builds. Example Jenkins/GitHub Actions usage:

```bash
#!/bin/bash
set -e

# Authenticate to cluster
oc login --token=${OCP_TOKEN} --server=${OCP_SERVER}

# Run nightly build
cd autox_scripts/nightly
./nightly_build.sh \
  -u beta \
  -i "${CATALOG_IMAGE_DIGEST}"

# Verify installation
oc wait --for=condition=ready pod -l app=rhods-operator -n redhat-ods-operator --timeout=600s
```

## License

See repository root for license information.
