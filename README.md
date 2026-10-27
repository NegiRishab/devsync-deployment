# DevSync deployment

Jenkins runs Ansible on the Jenkins server. Ansible connects over SSH to a
separate application server and deploys Docker Compose there. GitHub Actions
continues to test, build and publish images; Jenkins does not rebuild them.

## Repository contents

- `Jenkinsfile`: setup/deploy job and optional five-minute polling.
- `ansible/setup.yml`: install Docker/Compose and copy protected runtime settings.
- `ansible/deploy.yml`: stage a release, authenticate to Docker Hub and deploy.
- `compose.prod.yml`: production image references, internal databases and health checks.
- `scripts/fetch_release.py`: verify successful main-push CI runs and release artifacts.
- `scripts/deploy.py`: serialize deployments, preserve service references and restore
  previous application images if deployment fails.
- `jenkins/Dockerfile`: extend the Jenkins container with Ansible, Python and SSH.

No SSH keys, tokens or completed runtime environment files belong in this repo.

## 1. Prepare the Jenkins execution environment

Ansible must be installed where the Jenkins job executes. Installing it only on
the Docker host does not make it available inside the Jenkins container.

The supplied Dockerfile extends the Jenkins LTS image. Build it on the Jenkins
server:

```sh
docker build -t devsync-jenkins:ansible -f jenkins/Dockerfile .
```

Update your existing Jenkins container configuration to use that image. Preserve
its existing Jenkins home volume, ports and configuration; do not start a fresh
empty Jenkins instance. Back up Jenkins home before updating the controller image.
Alternatively, provision a dedicated Jenkins agent with Python, Ansible and SSH
on the same server and leave the controller unchanged.

Give the executor that has these tools the node label `devsync-ansible`.
The pipeline does not need a Docker socket inside Jenkins.

Required Jenkins plugins: Pipeline, Git and Credentials Binding. Ansible is invoked
using `ansible-playbook`; the Jenkins Ansible plugin is not required.

## 2. Application server prerequisites

The supplied setup playbook targets **Ubuntu/Debian on x86_64**. Other OS families
need an installation task adjustment. The application server needs Python 3, SSH,
and an existing SSH user with passwordless sudo for the deployment playbooks.

Allow SSH from the Jenkins server and HTTP port 80 for application clients.
Jenkins must have outbound access to GitHub, and the application server must reach
Docker Hub and its package repositories. Database ports are not published.

Copy `ansible/inventory.example.ini`, replace the example IP and remove its
`ansible_user` setting if you want the SSH credential's username to control it.
Keep the real inventory in Jenkins as a secret file.

Verify the application's SSH host-key fingerprint using the server console or
another trusted channel. Store its known_hosts entry as a Jenkins secret file.
Host-key checking stays enabled.

## 3. Add Jenkins credentials

Under Manage Jenkins -> Credentials, add:

| ID | Type | Value |
|---|---|---|
| `devsync-app-ssh` | SSH username with private key | Application server login |
| `devsync-inventory` | Secret file | Completed Ansible inventory |
| `devsync-known-hosts` | Secret file | Verified SSH host-key entry |
| `devsync-runtime-env` | Secret file | Completed `runtime.env.example` |
| `devsync-github-token` | Secret text | Fine-grained token with Actions: read for all three application repos |
| `devsync-dockerhub-pull` | Username with password | Docker Hub username and read-only access token |

For a private deployment repository, add a separate Git SCM credential with read
access and select it when configuring the Jenkins job.

Use the application server's public URL for `FRONTEND_URL` and
`PUBLIC_FRONTEND_URL`. Keep internal addresses such as `http://backend:5000`.
Set the same URL-safe PostgreSQL password in POSTGRES_PASSWORD and DB_URL.
Database password changes after initialization require a separate database change;
editing the environment file does not change an existing database user's password.

