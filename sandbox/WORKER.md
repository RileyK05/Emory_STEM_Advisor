# Dedicated sandbox worker deployment

This service belongs on a separate, disposable Linux host with Docker and gVisor `runsc` installed. It receives bounded HTTPS requests from the application host and stages bytes under `/var/lib/emory-sandbox/jobs`. The worker's Docker daemon must be local to this machine; never expose a Docker TCP socket to the application network.

Prepare the host and daemon before starting Compose:

1. Install Docker Engine and gVisor `runsc`, register the runtime as `runsc`, and verify `docker info --format '{{json .Runtimes}}'` lists it.
2. Build the execution images from the repository root: `docker build -f sandbox/Dockerfile.python -t emory-sandbox-python:3.12-v1 .`, `docker build -f sandbox/Dockerfile.javascript -t emory-sandbox-javascript:22-v1 .`, `docker build -f sandbox/Dockerfile.office -t emory-sandbox-office:7.4.7-v1 .`, and, when HTML visual review is enabled, `docker build -f sandbox/Dockerfile.browser -t emory-sandbox-browser:1.50.0-v1 .`. The Office and browser images pin their upstream package/browser versions; record and review the resulting image IDs when updating them.
3. Create the daemon-visible bind mount directory and allow the API UID to write: `sudo install -d -o 10001 -g 10001 /var/lib/emory-sandbox/jobs`. The same absolute host path is mounted into the worker container and then into execution containers.
4. Set `DOCKER_GID` to the numeric group ID owning `/var/run/docker.sock`, set `SANDBOX_WORKER_BEARER_TOKEN` to a random 32+ character secret shared with the application, then build and start using `docker compose -f sandbox/compose.worker.yaml up -d --build`.
5. Put an organization-managed HTTPS reverse proxy in front of `127.0.0.1:8080`. Restrict ingress to the application service, require TLS, and configure the client with that HTTPS origin and the shared token. Do not expose the worker on a public interface.

The worker needs Docker socket access to create, kill, and remove isolated containers. Treat this host as a security boundary: keep it separate from databases, model/provider credentials, and application secrets; patch it; restrict network ingress/egress; and do not run unrelated workloads there. Execution containers themselves use `--network none`, nonroot UID, dropped capabilities, read-only root filesystem, bounded CPU/memory/PIDs/time/output, and no credentials. Production startup refuses unless this worker is explicitly configured for its separate host and local gVisor daemon.

For Office jobs, Compose also requires the `emory-sandbox-office:7.4.7-v1`
image. When visual review is enabled it also requires the browser image named by
`SANDBOX_SCREENSHOT_IMAGE`. The worker's health response must show
`runtime=runsc` and the resolved `sha256` image IDs for every configured image.
The application checks the Office image ID before each conversion and the
browser image ID before each screenshot; the execution container uses that
resolved immutable image ID. `SANDBOX_SEPARATE_HOST_ATTESTED=true` is an
operator handoff attestation only. Verify the host separation, real `runsc`
runtime, output limits, cancellation, cleanup, formula caches, representative
DOCX/XLSX renders, and visual layout on the actual dedicated host before
enabling Office or code execution in production. Mocked runtime tests are not
live isolation proof.

The deployment is portable scaffolding, not proof of runtime isolation. Validate the host's gVisor runtime, filesystem behavior, limits, cancellation and cleanup before enabling code-execution tools in production.
