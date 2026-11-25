#!/bin/sh
# shellcheck disable=SC2034 # variables are consumed by sourcing verb scripts and plugin env.sh
# SPDX-License-Identifier: Apache-2.0
#
# cloud-deploy env.sh: locates plugins/cloud/gcp-cloud-deploy and sources its
# canonical environment validation & Cloud Run revision helpers.
set -eu

_cd_plugin_dir=""
if [ -n "${CLOUD_DEPLOY_ADAPTER_DIR:-}" ] && [ -f "$CLOUD_DEPLOY_ADAPTER_DIR/../../plugins/cloud/gcp-cloud-deploy/env.sh" ]; then
  # shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
  _cd_plugin_dir=$(CDPATH= cd -- "$CLOUD_DEPLOY_ADAPTER_DIR/../../plugins/cloud/gcp-cloud-deploy" && pwd)
elif [ -f "plugins/cloud/gcp-cloud-deploy/env.sh" ]; then
  # shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
  _cd_plugin_dir=$(CDPATH= cd -- "plugins/cloud/gcp-cloud-deploy" && pwd)
fi

if [ -z "$_cd_plugin_dir" ] || [ ! -f "$_cd_plugin_dir/env.sh" ]; then
  printf 'cloud-deploy: plugins/cloud/gcp-cloud-deploy/env.sh not found\n' >&2
  exit 1
fi

CLOUD_DEPLOY_PLUGIN_DIR=$_cd_plugin_dir
# shellcheck source=plugins/cloud/gcp-cloud-deploy/env.sh
. "$CLOUD_DEPLOY_PLUGIN_DIR/env.sh"
