FROM node:22.17.1-bookworm-slim

ENV NODE_OPTIONS=--disable-proto=throw

# No npm, package manager, application code, shell entrypoint, or credentials.
# The untrusted program is supplied on a read-only bind mount at runtime.
USER 65532:65532
WORKDIR /output