Runtime settings are copied to `/opt/devsync/runtime.env`, root-owned mode 0600.
Compose passes only the selected variables to each service.
The existing application uses `NODE_ENV=local` to disable TLS for the internal
PostgreSQL connection. That value is intentional for this Compose stack.

## 4. Create the Jenkins job

1. Commit and push this repository.
2. Create a Pipeline job named `devsync-deploy`.
3. Select **Pipeline script from SCM -> Git**.
4. Repository: `git@github.com:NegiRishab/devsync-deployment.git`
   (or HTTPS with the appropriate SCM credential).
5. Branch: `*/main`; script path: `Jenkinsfile`.
6. Save. The first run registers parameters; if the unconfigured first deployment
   fails, configure credentials and use Build with Parameters.

Use **ACTION=setup** first. This installs Docker/Compose and copies runtime
configuration; it does not start the application.

Then run **ACTION=deploy, SERVICE=all, RUN_ID empty, AUTO_DEPLOY=false**.
All three repositories must have a successful main-push CI run with an unexpired
release artifact. Manual GitHub Actions runs do not publish release artifacts
with the current application workflows.

The script currently maps:
- backend -> NegiRishab/TempSyncBackend1 -> ankit42098/devsync-main and devsync-backend-migrate
- chat -> NegiRishab/ChatService -> ankit42098/devsync-chat
- frontend -> NegiRishab/DevSyncFrontend -> ankit42098/devsync-frontend

Namespace checks exist in both Python scripts; update both if your namespace changes.

## 5. Automatic deployment

After the first deployment works, run the job with:
`ACTION=deploy, SERVICE=all, RUN_ID empty, AUTO_DEPLOY=true`.

Jenkins polls every five minutes. It selects each repository's latest successful
main-push CI run, validates the artifact, and compares it with the server's release
record. Unchanged releases do not restart containers. No public Jenkins webhook
endpoint or changes to the application's GitHub workflows are required.

To stop polling, run with AUTO_DEPLOY=false. Jenkins updates the schedule when it
loads the Jenkinsfile. Already-deployed releases use the server's saved image references even after their
artifacts expire. A new release still requires an unexpired artifact; extend chat's
short artifact retention if Jenkins might be offline.

For a manual single-service release, leave AUTO_DEPLOY=false, select the service,
and optionally provide a GitHub Actions RUN_ID. Older runs are rejected. This
parameter is not a rollback mechanism.

## Deployment behavior

The first release starts PostgreSQL, Redis and MongoDB with persistent database
volumes. Every backend release runs the matching compiled migration image before
replacing the backend. Only changed application services are replaced, plus the
frontend when needed to refresh Nginx's cached upstream addresses.

Application health checks and HTTP checks through Nginx must pass before the
`/opt/devsync/current` symlink points to the new release directory. Each release
directory retains the full image record and Compose configuration. Docker image
references use immutable digests; mutable tags and arbitrary registries are rejected.

A Jenkins job lock and an application-server file lock serialize deployments.
On deployment failure, a previous successful application's images are restored.
**Database migrations are never automatically reversed.** Use backward-compatible
migrations; image rollback cannot undo schema/data changes. The first deployment
has no previous release to restore. Failed deployments preserve data volumes.

Runtime secrets are separate from release history. After changing them with setup,
a subsequent new image release will recreate the affected service; unchanged
release polling does not apply runtime changes. Backups and end-to-end application
tests are still required; HTTP health checks only confirm basic availability.

## Local validation

```sh
python3 -m unittest discover -s tests -v
ansible-playbook -i ansible/inventory.example.ini ansible/setup.yml --syntax-check
ansible-playbook -i ansible/inventory.example.ini ansible/deploy.yml --syntax-check
```

These checks do not contact the application server. An actual Jenkins run and
remote deployment require your inventory, credentials and reachable server.

References:
- https://www.jenkins.io/doc/book/pipeline/syntax/
- https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/deb822_repository_module.html
- https://docs.github.com/en/rest/actions/artifacts
- https://docs.docker.com/engine/install/ubuntu/
