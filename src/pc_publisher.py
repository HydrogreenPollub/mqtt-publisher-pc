import os
import serial
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
import time

import capnp
from tempfile import SpooledTemporaryFile
from transitions import Machine

# --- Environment and Cap'n Proto Setup ---
load_dotenv()
capnp.remove_import_hook()

# Build an absolute path to the .capnp file relative to this script's location
PROTO_PATH = os.path.join(os.path.dirname(__file__), 'proto', 'ts_data.capnp')

try:
    ts_data_capnp = capnp.load(PROTO_PATH)
except FileNotFoundError:
    print(f"Error: Could not find schema at the expected path: {PROTO_PATH}")
    exit(1)


# --- Pure Function (Easy to Test) ---
def calculate_crc(data: bytes) -> bytes:
    """Calculates a simple 8-bit XOR checksum."""
    checksum = 0
    for byte_val in data:
        checksum ^= byte_val
    return checksum.to_bytes(1, 'big')


# --- Main State Machine Class (The Core Logic) ---
class TelemetryFrameListener:

    def _log_entry(self, event):
        """Generic callback to log state entries."""
        # This check prevents logging the loop in 'receiving_data'
        if event.transition.source == 'receiving_data' and event.transition.dest == 'receiving_data':
            return

        trigger_byte_hex = event.args[0].hex() if event.args and isinstance(event.args[0], bytes) else 'N/A'
        print(
            f"[STATE CHANGE] Event: process(0x{trigger_byte_hex}), "
            f"From: '{event.transition.source}' -> To: '{event.state.name}'"
        )

    def __init__(self, start_byte=b'\xFF', end_byte=b'\xEE', data_length=160):
        self.START_BYTE = start_byte
        self.END_BYTE = end_byte
        self.DATA_LENGTH = data_length
        self.rx_buffer = bytearray()
        self.last_valid_packet = None

        states = [
            {'name': 'waiting_for_start', 'on_enter': self._log_entry},
            {'name': 'receiving_data', 'on_enter': self._log_entry},
            {'name': 'waiting_for_end', 'on_enter': self._log_entry},
            {'name': 'waiting_for_crc', 'on_enter': self._log_entry},
        ]

        self.machine = Machine(
            model=self,
            states=states,
            initial='waiting_for_start',
            send_event=True
        )

        self.machine.add_transitions([
            {'trigger': 'process', 'source': 'waiting_for_start', 'dest': 'receiving_data',
             'conditions': 'is_start_byte', 'before': 'clear_and_append_buffer'},

            {'trigger': 'process', 'source': 'receiving_data', 'dest': 'receiving_data',
             'unless': 'is_buffer_full', 'before': 'append_to_buffer'},

            {'trigger': 'process', 'source': 'receiving_data', 'dest': 'waiting_for_end',
             'conditions': 'is_buffer_full', 'before': 'append_to_buffer'},

            {'trigger': 'process', 'source': 'waiting_for_end', 'dest': 'waiting_for_crc',
             'conditions': 'is_end_byte'},

            {'trigger': 'process', 'source': 'waiting_for_end', 'dest': 'waiting_for_start',
             'unless': 'is_end_byte'},

            {'trigger': 'process', 'source': 'waiting_for_crc', 'dest': 'waiting_for_start',
             'after': 'validate_and_finish'},
        ])

    def clear_and_append_buffer(self, event):
        self.rx_buffer.clear()
        self.rx_buffer.extend(event.args[0])

    def append_to_buffer(self, event):
        self.rx_buffer.extend(event.args[0])

    def is_start_byte(self, event):
        return event.args[0] == self.START_BYTE

    def is_end_byte(self, event):
        return event.args[0] == self.END_BYTE

    def is_buffer_full(self, event):
        # This condition is checked BEFORE appending the next byte.
        # It's true when buffer holds START_BYTE + (DATA_LENGTH - 1) bytes.
        return len(self.rx_buffer) == self.DATA_LENGTH

    def validate_and_finish(self, event):
        received_crc = event.args[0]
        # The buffer at this point should contain START_BYTE + DATA
        data_payload = self.rx_buffer[1:]

        if len(data_payload) != self.DATA_LENGTH:
            print(f"--> ERROR: Invalid frame length. Expected {self.DATA_LENGTH}, got {len(data_payload)}")
            self.last_valid_packet = None
            return

        self.last_valid_packet = data_payload.copy()
        # calculated_crc = calculate_crc(data_payload)
        # if received_crc == calculated_crc:
        #     print("--> SUCCESS: Packet is valid!")
        #     self.last_valid_packet = data_payload.copy()
        # else:
        #     print(f"--> ERROR: CRC Mismatch! Got {received_crc.hex()}, expected {calculated_crc.hex()}")
        #     self.last_valid_packet = None

    def get_new_packet(self):
        if self.last_valid_packet:
            packet_to_return = self.last_valid_packet
            self.last_valid_packet = None
            return packet_to_return
        return None


# --- Main Application Logic ---
def main():
    """Main execution function."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(username=os.getenv("BROKER_USERNAME"), password=os.getenv("BROKER_PASSWORD"))
    try:
        client.connect(os.getenv("BROKER_ADDRESS"), port=int(os.getenv("BROKER_PORT")))
        print("Successfully connected to MQTT broker.")
    except Exception as e:
        print(f"Could not connect to MQTT broker: {e}")
        return

    serial_port_name = input(f'Select serial port (default: {os.getenv("SERIAL_PORT")}): ') or os.getenv("SERIAL_PORT")
    try:
        ser = serial.Serial(
            port=serial_port_name,
            baudrate=int(os.getenv("SERIAL_BAUDRATE")),
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=1
        )
        print(f"Successfully opened serial port {serial_port_name}")
    except serial.SerialException as e:
        print(f"Could not open serial port {serial_port_name}: {e}")
        return

    listener = TelemetryFrameListener()

    print("\n--- Starting telemetry listener ---")
    while True:
        try:
            byte = ser.read(1)
            if not byte:
                continue

            listener.process(byte)

            packet = listener.get_new_packet()
            if packet:
                print("\n=== Message received, processing and sending... ===")
                client.publish(os.getenv("MQTT_TOPIC"), packet)

                try:
                    with SpooledTemporaryFile(max_size=1024, mode='wb+') as f:
                        f.write(packet)
                        f.seek(0)
                        data = ts_data_capnp.TSData.read(f)
                        print("Decoded Data:", data.to_dict())
                except Exception as e:
                    print(f"Cap'n Proto decoding error: {e}")

        except KeyboardInterrupt:
            print("\nExiting program.")
            break
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            break

    ser.close()
    client.disconnect()
    print("Cleanup complete.")


if __name__ == '__main__':
    main()
