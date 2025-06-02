#!/usr/bin/env python3
"""
Opulent Voice Receiver
Receives and plays audio from Opulent Voice radio system
Works on Mac, Linux, and Windows
Installation on Mac:
    brew install portaudio opus
    pip3 install pyaudio opuslib
Usage:
    python3 opulent_voice_receiver.py
"""
import sys
import socket
import struct
import time
import threading
import queue
from datetime import datetime
try:
    import opuslib
    print("✓ opuslib ready")
except ImportError:
    print("✗ opuslib missing. Install with:")
    print("  pip3 install opuslib")
    sys.exit(1)
try:
    import pyaudio
    print("✓ pyaudio ready")
except ImportError:
    print("✗ pyaudio missing. Install with:")
    print("  Mac: brew install portaudio && pip3 install pyaudio")
    print("  Linux: sudo apt install python3-pyaudio")
    print("  Windows: pip3 install pyaudio")
    sys.exit(1)
try:
    from emoji_data_python import replace_colons
    print("✓ emoji-data-python ready")
except ImportError:
    print("✗ emoji-data-python missing. Install with:")
    print("  pip3 install emoji-data-python")
    sys.exit(1)
class OpulentVoiceProtocol:
    """Opulent Voice Protocol Parser"""
    MAGIC_BYTES = b'\xFF\x5D'  # Sync Word taken from M17
    FRAME_TYPE_AUDIO = 0x01
    FRAME_TYPE_TEXT = 0x02
    FRAME_TYPE_CONTROL = 0x03
    FRAME_TYPE_DATA = 0x04
    HEADER_SIZE = 14
    @staticmethod
    def parse_frame(frame_data):
        """Parse received Opulent Voice frame"""
        if len(frame_data) < OpulentVoiceProtocol.HEADER_SIZE:
            return None
        try:
            magic, station_id, frame_type, sequence, payload_len, reserved = struct.unpack(
                '>2s 6s B H H B', frame_data[:OpulentVoiceProtocol.HEADER_SIZE]
            )
            if magic != OpulentVoiceProtocol.MAGIC_BYTES:
                return None
            payload = frame_data[OpulentVoiceProtocol.HEADER_SIZE:OpulentVoiceProtocol.HEADER_SIZE + payload_len]
            return {
                'station_id': station_id,
                'type': frame_type,
                'sequence': sequence,
                'payload': payload,
                'timestamp': time.time()
            }
        except struct.error:
            return None
class AudioPlayer:
    """Audio playback using PyAudio"""
    def __init__(self, sample_rate=48000, channels=1):
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio = pyaudio.PyAudio()
        self.output_stream = None
        self.audio_queue = queue.Queue(maxsize=50)  # Buffer up to 50 frames
        self.running = False
        # OPUS decoder
        self.decoder = opuslib.Decoder(fs=sample_rate, channels=channels)
        # Statistics
        self.stats = {
            'frames_decoded': 0,
            'frames_played': 0,
            'decode_errors': 0,
            'queue_overflows': 0
        }
        self.setup_audio_output()
    def setup_audio_output(self):
        """Setup audio output stream"""
        try:
            # Find default output device
            default_output = self.audio.get_default_output_device_info()
            print(replace_colons(f":loud_sound: Audio output: {default_output['name']}"))
            self.output_stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=1920,  # 40ms at 48kHz
                stream_callback=self.audio_callback
            )
            print(f"✓ Audio output ready: {self.sample_rate}Hz, {self.channels} channel(s)")
        except Exception as e:
            print(f"✗ Audio output error: {e}")
            raise
    def audio_callback(self, in_data, frame_count, time_info, status):
        """Audio output callback"""
        try:
            # Get decoded audio from queue
            if not self.audio_queue.empty():
                audio_data = self.audio_queue.get_nowait()
                self.stats['frames_played'] += 1
                return (audio_data, pyaudio.paContinue)
            else:
                # No audio available, play silence
                silence = b'\x00' * (frame_count * 2 * self.channels)
                return (silence, pyaudio.paContinue)
        except Exception as e:
            print(f"✗ Audio callback error: {e}")
            silence = b'\x00' * (frame_count * 2 * self.channels)
            return (silence, pyaudio.paContinue)
    def decode_and_queue_audio(self, opus_packet):
        """Decode OPUS packet and queue for playback"""
        try:
            # Decode OPUS to PCM
            pcm_audio = self.decoder.decode(opus_packet, 1920, decode_fec=False)
            self.stats['frames_decoded'] += 1
            # Add to playback queue
            if self.audio_queue.full():
                # Remove oldest frame if queue is full
                try:
                    self.audio_queue.get_nowait()
                    self.stats['queue_overflows'] += 1
                except queue.Empty:
                    pass
            self.audio_queue.put(pcm_audio)
        except Exception as e:
            self.stats['decode_errors'] += 1
            print(f"✗ OPUS decode error: {e}")
    def start(self):
        """Start audio playback"""
        if self.output_stream:
            self.output_stream.start_stream()
            self.running = True
            print(replace_colons(":musical_note: Audio playback started"))
    def stop(self):
        """Stop audio playback"""
        self.running = False
        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
        self.audio.terminate()
        print(replace_colons(":octagonal_sign: Audio playback stopped"))
    def get_stats(self):
        """Get playback statistics"""
        return self.stats.copy()
