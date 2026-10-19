import gi
gi.require_version("Gtk","3.0")
from gi.repository import Gtk
w = Gtk.Window(); w.set_decorated(False); w.fullscreen()
b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL); w.add(b)
def clicked(*_a):
    print("CLICKED-BUTTON", flush=True)
btn = Gtk.Button(label="点击我 / click me")
btn.set_size_request(-1, 240)
btn.connect("clicked", clicked)
b.pack_end(btn, False, False, 0)
w.add(Gtk.Label(label="FAKE MOVIE"))
w.connect("destroy", Gtk.main_quit)
w.show_all(); Gtk.main()
