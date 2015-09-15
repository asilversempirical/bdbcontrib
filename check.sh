#!/bin/sh

set -Ceu

: ${PYTHON:=python}
: ${PY_TEST:=`which py.test`}

if [ ! -x "${PY_TEST}" ]; then
    printf >&2 'unable to find pytest\n'
    exit 1
fi

root=`cd -- "$(dirname -- "$0")" && pwd`

(
    set -Ceu
    cd -- "${root}"
    rm -rf build
    ./pythenv.sh "$PYTHON" setup.py build
    BAYESDB_WIZARD_MODE=1 ./pythenv.sh "$PYTHON" "$PY_TEST" "$@"
)
