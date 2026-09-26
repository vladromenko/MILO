# MILO: установка и запуск

Полная техническая процедура находится в [Installation](INSTALLATION.md), схема
сети — в [Networking](NETWORKING.md), проверка — в [Testing](TESTING.md). Ниже
короткая памятка оператора для уже настроенной системы.

## Установка из Git

После установки базовой системы NVIDIA/ROS на Jetson и HailoRT на Raspberry Pi
весь код, модели, зависимости и сервисы устанавливаются из этого репозитория:

```bash
# Jetson
git clone https://github.com/vladromenko/MILO.git ~/MILO
cd ~/MILO
./install.sh jetson

# Raspberry Pi
git clone https://github.com/vladromenko/MILO.git ~/MILO
cd ~/MILO
./install.sh pi
```

Установщики не запускают MILO и не двигают руку. После однократной настройки
устройств, SSH и `MILO-NET` запуск выполняется с телефона или командой `./milo start`.

## Запуск на презентации

1. Освободите траекторию руки и проверьте провода.
2. Одновременно включите Jetson, Raspberry Pi, экран и контроллер руки.
3. На телефоне подключитесь к `MILO-NET`; согласитесь остаться в сети без интернета.
4. Откройте `http://10.42.0.1/`, введите Access code и проверьте статусы.
5. Нажмите **Start MILO**. Рука последовательно принимает стартовую позу, затем
   включается слежение за лицом. J6 не управляется.
6. Кнопка **Stop MILO** останавливает программу на обеих платах, но оставляет сайт
   и точку доступа для повторного запуска.

## Подключение с Mac

```bash
ssh milo-jetson
cd /home/vlad/MILO
./milo status
./milo logs
```

Raspberry Pi доступен через Jetson:

```bash
ssh milo-pi
cd /home/vlados/MILO
```

Если после перезагрузки Mac исчез локальный адрес проводного адаптера, найдите его
имя через `networksetup -listallhardwareports` и добавьте адрес, например:

```bash
sudo ifconfig en7 inet 10.43.0.2 netmask 255.255.255.0 alias
```

## Основные команды

```bash
./milo start              # сервисы, стартовая поза, слежение
./milo start --no-home    # запуск камеры/голоса без движения
./milo stop
./milo home
./milo arm
./milo disarm
./milo status --json
./milo wifi               # пароль MILO-NET
./milo web-code           # отдельный код сайта
```

Язык меняется только в настройках сайта. Личная память, лица, пароли, ключи,
модели и логи не хранятся в Git. Перед изменением кода остановите MILO, создайте
ветку, запустите тесты и только затем повторите проверку с железом.
