# Цифровий FPV відеоканал на базі ADALM PlutoSDR

Проєкт реалізує цифровий відеоканал FPV з малою затримкою на двох пристроях ADALM-Pluto. Відео з камери кодується в H.264, пакується в MPEG-TS і передається по UDP у модем на GNU Radio 3.10. Модем передає дані через Pluto з модуляцією QPSK і згортковим кодуванням. На приймальній стороні другий Pluto і такий самий модем відновлюють потік, після чого програвач ffplay виводить відео у вікно. Передавач працює на Windows або на Raspberry Pi 5, приймач працює на Windows.

## 1. Структура проєкту

```
pluto-fpv/
├── src/
│   ├── fpv_tx.grc              схема передавача, основне джерело модему TX
│   ├── fpv_rx.grc              схема приймача, основне джерело модему RX
│   ├── fpv_tx_headless.grc     передавач без вікна для Raspberry Pi, генерується
│   ├── make_tx_headless.py     створює fpv_tx_headless.grc з fpv_tx.grc
│   ├── common.ini              спільні налаштування обох сторін
│   ├── tx.ini                  налаштування передавача і його відео
│   ├── rx.ini                  налаштування приймача і його програвача
│   ├── video_tx.py             камера -> ffmpeg -> UDP -> модем
│   ├── video_rx.py             модем -> UDP -> лічильники -> ffplay
│   └── video_common.py         спільний код скриптів: конфігурація, журнал, пошук ffmpeg
├── run/
│   ├── tx_video.bat            запуск video_tx.py на Windows
│   ├── tx_video.sh             запуск video_tx.py на Linux
│   ├── rx_video.bat            запуск video_rx.py на Windows
│   └── rx_video.sh             запуск video_rx.py на Linux
├── pi/
│   ├── deploy.sh               переносить проєкт на Raspberry Pi і встановлює служби
│   ├── tx.ini                  tx.ini для плати, камера 640x512
│   ├── tx_modem_loop.sh        підтримує роботу модему, перезапускає його після втрати Pluto
│   ├── fpv-video.service       служба systemd для video_tx.py
│   └── fpv-modem.service       служба systemd для модему
└── logs/
    └── <дата>/                 tx.log, rx.log, video_tx.log, video_rx.log
```

## 2. Передумови

- Встановлення Git, radioconda з GNU Radio 3.10 і ffmpeg з ffplay:

    ```
    winget install Git.Git
    winget install ryanvolz.radioconda
    winget install Gyan.FFmpeg
    ```

- Встановлення драйверів USB для Pluto. Інсталятор `PlutoSDR-M2k-USB-Drivers.exe` знаодиться за URL: https://github.com/analogdevicesinc/plutosdr-m2k-drivers-win/releases. Після встановлення Pluto визначається в системі як мережевий адаптер з адресою `192.168.2.1`.

- Клонування репозиторію:

    ```
    git clone https://github.com/dmmitrenko/pluto-fpv.git
    cd pluto-fpv
    ```

- Перевірка:

    ```
    %USERPROFILE%\radioconda\python.exe -c "from gnuradio import iio, fec, digital, qtgui; print('ok')"
    ffmpeg -version
    %USERPROFILE%\radioconda\Library\bin\iio_info.exe -u ip:192.168.2.1
    ```

- Перевірка підключення Pluto: `iio_info.exe -u ip:192.168.3.1`.

- Налаштування Raspberry Pi:
    ```
    sudo apt update
    sudo apt install gnuradio libiio-utils ffmpeg git
    python3 -c "from gnuradio import iio, fec, digital; print('ok')"
    ```

## 3. Як запустити

Перед першим запуском перевірте `pluto_uri` у `tx.ini` і `rx.ini`. Файл `common.ini` має збігатися на обох сторонах.

На Windows кожна сторона запускається у два кроки: спочатку схема в GNU Radio Companion (`src/fpv_rx.grc` або `src/fpv_tx.grc`), потім скрипт відео:

```
run\rx_video.bat
run\tx_video.bat
```

На Raspberry Pi передавач встановлюється з кореня репозиторію:

```
pi/deploy.sh fpv@fpv-tx.local
```