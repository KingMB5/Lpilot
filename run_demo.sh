#!/usr/bin/env sh
# LiaraPilot - one-click DEMO launcher (Linux/macOS).
# DEMO_MODE=1 forces FakeLLM + MockDeployer: fully offline, zero external calls.
DEMO_MODE=1 python3 -m uvicorn app.main:app --host 0.0.0.0 --port 3000
