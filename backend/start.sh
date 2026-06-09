#!/bin/zsh
source ~/.zshrc
cd "$(dirname "$0")"
export FLASK_ENV=development
python3 app.py
