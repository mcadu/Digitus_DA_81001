#!/usr/bin/env python3
import json
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import PySimpleGUI as sg

from BarcodeReader import (BarcodeReader, HidBarcodeReader, KeyboardBarcodeReader, MODES, MODE_HID_RAW,
                           MODE_KEYBOARD, MODE_SERIAL)

BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
SYMBOL_FONT = ("Segoe UI Emoji", 48)


def load_symbols(path=BASE_DIR / "symbols.json"):
    """Return (barcode -> symbol map, default symbol); empty if the file is missing or invalid."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data.get("codes", {})), data.get("default", "")
    except (OSError, ValueError):
        return {}, ""


class BarcodeReaderUI:
    def __init__(self):
        self.symbols, self.default_symbol = load_symbols()
        ports = BarcodeReader.listPorts()
        self.hid_devices = {}
        settings_tab = [
            [sg.Text("Mode:"), sg.Combo(MODES, default_value=MODE_SERIAL, key="-MODE-", readonly=True,
                                        enable_events=True, size=(20, 1))],
            [sg.Text("Device:", key="-DEVICE_LABEL-"),
             sg.Combo(ports, default_value=ports[0] if ports else "/dev/ttyACM0", key="-PORT-", size=(40, 1)),
             sg.Button("Refresh", key="-REFRESH-"),
             sg.Text("Baud:", key="-BAUD_LABEL-"), sg.Input("9600", key="-BAUD-", size=(7, 1)),
             sg.Button("Connect", key="-CONNECT-"), sg.Button("Disconnect", key="-DISCONNECT-", disabled=True)],
            [sg.Text("Status:"), sg.Text("Disconnected", key="-STATUS-", text_color="red")],
            [sg.Frame("Simulation", [
                [sg.Text("File:"),
                 sg.Input(str(BASE_DIR / "sample_barcodes.txt"), key="-SIM_FILE-", size=(40, 1), enable_events=True),
                 sg.FileBrowse(file_types=(("Text files", "*.txt"), ("All files", "*.*")))],
                [sg.Text("Delay (ms):"), sg.Input("500", key="-SIM_DELAY-", size=(6, 1)),
                 sg.Button("Play file", key="-SIM_PLAY-"), sg.Button("Stop", key="-SIM_STOP-", disabled=True)],
                [sg.Text("Single:"), sg.Input(key="-SIM_CODE-", size=(30, 1)),
                 sg.Button("Send", key="-SIM_SEND-", bind_return_key=False)]
            ])]
        ]

        output_tab = [
            [sg.Text("Last barcode:")],
            [sg.Text("", key="-LAST-", font=("Courier", 20), size=(40, 1), relief=sg.RELIEF_SUNKEN)],
            [sg.Text("", key="-SYMBOL-", font=SYMBOL_FONT, size=(4, 1), justification="center")],
            [sg.Text("History:")],
            [sg.Multiline(size=(60, 15), key="-OUTPUT-", autoscroll=True, disabled=True, expand_x=True,
                          expand_y=True, font=("Courier", 10))],
            [sg.Button("Clear", key="-CLEAR-")]
        ]

        self.layout = [
            [sg.Text("Barcode Reader", font=("Arial", 16))],
            [sg.TabGroup([[
                sg.Tab("Settings", settings_tab),
                sg.Tab("Output", output_tab)
            ]], expand_x=True, expand_y=True)],
            [sg.Button("Exit")]
        ]
        self.window = sg.Window("Barcode Reader", self.layout, resizable=True, finalize=True)
        self.reader = None
        self.thread = None
        self.stop_event = threading.Event()
        self.sim_thread = None
        self.sim_stop_event = threading.Event()

    def log_output(self, message):
        self.window["-OUTPUT-"].update(f"{message}\n", append=True)

    def update_mode(self, mode):
        """Adapt the device list and enabled fields to the selected mode."""
        serial_mode = mode == MODE_SERIAL
        self.window["-BAUD-"].update(disabled=not serial_mode)
        self.window["-PORT-"].update(disabled=mode == MODE_KEYBOARD)
        self.window["-REFRESH-"].update(disabled=mode == MODE_KEYBOARD)
        if serial_mode:
            ports = BarcodeReader.listPorts()
            self.window["-PORT-"].update(value=ports[0] if ports else "/dev/ttyACM0", values=ports)
        elif mode == MODE_HID_RAW:
            try:
                self.hid_devices = HidBarcodeReader.listDevices()
            except Exception as e:
                self.hid_devices = {}
                self.log_output(f"HID error (pip install hidapi): {e}")
            labels = list(self.hid_devices)
            self.window["-PORT-"].update(value=labels[0] if labels else "", values=labels)
        else:
            self.window["-PORT-"].update(value="(all keyboard input)", values=[])

    def create_reader(self, mode):
        if mode == MODE_SERIAL:
            return BarcodeReader(self.window["-PORT-"].get(), baudrate=int(self.window["-BAUD-"].get()))
        if mode == MODE_KEYBOARD:
            return KeyboardBarcodeReader()
        label = self.window["-PORT-"].get()
        if label not in self.hid_devices:
            raise ValueError("select a HID device")
        return HidBarcodeReader(self.hid_devices[label], description=label)

    def connect_reader(self):
        try:
            self.reader = self.create_reader(self.window["-MODE-"].get())
        except Exception as e:
            self.log_output(f"Connection error: {e}")
            self.window["-STATUS-"].update("Connection Failed", text_color="red")
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.read_loop, args=(self.reader,), daemon=True)
        self.thread.start()
        self.window["-STATUS-"].update("Connected", text_color="green")
        self.window["-CONNECT-"].update(disabled=True)
        self.window["-DISCONNECT-"].update(disabled=False)
        self.window["-MODE-"].update(disabled=True)
        self.log_output(f"Connected to {self.reader.description}")

    def disconnect_reader(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
            self.thread = None
        if self.reader:
            self.reader.close()
            self.reader = None
        self.window["-STATUS-"].update("Disconnected", text_color="red")
        self.window["-CONNECT-"].update(disabled=False)
        self.window["-DISCONNECT-"].update(disabled=True)
        self.window["-MODE-"].update(disabled=False)
        self.log_output("Disconnected")

    def read_loop(self, reader):
        """Worker thread: forwards scans and errors to the GUI event loop."""
        while not self.stop_event.is_set():
            try:
                code = reader.readBarcode()
            except Exception as e:
                self.window.write_event_value("-READ_ERROR-", str(e))
                return
            if code:
                self.window.write_event_value("-BARCODE-", code)

    def start_simulation(self, path, delay_ms):
        """Play one barcode per line from a text file; empty lines and '#' comments are skipped."""
        if not path.strip():
            self.log_output("Simulation: select a file first (Settings tab)")
            return
        try:
            with open(path, encoding="utf-8") as f:
                codes = [line.strip() for line in f]
        except OSError as e:
            self.log_output(f"Simulation error: {e}")
            return
        codes = [c for c in codes if c and not c.startswith("#")]
        if not codes:
            self.log_output("Simulation: no barcodes in file")
            return
        self.log_output(f"Simulation: playing {len(codes)} barcode(s)")
        self.sim_stop_event.clear()
        self.window["-SIM_PLAY-"].update(disabled=True)
        self.window["-SIM_STOP-"].update(disabled=False)
        self.sim_thread = threading.Thread(target=self.sim_loop, args=(codes, delay_ms / 1000), daemon=True)
        self.sim_thread.start()

    def stop_simulation(self):
        self.sim_stop_event.set()
        if self.sim_thread:
            self.sim_thread.join()
            self.sim_thread = None
        self.window["-SIM_PLAY-"].update(disabled=False)
        self.window["-SIM_STOP-"].update(disabled=True)

    def sim_loop(self, codes, delay):
        """Worker thread: emits the same event as the real scanner."""
        for code in codes:
            if self.sim_stop_event.is_set():
                return
            self.window.write_event_value("-BARCODE-", code)
            if self.sim_stop_event.wait(delay):
                return
        self.window.write_event_value("-SIM_DONE-", None)

    def run(self):
        self.log_output("Select the scanner mode, connect and scan a barcode, or use the simulation")

        while True:
            event, values = self.window.read()
            if event in (sg.WIN_CLOSED, "Exit"):
                break

            try:
                if event == "-CONNECT-":
                    self.connect_reader()
                elif event == "-DISCONNECT-":
                    self.disconnect_reader()
                elif event in ("-MODE-", "-REFRESH-"):
                    self.update_mode(values["-MODE-"])
                elif event == "-BARCODE-":
                    symbol = self.symbols.get(values[event], self.default_symbol)
                    self.window["-LAST-"].update(values[event])
                    self.window["-SYMBOL-"].update(symbol)
                    self.log_output(f"{datetime.now():%H:%M:%S}  {symbol}  {values[event]}")
                elif event == "-SIM_PLAY-":
                    try:
                        delay_ms = max(0, int(values["-SIM_DELAY-"]))
                    except ValueError:
                        self.log_output("Simulation: delay must be a number")
                        continue
                    self.start_simulation(values["-SIM_FILE-"], delay_ms)
                elif event == "-SIM_STOP-":
                    self.stop_simulation()
                elif event == "-SIM_DONE-":
                    self.stop_simulation()
                    self.log_output("Simulation finished")
                elif event == "-SIM_SEND-":
                    code = values["-SIM_CODE-"].strip()
                    if code:
                        self.window.write_event_value("-BARCODE-", code)
                elif event == "-READ_ERROR-":
                    self.log_output(f"Read error: {values[event]}")
                    self.disconnect_reader()
                elif event == "-CLEAR-":
                    self.window["-OUTPUT-"].update("")
                    self.window["-LAST-"].update("")
                    self.window["-SYMBOL-"].update("")
            except Exception as e:
                self.log_output(f"Unexpected error: {e}")
                self.log_output(traceback.format_exc())

        self.sim_stop_event.set()
        if self.reader:
            self.disconnect_reader()
        self.window.close()


if __name__ == "__main__":
    BarcodeReaderUI().run()