class OpulentVoiceReceiver:
    """Main receiver class"""
    def __init__(self, listen_ip="0.0.0.0", listen_port=8080):
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.socket = None
        self.running = False
        # Components
        self.protocol = OpulentVoiceProtocol()
        self.audio_player = AudioPlayer()
        # Statistics
        self.stats = {
            'packets_received': 0,
            'valid_frames': 0,
            'audio_frames': 0,
            'control_frames': 0,
            'invalid_frames': 0,
            'bytes_received': 0
        }
        # PTT state tracking
        self.ptt_active = False
        self.last_audio_time = 0
        self.setup_socket()
    def setup_socket(self):
        """Setup UDP listening socket"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.listen_ip, self.listen_port))
            print(replace_colons(f":globe_with_meridians: Listening on {self.listen_ip}:{self.listen_port}"))
        except Exception as e:
            print(f"✗ Socket setup error: {e}")
            raise
    def process_frame(self, frame_data, sender_addr):
        """Process received Opulent Voice frame"""
        parsed_frame = self.protocol.parse_frame(frame_data)
        if not parsed_frame:
            self.stats['invalid_frames'] += 1
            return
        self.stats['valid_frames'] += 1
        frame_type = parsed_frame['type']
        sequence = parsed_frame['sequence']
        payload = parsed_frame['payload']
        if frame_type == OpulentVoiceProtocol.FRAME_TYPE_AUDIO:
            self.stats['audio_frames'] += 1
            self.last_audio_time = time.time()
            # Decode and play audio
            self.audio_player.decode_and_queue_audio(payload)
            print(replace_colons(f":musical_note: Audio frame #{sequence}: {len(payload)} bytes OPUS"))
        elif frame_type == OpulentVoiceProtocol.FRAME_TYPE_CONTROL:
            self.stats['control_frames'] += 1
            message = payload.decode('utf-8', errors='ignore')
            if message == "PTT_START":
                self.ptt_active = True
                print(replace_colons(f":microphone: PTT START from {sender_addr[0]}"))
            elif message == "PTT_STOP":
                self.ptt_active = False
                print(replace_colons(f":mute: PTT STOP from {sender_addr[0]}"))
            else:
                print(f":clipboard: Control: {message}")
        elif frame_type == OpulentVoiceProtocol.FRAME_TYPE_TEXT:
            text_message = payload.decode('utf-8', errors='ignore')
            print(replace_colons(f":speech_balloon: Text frame #{sequence}: {text_message}"))
        else:
            print(replace_colons(f":question: Unknown frame type: {frame_type}"))
    def listen_loop(self):
        """Main listening loop"""
        print(replace_colons(":ear: Listening for Opulent Voice packets..."))
        while self.running:
            try:
                # Receive packet
                data, sender_addr = self.socket.recvfrom(4096)
                self.stats['packets_received'] += 1
                self.stats['bytes_received'] += len(data)
                # Process frame
                self.process_frame(data, sender_addr)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:  # Only print error if we're supposed to be running
                    print(f"✗ Receive error: {e}")
    def print_status(self):
        """Print current status"""
        now = datetime.now().strftime("%H:%M:%S")
        audio_stats = self.audio_player.get_stats()
        print(replace_colons(f"\n:bar_chart: Status at {now}:"))
        print(f"   Packets received: {self.stats['packets_received']}")
        print(f"   Valid frames: {self.stats['valid_frames']}")
        print(f"   Audio frames: {self.stats['audio_frames']}")
        print(f"   Control frames: {self.stats['control_frames']}")
        print(f"   OPUS decoded: {audio_stats['frames_decoded']}")
        print(f"   Audio played: {audio_stats['frames_played']}")
        print(f"   Decode errors: {audio_stats['decode_errors']}")
        print(f"   PTT status: {'ACTIVE' if self.ptt_active else 'INACTIVE'}")
        if self.last_audio_time > 0:
            time_since_audio = time.time() - self.last_audio_time
            print(f"   Last audio: {time_since_audio:.1f}s ago")
    def start(self):
        """Start the receiver"""
        self.running = True
        self.audio_player.start()
        # Start listening in a separate thread
        self.listen_thread = threading.Thread(target=self.listen_loop)
        self.listen_thread.daemon = True
        self.listen_thread.start()
        print(replace_colons(":rocket: Opulent Voice Receiver started"))
    def stop(self):
        """Stop the receiver"""
        self.running = False
        self.audio_player.stop()
        if self.socket:
            self.socket.close()
        print(replace_colons(":octagonal_sign: Receiver stopped"))

# Main execution
if __name__ == "__main__":

    print("=" * 50)
    print(replace_colons(":studio_microphone: Opulent Voice Receiver"))
    print("=" * 50)
    # Configuration
    LISTEN_PORT = 8080
    print(replace_colons(f":satellite_antenna: Will listen on port {LISTEN_PORT}"))
    print(replace_colons(":loud_sound: Make sure your speakers/headphones are connected!"))
    print()
    try:
        # Create and start receiver
        receiver = OpulentVoiceReceiver(listen_port=LISTEN_PORT)
        receiver.start()
        print(replace_colons("\n:white_check_mark: Receiver ready! Waiting for transmissions..."))
        print(replace_colons(":bar_chart: Press Ctrl+C to show stats and exit"))
        # Status updates every 10 seconds
        last_status = time.time()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(replace_colons("\n:bar_chart: Final Statistics:"))
        receiver.print_status()
        print(replace_colons("\n:wave: Thanks for using Opulent Voice Receiver!"))
    except Exception as e:
        print(f"✗ Error: {e}")
    finally:
        receiver.stop()
