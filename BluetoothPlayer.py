import dbus
import dbus.mainloop.glib
import re
import threading
import time
from concurrent.futures.thread import ThreadPoolExecutor
from gi.repository import GLib
from TextMessage import TextMessage


class BluetoothPlayer:

    listeners = {}
    mainloop = None
    bus = None
    connect_thread = None
    should_run = True
    executor = None
    tm = TextMessage()

    bt_connected = False
    media_connected = False
    music_playing = False
    possible_pause = False
    media_player = None

    pause_music = None
    play_music = None
    next_music = None
    prev_music = None

    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.mainloop = GLib.MainLoop()
        self.bus = dbus.SystemBus()

        self.bus.add_signal_receiver(self.properties_changed,
                                     bus_name="org.bluez",
                                     dbus_interface="org.freedesktop.DBus.Properties",
                                     signal_name="PropertiesChanged",
                                     path_keyword="path")

        self.executor = ThreadPoolExecutor(max_workers=4)
        thread = threading.Thread(target=self.start)
        thread.start()

    def start(self):
        self.connect_thread = threading.Thread(target=self.connect_device)
        self.connect_thread.start()

        self.mainloop.run()

    def evaluate_play_status(self, new_status):
        if new_status == "playing":
            self.possible_pause = False
            self.music_playing = True
            print("[player] music is playing")
            self.fire_event('playing', self.music_playing)
        else:
            self.possible_pause = True
            time.sleep(0.5)
            if self.possible_pause:
                self.music_playing = False
                print("[player] music is paused")
                self.fire_event('playing', self.music_playing)

    def shutdown(self):
        self.should_run = False
        self.mainloop.quit()

    def connect_device(self):
        while self.should_run:
            time.sleep(10)
            if not self.bt_connected:
                print("[player] connecting bluetooth...")
                obj = self.bus.get_object("org.bluez", "/")
                interface = dbus.Interface(obj, "org.freedesktop.DBus.ObjectManager")
                regexp = re.compile("(^/org/bluez/hci[0-9]/dev_[A-Z0-9_]+$)")

                for object in interface.GetManagedObjects():
                    matches = regexp.match(object)
                    if matches is not None:
                        hciobj = self.bus.get_object("org.bluez", matches[0])
                        hciiface = dbus.Interface(hciobj, "org.bluez.Device1")
                        conn = hciiface.get_dbus_method("Connect")
                        print("[player] connecting to {}...".format(matches[0]))
                        try:
                            conn()
                            break
                        except:
                            print("[player] connection to {} failed".format(matches[0]))
                            pass
            time.sleep(50)

    def properties_changed(self, interface, changed, invalidated, path):
        if interface == "org.bluez.Device1":
            if "Connected" in changed:
                self.bt_connected = changed["Connected"]
                print("[player] connection state: {}".format(self.bt_connected))

        if interface == "org.bluez.MediaControl1":
            if "Player" in changed:
                self.media_player = changed["Player"]
            if "Connected" in changed:
                self.media_connected = changed["Connected"]
                if self.media_connected and self.media_player:
                    print("[player] bluetooth media is connected: " + self.media_player)
                    obj = self.bus.get_object("org.bluez", self.media_player)
                    media_interface = dbus.Interface(obj, "org.bluez.MediaPlayer1")
                    self.pause_music = media_interface.get_dbus_method("Pause")
                    self.play_music = media_interface.get_dbus_method("Play")
                    self.next_music = media_interface.get_dbus_method("Next")
                    self.prev_music = media_interface.get_dbus_method("Previous")
                    time.sleep(5)
                    self.play_music()

        elif interface == "org.bluez.MediaPlayer1":
            if "Track" in changed:
                track = changed["Track"]
                self.executor.submit(self.notify_track, track["Title"], track["Artist"])

            if "Status" in changed:
                self.executor.submit(self.evaluate_play_status, changed["Status"])

            if "Position" in changed:
                player_position = int(changed["Position"]/1000)
                self.fire_event('position', player_position)

    def notify_track(self, title, artist):
        print("[player] track: {} - {}".format(title, artist))
        self.tm.send_music(self.bus, title, artist)
        # sometimes the radio unit does not display the track
        # maybe it must be sent in sync with something else
        # however, sending the track again after some time "fixes" the issue
        time.sleep(1)
        self.tm.send_music(self.bus, title, artist)

    def on_bm_playing(self, is_playing):
        print("[player] on_bm_playing {}".format(is_playing))
        if self.media_connected:
            if is_playing:
                print("Player: play")
                self.play_music()
            else:
                print("Player: stop")
                self.pause_music()

    def on_button(self, key):
        print("[player] on_button "+key)
        if self.media_connected:
            if key == 'up':
                self.next_music()
            elif key == 'down':
                self.prev_music()
            elif key == 'mute':
                if not self.music_playing:
                    self.play_music()
                else:
                    self.pause_music()
        else:
            print("[player] media not connected, ignoring key {}".format(key))

    def fire_event(self, event, *args):
        if event not in self.listeners:
            return
        for listener in self.listeners[event]:
            listener(*args)

    def on_event(self, event, callback):
        if event not in self.listeners:
            self.listeners[event] = []
        self.listeners[event].append(callback)
