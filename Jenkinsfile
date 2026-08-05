pipeline {
    agent any
    stages {
        stage("Checkout") { steps { checkout scm } }
        stage("Deploy") {
            steps {
                sh "rsync -avz --exclude=.git --exclude=venv --exclude=__pycache__ . prod@10.66.66.5:/opt/opscenter/ && ssh prod@10.66.66.5 \"sudo systemctl restart opscenter-backend && sleep 2 && systemctl is-active opscenter-backend\" && ssh prod@10.66.66.5 \"sudo systemctl reload caddy\""
            }
        }
        stage("Verify") {
            steps {
                sh 'for i in 1 2 3; do S=$(curl -s -o /dev/null -w "%{http_code}" http://10.66.66.5:9091/api/v2/health-check -X POST || true); echo "health=$S"; [ "$S" = "200" ] && echo HEALTH_OK && exit 0; sleep 3; done; exit 1'
            }
        }
    }
}
