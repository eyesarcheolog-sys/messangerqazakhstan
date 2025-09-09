#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# ДОБАВЛЕНА ЭТА СТРОКА: Указываем Flask, где находится наше приложение
export FLASK_APP=server.py

flask db upgrade
