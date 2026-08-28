pipeline {
    agent any
    options { disableConcurrentBuilds() }
    stages {
        stage("Checkout") { steps { checkout scm } }
        stage("Backup") {
            steps {
                sh '''
                    ssh prod@10.66.66.5 '
                        set -eu
                        backup_dir=/home/prod/opscenter-deploy-backups/$(date +%Y%m%d-%H%M%S)
                        mkdir -p "$backup_dir"
                        cp -a /opt/opscenter/backend "$backup_dir/backend"
                        cp -a /opt/opscenter/frontend/v3 "$backup_dir/frontend-v3"
                    '
                '''
            }
        }
        stage("Deploy") {
            steps {
                sh '''
                    rsync -avz \
                        --exclude=.git \
                        --exclude=.venv \
                        --exclude=venv \
                        --exclude=__pycache__ \
                        --exclude=frontend/groups.json \
                        --exclude=frontend/services.json \
                        --exclude=frontend/v3 \
                        . prod@10.66.66.5:/opt/opscenter/
                    ssh prod@10.66.66.5 '
                        set -eu
                        cd /opt/opscenter/frontend-vite
                        npx --yes pnpm@9.15.4 install --frozen-lockfile
                        npx --yes pnpm@9.15.4 build
                        mkdir -p /opt/opscenter/frontend/v3
                        rsync -a --delete dist/ /opt/opscenter/frontend/v3/
                        sudo systemctl restart opscenter-backend
                        sleep 2
                        systemctl is-active opscenter-backend
                        sudo systemctl reload caddy
                    '
                '''
            }
        }
        stage("Verify") {
            steps {
                sh '''
                    for i in $(seq 1 12); do
                        health=$(curl -s -o /dev/null -w "%{http_code}" http://10.66.66.5:9091/api/v2/health-check -X POST || true)
                        plaza_count=$(curl -fsS http://10.66.66.5:9091/api/v2/services/plaza | grep -o '"key"' | wc -l | tr -d ' ' || true)
                        echo "health=$health plaza_count=$plaza_count"
                        [ "$health" = "200" ] && [ "$plaza_count" -ge "19" ] && echo HEALTH_OK && exit 0
                        sleep 5
                    done
                    exit 1
                '''
            }
        }
    }
}
