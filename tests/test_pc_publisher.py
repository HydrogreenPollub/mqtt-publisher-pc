import pytest
from src.pc_publisher import TelemetryFrameListener, calculate_crc


# --- Test Data and Fixtures ---

@pytest.fixture
def listener():
    """Returns a fresh instance of the listener for each test."""
    return TelemetryFrameListener(start_byte=b'\xFF', end_byte=b'\xEE', data_length=160)


@pytest.fixture
def data_payload():
    """Returns a standard 160-byte data payload."""
    return bytes([i % 256 for i in range(160)])


@pytest.fixture
def valid_frame(data_payload):
    """Returns a complete, valid telemetry frame as bytes."""
    start = b'\xFF'
    end = b'\xEE'
    crc = calculate_crc(data_payload)
    return start + data_payload + end + crc


# --- Basic and Utility Test Cases ---

def test_calculate_crc():
    """Tests the CRC function with known inputs and outputs."""
    assert calculate_crc(b'\x01\x02\x03') == b'\x00'
    assert calculate_crc(b'\xAA\xBB\xCC') == b'\xdd'


def test_initial_state(listener):
    """Checks if the listener initializes in the correct state."""
    assert listener.is_waiting_for_start()


# --- Atomic State Transition Tests ---

def test_atomic_transition_from_waiting_to_receiving(listener):
    """Tests the specific transition from 'waiting_for_start' to 'receiving_data'."""
    # 1. Start in the initial state
    assert listener.is_waiting_for_start()

    # 2. Process a garbage byte -> should remain in the same state
    listener.process(b'\x01')
    assert listener.is_waiting_for_start()
    assert listener.rx_buffer == b''

    # 3. Process the correct START_BYTE -> should transition
    listener.process(listener.START_BYTE)
    assert listener.is_receiving_data()
    assert listener.rx_buffer == listener.START_BYTE  # Buffer should now contain the start byte


def test_atomic_transition_loop_in_receiving(listener, data_payload):
    """Tests that the FSM correctly loops within the 'receiving_data' state."""
    # 1. Authentically transition into the receiving_data state
    listener.process(listener.START_BYTE)
    assert listener.is_receiving_data()

    # 2. Process all data bytes except the very last one
    # The buffer already has START_BYTE (1), so we add DATA_LENGTH - 1 bytes.
    for i in range(listener.DATA_LENGTH - 1):
        listener.process(data_payload[i:i + 1])
        # It should remain in the receiving_data state during this loop
        assert listener.is_receiving_data()

    # 3. Check the state before the final data byte
    assert len(listener.rx_buffer) == listener.DATA_LENGTH  # START_BYTE + 159 data bytes
    assert listener.is_receiving_data()

    # 4. Process the final data byte, which should trigger the transition out
    listener.process(data_payload[listener.DATA_LENGTH - 1:listener.DATA_LENGTH])
    assert listener.is_waiting_for_end()
    assert len(listener.rx_buffer) == listener.DATA_LENGTH + 1  # START_BYTE + 160 data bytes


# --- Full Workflow and Error Condition Tests ---

def test_happy_path_full_valid_frame(listener, valid_frame, data_payload):
    """
    Tests the entire successful workflow from start to finish by processing
    a single valid frame byte-by-byte.
    """
    # 1. Process garbage bytes -> should be ignored
    listener.process(b'\x01')
    assert listener.is_waiting_for_start()

    # 2. Process the entire valid frame
    for byte_int in valid_frame:
        listener.process(byte_int.to_bytes(1, 'big'))

    # 3. Check the final result
    packet = listener.get_new_packet()
    assert packet is not None
    assert packet == data_payload

    assert listener.get_new_packet() is None
    assert listener.is_waiting_for_start()


def test_error_path_bad_end_byte(listener, data_payload):
    """Tests that a frame with an incorrect end byte is rejected and the machine resets."""
    start = b'\xFF'
    bad_end = b'\xDD'
    data = data_payload
    crc = calculate_crc(data)

    bad_frame = start + data + bad_end + crc

    for byte_int in bad_frame:
        listener.process(byte_int.to_bytes(1, 'big'))

    assert listener.get_new_packet() is None
    assert listener.is_waiting_for_start()


def test_error_path_bad_crc(listener, data_payload):
    """Tests that a frame with an incorrect CRC is rejected and the machine resets."""
    start = b'\xFF'
    end = b'\xEE'
    data = data_payload
    bad_crc = b'\x11'

    bad_frame = start + data + end + bad_crc

    for byte_int in bad_frame:
        listener.process(byte_int.to_bytes(1, 'big'))

    assert listener.get_new_packet() is None
    assert listener.is_waiting_for_start()


def test_handles_consecutive_frames(listener, valid_frame, data_payload):
    """Tests that the listener can process two valid frames back-to-back."""
    two_frames = valid_frame + valid_frame

    packets_found = []
    for byte_int in two_frames:
        listener.process(byte_int.to_bytes(1, 'big'))
        packet = listener.get_new_packet()
        if packet:
            packets_found.append(packet)

    assert len(packets_found) == 2
    assert packets_found[0] == data_payload
    assert packets_found[1] == data_payload

    assert listener.get_new_packet() is None
    assert listener.is_waiting_for_start()
