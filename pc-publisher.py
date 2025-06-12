import os
import serial
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

import capnp
from tempfile import SpooledTemporaryFile
from transitions import Machine
from transitions.extensions import GraphMachine

load_dotenv()
capnp.remove_import_hook()
try:
    ts_data_capnp = capnp.load('proto/ts_data.capnp')
except FileNotFoundError:
    print("Error: Could not find 'proto/ts_data.capnp'. Make sure the path is correct.")
    exit(1)

serial_port = ""
try:
    serial_client = serial.Serial(
        port=serial_port,
        baudrate=int(os.getenv("SERIAL_BAUDRATE")),
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        bytesize=serial.EIGHTBITS,
        timeout=1
    )
    print(f"Successfully opened serial port {serial_port}")
except serial.SerialException as e:
    print(f"Could not open serial port {serial_port}: {e}")
    exit(1)

serial_client = serial.Serial(
    port=serial_port,
    baudrate=int(os.getenv("SERIAL_BAUDRATE")),
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    bytesize=serial.EIGHTBITS,
    timeout=10
)

print("SERIAL_PORT", serial_port)
print("SERIAL_BAUDRATE", os.getenv("SERIAL_BAUDRATE"))
print("MQTT_TOPIC", os.getenv("MQTT_TOPIC"))

FRAME_LENGTH = 160

def calculate_crc(data: bytes) -> bytes:
    checksum = 0
    for byte_val in data:
        checksum ^= byte_val
    return checksum.to_bytes(1, 'big')


class TelemetryFrameListener:
    states = [
        'waiting_for_start',
        'receiving_data',
        'waiting_for_end',
        'waiting_for_crc'
    ]

    def __init__(self, start_byte=b'\xFF', end_byte=b'\xEE', data_length=160):
        self.START_BYTE = start_byte
        self.END_BYTE = end_byte
        self.DATA_LENGTH = data_length
        self.rx_buffer = bytearray()
        self.last_valid_packet = None

        # --- Initialize the state machine ---
        self.machine = Machine(
            model=self,
            states=TelemetryFrameListener.states,
            initial='waiting_for_start',
            after_state_change=self._log_state_change
        )

        # --- Define transitions declaratively ---
        self.machine.add_transitions([
            {
                'trigger': 'process',
                'source': 'waiting_for_start',
                'dest': 'receiving_data',
                'conditions': 'is_start_byte',
                'before': ['clear_buffer', 'append_to_buffer']
            },
            {
                'trigger': 'process',
                'source': 'receiving_data',
                'dest': 'receiving_data',
                'unless': 'is_buffer_full',
                'before': 'append_to_buffer'
            },
            {
                'trigger': 'process',
                'source': 'receiving_data',
                'dest': 'waiting_for_end',
                'conditions': 'is_buffer_full',
                'before': 'append_to_buffer'
            },
            {
                'trigger': 'process',
                'source': 'waiting_for_end',
                'dest': 'waiting_for_crc',
                'conditions': 'is_end_byte'
            },
            {
                'trigger': 'process',
                'source': 'waiting_for_end',
                'dest': 'waiting_for_start',
                'unless': 'is_end_byte'
            },
            {
                'trigger': 'process',
                'source': 'waiting_for_crc',
                'dest': 'waiting_for_start',
                'after': 'validate_and_finish'
            }
        ])

    def _log_state_change(self, event):
        """
        Prints details about the state transition. The 'event' object is
        automatically passed by the 'transitions' library.
        """
        # event.args contains the positional arguments passed to the trigger method (e.g., the byte)
        trigger_byte_hex = event.args[0].hex() if event.args else 'N/A'
        print(
            f"[STATE CHANGE] Event: process(0x{trigger_byte_hex}), "
            f"From: '{event.source}', To: '{event.state.name}'"
        )

    # --- Methods used by the state machine (conditions and callbacks) ---
    def clear_buffer(self, byte):
        self.rx_buffer.clear()

    def append_to_buffer(self, byte):
        self.rx_buffer.extend(byte)

    def is_start_byte(self, byte):
        return byte == self.START_BYTE

    def is_end_byte(self, byte):
        return byte == self.END_BYTE

    def is_buffer_full(self, byte):
        # We check if the buffer is full *before* adding the next byte.
        return len(self.rx_buffer) >= self.DATA_LENGTH - 1

    def validate_and_finish(self, byte):
        """Called after leaving the waiting_for_crc state."""
        received_crc = byte
        calculated_crc = calculate_crc(self.rx_buffer)
        if received_crc == calculated_crc:
            print("Packet is valid!")
            self.last_valid_packet = self.rx_buffer.copy()
        else:
            print(f"CRC Mismatch! Got {received_crc.hex()}, expected {calculated_crc.hex()}")
            self.last_valid_packet = None

    def get_new_packet(self):
        """
        Checks for and returns a new, valid packet if one has been received.

        This method "consumes" the packet, meaning it will return None
        on subsequent calls until a new packet is fully processed.

        Returns:
            A bytearray containing the valid packet, or None if no new
            packet is available.
        """
        if self.last_valid_packet:
            # Copy the packet to a local variable
            packet_to_return = self.last_valid_packet
            # Clear the internal packet storage
            self.last_valid_packet = None
            # Return the copied packet
            return packet_to_return

        # If we get here, no new packet was available
        return None


if __name__ == '__main__':
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.username_pw_set(username=os.getenv("BROKER_USERNAME"), password=os.getenv("BROKER_PASSWORD"))

    try:
        mqtt_client.connect(os.getenv("BROKER_ADDRESS"), port=int(os.getenv("BROKER_PORT")))
        print("Successfully connected to MQTT broker.")
    except Exception as e:
        print(f"Could not connect to MQTT broker: {e}")
        exit(1)

    listener = TelemetryFrameListener()

    while True:
        try:
            if serial_client.in_waiting <= 0:
                continue

            listener.process(serial_client.read(1))

            buffer = listener.get_new_packet()
            if buffer is None:
                continue

            print(" ")
            print("=== Message received and sent (%d bytes) ===" % len(buffer))
            print(buffer.hex(sep=' '))

            try:
                f = SpooledTemporaryFile(256, 'wb+')
                f.write(buffer)
                f.seek(0)
                data = ts_data_capnp.TSData.read(f)
                data = data.to_dict()
                print(data)
            except Exception as e:
                print(e)

            mqtt_client.publish(os.getenv("MQTT_TOPIC"), buffer)

        except KeyboardInterrupt:
            print("\nExiting program.")
            break
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            break

        serial_client.close()
        mqtt_client.disconnect()
        print("Cleanup complete.")
