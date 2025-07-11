## PC MQTT Publisher

Recevice telemetry data from LoRa and pass them to Linux server via MQTT protocol.

### Linux usage

__Copy `.env` file from Hydrogreen Wiki.__

Install (make sure you've copied `.env` file from Wiki):
```shell
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Prepare:
```shell
sudo chmod 666 /dev/ttyACM*
source .venv/bin/activate
```

Execute:
```shell
python3 src/pc_publisher.py
```

### Windows usage

__Copy `.env` file from Hydrogreen Wiki.__

Install python 3.11 from [here](https://www.python.org/downloads/release/python-3110/). 
(Check Add path)

Install (use `powershell`):
```shell
set-executionpolicy RemoteSigned
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Prepare:
```shell
.\.venv\Scripts\Activate.ps1
```

Execute:
```shell
python src/pc_publisher.py
```
