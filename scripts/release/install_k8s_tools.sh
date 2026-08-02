#!/usr/bin/env bash
set -euo pipefail

KUBECTL_VERSION="${KUBECTL_VERSION:-$(curl -L -s https://dl.k8s.io/release/stable.txt)}"
ARGO_ROLLOUTS_VERSION="${ARGO_ROLLOUTS_VERSION:-v1.8.3}"
K8S_TOOLS_DIR="${K8S_TOOLS_DIR:-/usr/local/bin}"

mkdir -p "${K8S_TOOLS_DIR}"

curl -LO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
install -m 0755 kubectl "${K8S_TOOLS_DIR}/kubectl"
rm kubectl

curl -LO "https://github.com/argoproj/argo-rollouts/releases/download/${ARGO_ROLLOUTS_VERSION}/kubectl-argo-rollouts-linux-amd64"
install -m 0755 kubectl-argo-rollouts-linux-amd64 "${K8S_TOOLS_DIR}/kubectl-argo-rollouts"
rm kubectl-argo-rollouts-linux-amd64

"${K8S_TOOLS_DIR}/kubectl" version --client
"${K8S_TOOLS_DIR}/kubectl" argo rollouts version
