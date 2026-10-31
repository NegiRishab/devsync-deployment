pipeline {
    agent { label 'devsync-ansible' }
    options {
        disableConcurrentBuilds()
        timeout(time: 40, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
        skipDefaultCheckout(true)
    }
    parameters {
        choice(name: 'ACTION', choices: ['deploy', 'setup'], description: 'Run setup once before the first deployment.')
        choice(name: 'SERVICE', choices: ['all', 'backend', 'chat', 'frontend'], description: 'First deployment must use all.')
        string(name: 'RUN_ID', defaultValue: '', description: 'Optional GitHub run ID for a single service. Empty selects its latest successful main push.')
        booleanParam(name: 'AUTO_DEPLOY', defaultValue: false, description: 'Poll every five minutes. Enable with ACTION=deploy, SERVICE=all, RUN_ID empty.')
    }
    triggers {
        cron(params.AUTO_DEPLOY && params.ACTION == 'deploy' ? 'H/5 * * * *' : '')
    }
    stages {
        stage('Checkout deployment code') {
            steps { checkout scm }
        }
        stage('Check tools and parameters') {
            steps {
                sh 'python3 --version && ansible-playbook --version && ssh -V'
                script {
                    if (params.AUTO_DEPLOY && (params.ACTION != 'deploy' || params.SERVICE != 'all' || params.RUN_ID.trim())) {
                        error('Automatic deployment requires deploy, all services, and an empty RUN_ID.')
                    }
                }
            }
        }
        stage('Read deployed release') {
            when { expression { params.ACTION == 'deploy' } }
            steps {
                withCredentials([
                    sshUserPrivateKey(credentialsId: 'app-server-ssh', keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER'),
                    file(credentialsId: 'devsync-inventory', variable: 'INVENTORY'),
                    file(credentialsId: 'devsync-known-hosts', variable: 'KNOWN_HOSTS')
                ]) {
                    sh '''
                        set -eu
                        rm -rf .local
                        mkdir -p .local
                        export ANSIBLE_PRIVATE_KEY_FILE="$SSH_KEY"
                        export ANSIBLE_REMOTE_USER="$SSH_USER"
                        export ANSIBLE_SSH_COMMON_ARGS="-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KNOWN_HOSTS"
                        ansible-playbook -i "$INVENTORY" ansible/read-state.yml
                    '''
                }
            }
        }
        stage('Resolve successful CI release') {
            when { expression { params.ACTION == 'deploy' } }
            steps {
                withCredentials([string(credentialsId: 'github-token', variable: 'GITHUB_TOKEN')]) {
                    sh '''
                        set -eu
                        mkdir -p .local
                        python3 scripts/fetch_release.py --service "$SERVICE" --run-id "$RUN_ID" --current .local/current.json --output .local/candidate.json
                    '''
                }
            }
        }
        stage('Run Ansible over SSH') {
            steps {
                withCredentials([
                    sshUserPrivateKey(credentialsId: 'app-server-ssh', keyFileVariable: 'SSH_KEY', usernameVariable: 'SSH_USER'),
                    file(credentialsId: 'devsync-inventory', variable: 'INVENTORY'),
                    file(credentialsId: 'devsync-known-hosts', variable: 'KNOWN_HOSTS')
                ]) {
                    script {
                        if (params.ACTION == 'setup') {
                            withCredentials([file(credentialsId: 'devsync-runtime-env', variable: 'RUNTIME_ENV_FILE')]) {
                                sh '''
                                    set -eu
                                    export ANSIBLE_PRIVATE_KEY_FILE="$SSH_KEY"
                                    export ANSIBLE_REMOTE_USER="$SSH_USER"
                                    export ANSIBLE_SSH_COMMON_ARGS="-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KNOWN_HOSTS"
                                    ansible-playbook -i "$INVENTORY" ansible/setup.yml
                                '''
                            }
                        } else {
                            withCredentials([usernamePassword(credentialsId: 'dockerhub-creds', usernameVariable: 'REGISTRY_USER', passwordVariable: 'REGISTRY_TOKEN')]) {
                                sh '''
                                    set -eu
                                    export ANSIBLE_PRIVATE_KEY_FILE="$SSH_KEY"
                                    export ANSIBLE_REMOTE_USER="$SSH_USER"
                                    export ANSIBLE_SSH_COMMON_ARGS="-o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KNOWN_HOSTS"
                                    ansible-playbook -i "$INVENTORY" ansible/deploy.yml
                                '''
                            }
                        }
                    }
                }
            }
        }
    }
    post {
        always {
            sh 'rm -rf .local'
        }
    }
}
