#!/bin/bash
# ========================================
# OpsCenter Deploy Script
# Usage: deploy.sh [backend|frontend|full] [commit_hash]
# ========================================
set -e

TYPE="${1:-full}"
COMMIT="${2:-unknown}"
APP_DIR="/opt/opscenter"
BACKUP_DIR="${APP_DIR}/deploy/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="${TYPE}_${TIMESTAMP}_${COMMIT}"

mkdir -p "${BACKUP_DIR}"

log() {
    echo "[$(date '+%H:%M:%S')] $*"
}

deploy_backend() {
    log "=== Deploying Backend ==="
    
    # 1. Backup current version
    if [ -d "${APP_DIR}/backend/app" ]; then
        log "Backing up backend to ${BACKUP_DIR}/${BACKUP_NAME}"
        tar czf "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" \
            -C "${APP_DIR}" backend/app backend/requirements.txt 2>/dev/null || true
        echo "${COMMIT}" > "${BACKUP_DIR}/${BACKUP_NAME}.commit"
    fi
    
    # 2. Install requirements if changed
    if [ -f "${APP_DIR}/backend/requirements.txt" ]; then
        log "Checking Python dependencies..."
        cd "${APP_DIR}"
        if [ -d "venv" ]; then
            ./venv/bin/pip install -r backend/requirements.txt -q 2>/dev/null || true
        fi
    fi
    
    # 3. Restart backend service
    log "Restarting opscenter-backend service..."
    systemctl restart opscenter-backend
    
    # 4. Verify service started
    sleep 2
    if systemctl is-active --quiet opscenter-backend; then
        log "Backend service is active"
    else
        log "ERROR: Backend service failed to start!"
        systemctl status opscenter-backend --no-pager -l | tail -10
        return 1
    fi
    
    log "Backend deploy complete"
}

deploy_frontend() {
    log "=== Deploying Frontend ==="
    
    # 1. Backup current version
    if [ -f "${APP_DIR}/frontend/index.html" ]; then
        log "Backing up frontend to ${BACKUP_DIR}/${BACKUP_NAME}"
        tar czf "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" \
            -C "${APP_DIR}" frontend/ 2>/dev/null || true
        echo "${COMMIT}" > "${BACKUP_DIR}/${BACKUP_NAME}.commit"
    fi
    
    # 2. Reload Caddy
    log "Reloading Caddy..."
    systemctl reload caddy 2>/dev/null || systemctl restart caddy
    
    # 3. Verify
    if systemctl is-active --quiet caddy; then
        log "Caddy is active"
    else
        log "WARNING: Caddy reload failed"
    fi
    
    log "Frontend deploy complete"
}

rollback() {
    log "=== Rolling back ${TYPE} ==="
    local LATEST_BACKUP=$(ls -t "${BACKUP_DIR}/${TYPE}_"*.tar.gz 2>/dev/null | head -1)
    
    if [ -z "${LATEST_BACKUP}" ]; then
        log "ERROR: No backup found for ${TYPE}!"
        return 1
    fi
    
    log "Restoring from: ${LATEST_BACKUP}"
    tar xzf "${LATEST_BACKUP}" -C "${APP_DIR}"
    
    if [ "${TYPE}" = "backend" ]; then
        systemctl restart opscenter-backend
    elif [ "${TYPE}" = "frontend" ]; then
        systemctl reload caddy
    fi
    
    log "Rollback complete"
}

# Main
case "${TYPE}" in
    backend)
        deploy_backend
        ;;
    frontend)
        deploy_frontend
        ;;
    full)
        deploy_backend
        deploy_frontend
        ;;
    rollback_backend|rollback_frontend)
        rollback
        ;;
    *)
        echo "Usage: $0 [backend|frontend|full] [commit_hash]"
        exit 1
        ;;
esac

log "=== Deploy finished ==="
