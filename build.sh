# build.sh
#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

export FLASK_APP="server:app"

flask db upgrade