# Lightny SPB edge

The public `lightny.ru` hostnames terminate TLS on the SPB nginx server and proxy application traffic through AmneziaWG to the MicroK8s ingress on `10.77.0.1:443`.

Files:

- `app.lightny.ru-http.conf`: temporary HTTP-only bootstrap used before the first certificate is issued.
- `app.lightny.ru.conf`: browser and Telegram Mini App origin. `/api/` routes to FastAPI and `/images/` routes to the image proxy.
- `api-images.lightny.ru.conf`: optional direct API and image origins.

Certificates must use an unattended nginx or webroot ACME authenticator. After any certificate change, run `certbot renew --dry-run` and verify the renewal timer.

Apply changes by copying into `/etc/nginx/sites-available/`, linking from `/etc/nginx/sites-enabled/`, running `nginx -t`, and reloading nginx. Keep the previous active file as a timestamped rollback copy.
