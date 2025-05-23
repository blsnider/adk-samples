#!/bin/bash
# Setup script for Scheels Invoice Webapp
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r agents/invoice-webapp/requirements.txt
