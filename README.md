## PC MQTT Publisher

Recevice telemetry data from LoRa and pass them to Linux server via MQTT protocol.

### Linux usage
 
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

Install (make sure you've copied `.env` file from Wiki), open powershell as administrator:
```shell
set-executionpolicy RemoteSigned
python -m venv .venv
.\venv\Scripts\Activate.ps
pip install -r requirements.txt
```

Prepare:
```shell
.\venv\Scripts\Activate.ps
```

Execute:
```shell
python src/pc_publisher.py
```
