# Deploy de OLIMPO en una VPS

App web (Streamlit) + bot de Telegram, corriendo en Docker vía
`docker-compose.yml` en la raíz del repo. `web` sirve la app en
`127.0.0.1:8501`; `bot` corre `bot_auth.py` por polling. Ambos comparten
la base SQLite y `external_modules/` a través de volúmenes.

## 1. Requisitos en la VPS

```bash
curl -fsSL https://get.docker.com | sh
sudo apt install -y nginx certbot python3-certbot-nginx
```

## 2. Clonar y configurar

```bash
git clone <url-del-repo> /opt/olimpo
cd /opt/olimpo
cp .env.example .env
```

Edita `.env` con los valores reales (`OLIMPO_BOT_TOKEN`, `OLIMPO_ADMINS`,
`SMSPOOL_API_KEY`, etc). No definas `OLIMPO_DB_PATH` ahí — `docker-compose.yml`
ya lo fuerza a `/app/data/olimpo.db`, dentro del volumen persistente
`olimpo_data`.

## 3. Levantar los servicios

```bash
docker compose up -d --build
docker compose logs -f
```

`restart: unless-stopped` hace que ambos contenedores vuelvan a arrancar
solos si el droplet se reinicia o si `dockerd` se cae — no hace falta
systemd para esto.

## 4. Nginx + HTTPS

```bash
sudo cp deploy/olimpo.nginx.conf /etc/nginx/sites-available/olimpo
# editar tu-dominio.com dentro del archivo
sudo ln -s /etc/nginx/sites-available/olimpo /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d tu-dominio.com
```

## 5. Backups automáticos

```bash
crontab -e
# agregar:
0 4 * * * /opt/olimpo/deploy/backup.sh >> /var/log/olimpo-backup.log 2>&1
```

Guarda los últimos 14 días de `olimpo.db` en `/opt/olimpo/backups/`.

## 6. Actualizar tras un cambio en el repo

```bash
cd /opt/olimpo
git pull
docker compose up -d --build
```

## 7. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

No expongas el puerto 8501 a internet directo — solo vía nginx (el
`docker-compose.yml` ya lo publica solo en `127.0.0.1`).
