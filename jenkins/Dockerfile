FROM jenkins/jenkins:lts-jdk21
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv openssh-client \
    && python3 -m venv /opt/ansible \
    && /opt/ansible/bin/pip install --no-cache-dir 'ansible-core>=2.19,<2.20' \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/ansible/bin:$PATH"
USER jenkins
