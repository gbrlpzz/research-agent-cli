#!/bin/bash
cd "$(dirname "$0")"
source .env
python3 scripts/agent.py --interactive
