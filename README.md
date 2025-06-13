## mqtt-publisher-pc
 
Install:
```shell
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Prepare:
```shell
sudo chmod 666 /dev/ttyACM*
cd src
```

Execute:
```shell
python3 pc_publisher.py
```