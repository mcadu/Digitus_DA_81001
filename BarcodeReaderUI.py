#!/usr/bin/env python3
import itertools
import json
import sys
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import PySimpleGUI as sg

from BarcodeReader import (BarcodeReader, HidBarcodeReader, KeyboardBarcodeReader, MODES, MODE_HID_RAW,
                           MODE_KEYBOARD, MODE_SERIAL)

BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
SYMBOL_FONT = ("Segoe UI Emoji", 48)
SIM_BOOK_COUNTS = (3, 4, 5)
SIM_DELAY = 0.5  # seconds between simulated scans


def load_symbols(path=BASE_DIR / "user.json"):
    """Return (barcode -> symbol map, default symbol); empty if the file is missing or invalid."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data.get("codes", {})), data.get("default", "")
    except (OSError, ValueError):
        return {}, ""


class BarcodeReaderUI:
    def __init__(self):
        self.users, self.default_symbol = load_symbols(BASE_DIR / "user.json")
        self.books, _ = load_symbols(BASE_DIR / "books.json")
        self.loans = {}  # user code -> [(book code, symbol, bring back date)]
        self.current_user = None
        ports = BarcodeReader.listPorts()
        self.hid_devices = {}
        settings_tab = [
            [sg.Text("Modus:"), sg.Combo(MODES, default_value=MODE_KEYBOARD, key="-MODE-", readonly=True,
                                         enable_events=True, size=(20, 1))],
            [sg.Text("Gerät:", key="-DEVICE_LABEL-"),
             sg.Combo(ports, default_value=ports[0] if ports else "/dev/ttyACM0", key="-PORT-", size=(40, 1)),
             sg.Button("Aktualisieren", key="-REFRESH-"),
             sg.Text("Baudrate:", key="-BAUD_LABEL-"), sg.Input("9600", key="-BAUD-", size=(7, 1)),
             sg.Button("Verbinden", key="-CONNECT-"), sg.Button("Trennen", key="-DISCONNECT-", disabled=True)],
            [sg.Text("Status:"), sg.Text("Getrennt", key="-STATUS-", text_color="red")],
            [sg.Frame("Simulation", [
                [sg.Button("Abspielen", key="-SIM_PLAY-")],
                [sg.Text("Einzeln:"), sg.Input(key="-SIM_CODE-", size=(30, 1)),
                 sg.Button("Senden", key="-SIM_SEND-", bind_return_key=False)]
            ])]
        ]

        output_tab = [
            [sg.Text("", key="-SYMBOL-", font=SYMBOL_FONT, size=(4, 1), justification="center")],
            [sg.Text("Benutzer:"), sg.Text("", key="-USER-", font=("Segoe UI Emoji", 16), size=(30, 1))],
            [sg.Table([], headings=["Buch", "Symbol", "Rückgabe"], key="-LOANS-", num_rows=8,
                      auto_size_columns=False, col_widths=[14, 12, 12], justification="center",
                      font=("Segoe UI Emoji", 24), row_height=42, expand_x=True, expand_y=True)],
            [sg.Text("Letzter Barcode:")],
            [sg.Text("", key="-LAST-", font=("Courier", 20), size=(40, 1), relief=sg.RELIEF_SUNKEN)]
        ]

        history_tab = [
            [sg.Text("Verlauf:")],
            [sg.Multiline(size=(60, 15), key="-OUTPUT-", autoscroll=True, disabled=True, expand_x=True,
                          expand_y=True, font=("Courier", 10))],
            [sg.Button("Leeren", key="-CLEAR-")]
        ]

        self.layout = [
            [sg.Text("Bibliothek", font=("Arial", 16))],
            [sg.TabGroup([[
                sg.Tab("Einstellungen", settings_tab),
                sg.Tab("Ausgabe", output_tab),
                sg.Tab("Verlauf", history_tab)
            ]], expand_x=True, expand_y=True)],
            [sg.Button("Beenden", key="Exit")]
        ]
        self.window = sg.Window("Bibliothek", self.layout, resizable=True, finalize=True)
        self.reader = None
        self.thread = None
        self.stop_event = threading.Event()
        self.sim_thread = None
        self.sim_stop_event = threading.Event()
        self.update_mode(MODE_KEYBOARD)

    def log_output(self, message):
        self.window["-OUTPUT-"].update(f"{message}\n", append=True)

    def show_user(self):
        user = self.current_user
        self.window["-USER-"].update(f"{self.users[user]}  {user}" if user else "")
        self.window["-LOANS-"].update(
            [[code, symbol, f"{due:%d.%m.%Y}"] for code, symbol, due in self.loans.get(user, [])])

    def handle_barcode(self, code):
        """A user code selects the user; a book code is lent to the selected user until tomorrow."""
        self.window["-LAST-"].update(code)
        now = datetime.now()
        if code in self.users:
            self.current_user = code
            self.loans.setdefault(code, [])
            symbol = self.users[code]
            self.show_user()
            self.window["-SYMBOL-"].update(symbol)
            self.log_output(f"{now:%H:%M:%S}  {symbol}  {code}  (Benutzer)")
        elif code in self.books:
            symbol = self.books[code]
            if self.current_user is None:
                self.log_output(f"{now:%H:%M:%S}  {symbol}  {code}  zuerst einen Benutzercode scannen")
            else:
                existing_loan = next(
                    ((user, index)
                     for user, loans in self.loans.items()
                     for index, (book, _, _) in enumerate(loans)
                     if book == code),
                    None)
                if existing_loan is None:
                    due = (now + timedelta(days=1)).date()
                    self.loans[self.current_user].append((code, symbol, due))
                    self.show_user()
                    self.log_output(f"{now:%H:%M:%S}  {symbol}  {code}  (Buch ausgeliehen, Rückgabe {due:%d.%m.%Y})")
                elif existing_loan[0] == self.current_user:
                    self.loans[self.current_user].pop(existing_loan[1])
                    self.show_user()
                    self.log_output(f"{now:%H:%M:%S}  {symbol}  {code}  (Buch zurückgegeben)")
                else:
                    self.log_output(f"{now:%H:%M:%S}  {symbol}  {code}  bereits an einen anderen Benutzer ausgeliehen")
        else:
            symbol = self.default_symbol
            self.log_output(f"{now:%H:%M:%S}  {symbol}  {code}  (unbekannt)")

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
                self.log_output(f"HID-Fehler (pip install hidapi): {e}")
            labels = list(self.hid_devices)
            self.window["-PORT-"].update(value=labels[0] if labels else "", values=labels)
        else:
            self.window["-PORT-"].update(value="(alle Tastatureingaben)", values=[])

    def create_reader(self, mode):
        if mode == MODE_SERIAL:
            return BarcodeReader(self.window["-PORT-"].get(), baudrate=int(self.window["-BAUD-"].get()))
        if mode == MODE_KEYBOARD:
            return KeyboardBarcodeReader()
        label = self.window["-PORT-"].get()
        if label not in self.hid_devices:
            raise ValueError("HID-Gerät auswählen")
        return HidBarcodeReader(self.hid_devices[label], description=label)

    def connect_reader(self):
        try:
            self.reader = self.create_reader(self.window["-MODE-"].get())
        except Exception as e:
            self.log_output(f"Verbindungsfehler: {e}")
            self.window["-STATUS-"].update("Verbindung fehlgeschlagen", text_color="red")
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.read_loop, args=(self.reader,), daemon=True)
        self.thread.start()
        self.window["-STATUS-"].update("Verbunden", text_color="green")
        self.window["-CONNECT-"].update(disabled=True)
        self.window["-DISCONNECT-"].update(disabled=False)
        self.window["-MODE-"].update(disabled=True)
        self.log_output(f"Verbunden mit {self.reader.description}")

    def disconnect_reader(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
            self.thread = None
        if self.reader:
            self.reader.close()
            self.reader = None
        self.window["-STATUS-"].update("Getrennt", text_color="red")
        self.window["-CONNECT-"].update(disabled=False)
        self.window["-DISCONNECT-"].update(disabled=True)
        self.window["-MODE-"].update(disabled=False)
        self.log_output("Getrennt")

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

    def start_simulation(self):
        """Demo: the first 3 users borrow 3, 4 and 5 books."""
        users, books = list(self.users)[:len(SIM_BOOK_COUNTS)], iter(self.books)
        codes = []
        for user, count in zip(users, SIM_BOOK_COUNTS):
            codes.append(user)
            codes.extend(itertools.islice(books, count))
        self.log_output(f"Simulation: {len(codes)} Barcodes werden abgespielt")
        self.sim_stop_event.clear()
        self.window["-SIM_PLAY-"].update(disabled=True)
        self.sim_thread = threading.Thread(target=self.sim_loop, args=(codes,), daemon=True)
        self.sim_thread.start()

    def sim_loop(self, codes):
        """Worker thread: emits the same event as the real scanner."""
        for code in codes:
            self.window.write_event_value("-BARCODE-", code)
            if self.sim_stop_event.wait(SIM_DELAY):
                return
        self.window.write_event_value("-SIM_DONE-", None)

    def run(self):
        self.log_output("Scanner-Modus wählen, verbinden und einen Barcode scannen oder die Simulation verwenden")

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
                    self.handle_barcode(values[event])
                elif event == "-SIM_PLAY-":
                    self.start_simulation()
                elif event == "-SIM_DONE-":
                    self.window["-SIM_PLAY-"].update(disabled=False)
                    self.log_output("Simulation beendet")
                elif event == "-SIM_SEND-":
                    code = values["-SIM_CODE-"].strip()
                    if code:
                        self.window.write_event_value("-BARCODE-", code)
                elif event == "-READ_ERROR-":
                    self.log_output(f"Lesefehler: {values[event]}")
                    self.disconnect_reader()
                elif event == "-CLEAR-":
                    self.window["-OUTPUT-"].update("")
                    self.window["-LAST-"].update("")
                    self.window["-SYMBOL-"].update("")
                    self.current_user = None
                    self.show_user()
            except Exception as e:
                self.log_output(f"Unerwarteter Fehler: {e}")
                self.log_output(traceback.format_exc())

        self.sim_stop_event.set()
        if self.reader:
            self.disconnect_reader()
        self.window.close()


if __name__ == "__main__":
    BarcodeReaderUI().run()
