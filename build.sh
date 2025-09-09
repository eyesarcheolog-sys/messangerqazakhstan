#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Указываем Flask, как найти наше приложение через фабрику
export FLASK_APP="app_factory:create_app()"

flask db upgrade

