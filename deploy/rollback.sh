#!/bin/bash
# ========================================
# OpsCenter Rollback Script
# Usage: rollback.sh [backend|frontend|full]
# ========================================
set -e

TYPE="${1:-full}"
APP_DIR="/opt/opscenter"
BACKUP_DIR="${APP_DIR}/deploy/backups"

log() {
    echo "[$(date '+%H:%M:%S')] $*"
}

rollback_component() {
    local COMPONENT="$1"
    log "=== Rolling back ${COMPONENT} ==="
    
    # Find latest backup
    local LATEST_BACKUP=$(ls -t "${BACKUP_DIR}/${COMPONENT}_"*.tar.gz 2>/dev/null | head -1)
    
    if [ -z "${LATEST_BACKUP}" ]; then
        log "ERROR: No backup found for ${COMPONENT}!"
        return 1
    fi
    
    local BACKUP_FILE=$(basename "${LATEST_BACKUP}" .tar.gz)
    log "Restoring from: ${BACKUP_FILE}"
    
    # Restore files
    tar xzf "${LATEST_BACKUP}" -C "${APP_DIR}"
    
    # Restart service
    if [ "${COMPONENT}" = "backend" ]; then
        log "Restarting opscenter-backend..."
        systemctl restart opscenter-backend
        sleep 2
        
        if systemctl is-active --quiet opscenter-backend; then
            log "Backend restored successfully"
        else
            log "ERROR: Backend failed to start after rollback!"
            return 1
        fi
        
        # Quick health check
        local STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:9091/api/v2/servers 2>/dev/null || echo "000")
        log "Health check: HTTP ${STATUS}"
        
    elif [ "${COMPONENT}" = "frontend" ]; then
        log "Reloading Caddy..."
        systemctl reload caddy 2>/dev/null || systemctl restart caddy
        log "Frontend restored successfully"
    fi
    
    log "${COMPONENT} rollback complete"
}

show_backups() {
    echo "Available backups:"
    ls -lth "${BACKUP_DIR}/"*.tar.gz 2>/dev/null || echo "  (none)"
}

# Main
case "${TYPE}" in
    backend|frontend)
        rollback_component "${TYPE}"
        ;;
    full)
        rollback_component "backend"
        rollback_component "frontend"
        ;;
    list)
        show_backups
        ;;
    *)
        echo "Usage: $0 [backend|frontend|full|list]"
        exit 1
        ;;
esac

log "=== Rollback finished ==="
