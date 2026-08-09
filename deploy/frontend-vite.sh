#!/bin/bash
# v3.28 F3 前端 Vite 构建部署脚本
# 用法: bash deploy/frontend-vite.sh
set -e
cd /data/services/opscenter/frontend-vite
echo "[1/3] npm install..."
npm install --no-audit --no-fund
echo "[2/3] vite build..."
npm run build
echo "[3/3] copy dist -> frontend/v3 (gray path)"
mkdir -p ../frontend/v3
cp -r dist/* ../frontend/v3/
echo "OK - /v3/ updated, verify at https://ops.salmon.xin/v3/"
