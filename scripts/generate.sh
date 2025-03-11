#!/usr/bin/env bash

PYTHON="$(command -v python)"
if [[ "${PYTHON}" == "" ]]; then
  PYTHON="$(command -v python3)"
fi

${PYTHON} -m pip install jinja2 PyYAML
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
${PYTHON} "${SCRIPT_DIR}/cli-wrapper-gen.py" "${SCRIPT_DIR}/.."
