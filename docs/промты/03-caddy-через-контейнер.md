Майкл, от Александра: «да» на шаг 3 через контейнер caddy. Шаг 2 (доступ к ключу реестра) отменён — права /opt/hive-registry и access.key не трогать, вместо этого только проверка версии systemd (пункт Г). Больше ничего не менять, секреты не показывать.

А. Проверки до правки (если что-то не так — остановись и пришли вывод)
- `docker inspect -f '{{.HostConfig.NetworkMode}}' caddy` — ожидается host. Если не host — стоп: тогда адрес 127.0.0.1:18780 изнутри контейнера не дойдёт до службы на хосте.
- `docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' caddy` — ожидается /opt/caddy -> /etc/caddy (папка, не отдельный файл). Если смонтирован отдельный файл — стоп.
- Копия /opt/caddy/Caddyfile.bak-2026-11-01-app уже есть (шаг 0). Коды сайтов «до» уже сняты.

Б. Правка
- Дописать в конец файла, не заменяя его (дописать через `cat >> /opt/caddy/Caddyfile <<'EOF2' … EOF2`):
```
# 01.11.2026: мини-приложение Александра (служба office-app), откат — Caddyfile.bak-2026-11-01-app
app.afhive.tech:8443 {
	handle /ping {
		respond "ok" 200
	}
	handle {
		reverse_proxy 127.0.0.1:18780
	}
}
```
- `docker exec caddy grep -c 'app.afhive.tech' /etc/caddy/Caddyfile` — ожидается 1 (контейнер видит правку).
- `docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile` — без ошибок. Если ошибка — откат (ниже), перезагрузку не делать.
- `docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`

В. Проверки после
- `curl -s https://app.afhive.tech:8443/ping` → ok (сертификат может выпускаться до минуты, повтори до 3 раз).
- `curl -s -o /dev/null -w '%{http_code}' https://app.afhive.tech:8443/` → 502 — это нормально, службы приложения ещё нет.
- Цикл кодов сайтов из шага 0 — коды должны совпасть с «до»: главный 200, qa 401, files 200, home 200, claude 200, panel 401, gis 401.
- `docker logs --since 5m caddy 2>&1 | grep -iE 'error|app.afhive' | tail -20` — пришли, если есть ошибки.

Г. Только чтение: `systemctl --version | head -1` — номер версии.

Откат
- `cp /opt/caddy/Caddyfile.bak-2026-11-01-app /opt/caddy/Caddyfile && docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`, затем цикл кодов сайтов.

Что прислать Александру: по пунктам А–Г — сделано/не сделано, выводы проверок, запись в свой журнал изменений VPS.
