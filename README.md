# OpenVPN Web Management Panel

Легковесная веб-панель на Python/Flask для управления клиентскими конфигурациями OpenVPN (на базе логики скрипта nyr/openvpn-install). Автоматизирует рутину генерации сертификатов, обеспечивает живой поиск по базе данных Common Name (CN) и позволяет безопасно отзывать доступ.

## Возможности
* Выпуск `.ovpn` конфигураций в один клик.
* Автоматическая валидация ввода (замена пробелов на нижнее подчеркивание на лету).
* Живой поиск по существующим сертификатам.
* Быстрое скрытие/отображение отозванных клиентов с помощью чекбокса.
* Полностью автономный адаптивный интерфейс с автоматической поддержкой системной темной/светлой темы (без внешних зависимостей и CDN).
* Интеграция с systemd и Nginx Reverse Proxy.

---

## Пошаговое развертывание

### Шаг 1. Предварительная настройка сети (Маршрутизация и NFTables)
Включите форвардинг пакетов IPv4 на уровне ядра:

    echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/forward.conf
    sysctl -p /etc/sysctl.d/forward.conf

Настройте правила трансляции адресов (NAT/Masquerade) для интерфейса `tun0` через nftables. Отредактируйте `/etc/nftables.conf`:

    #!/usr/sbin/nft -f
    flush ruleset
    table ip nat {
        chain POSTROUTING {
            type nat hook postrouting priority srcnat; policy accept;
            iifname "tun0" masquerade
        }
    }

Активируйте и запустите службу межсетевого экрана:

    systemctl enable --now nftables

### Шаг 2. Инициализация OpenVPN Core
Запустите базовый инсталлятор OpenVPN передав ему стандартные параметры (Интерфейс, UDP, Порт 1194, Имя первого клиента `client`):

    wget -O /tmp/openvpn.sh https://raw.githubusercontent.com/Ttolyanich/openvpn-web/main/openvpn.sh
    chmod +x /tmp/openvpn.sh
    /tmp/openvpn.sh

### Шаг 3. Клонирование и деплой веб-панели
Склонируйте репозиторий в системную директорию `/opt`:

    cd /opt
    git clone https://github.com/Ttolyanich/openvpn-web.git
    cd openvpn-web

Установите пакетный менеджер Python, утилиты авторизации (содержат htpasswd), веб-сервер и глобальные зависимости:

    apt-get update && apt-get install python3-pip python3-flask apache2-utils nginx -y
    pip3 install -r requirements.txt --break-system-packages

### Шаг 4. Настройка системной службы (systemd)
Зарегистрируйте юнит-файл в системе и запустите бэкенд:

    ln -sf /opt/openvpn-web/openvpn-web.service /etc/systemd/system/openvpn-web.service
    systemctl daemon-reload
    systemctl enable --now openvpn-web

### Шаг 5. Защита и публикация через Nginx (HTTP Basic Auth)
Для работы утилиты `htpasswd` убедитесь, что на Шаге 3 был установлен пакет `apache2-utils`. 
Сгенерируйте файл зашифрованных паролей для авторизации (замените `admin` и `ваш_пароль` на свои данные):

    htpasswd -bc /etc/nginx/.vpn_panel_passwd admin ваш_пароль

Привяжите конфигурационный файл виртуального хоста Nginx и перезапустите веб-сервер:

    ln -sf /opt/openvpn-web/nginx-openvpn-web.conf /etc/nginx/sites-enabled/openvpn-web.conf
    rm -f /etc/nginx/sites-enabled/default
    systemctl restart nginx
