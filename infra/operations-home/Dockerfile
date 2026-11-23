FROM caddy:2.11.9-alpine@sha256:0bbb055bda6793c999bde78b1b5ce3e89f829fb031af2cf50056ea1246559fee
COPY --chmod=644 Caddyfile /etc/caddy/Caddyfile
COPY --chmod=755 site/ /srv/
EXPOSE 8080
