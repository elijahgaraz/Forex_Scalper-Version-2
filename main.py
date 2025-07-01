# main.py
import sys
import os

# Add the project root to sys.path to allow absolute imports
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from gui import MainApplication
from settings import Settings

# For Twisted and Tkinter integration
_tksupport_installed = False
try:
    from twisted.internet import reactor, tksupport
    _tksupport_installed = True
except ImportError:
    print("WARNING: Twisted 'reactor' or 'tksupport' not found. "
          "The application might not run correctly with OpenApiPy client.")
    reactor = None # type: ignore

def main() -> None:
    settings = Settings.load()
    app = MainApplication(settings)

    if _tksupport_installed and reactor:
        print("Installing tksupport for Tkinter and Twisted reactor integration.")
        tksupport.install(app) # app is the tk.Tk instance
        # reactor.run() will process both Twisted events and Tkinter events.
        # It replaces app.mainloop().
        # The Trader class should be aware if reactor is run this way
        # to avoid starting its own reactor thread.
        print("Starting Twisted reactor (which now includes Tkinter event loop).")
        reactor.run()
    else:
        print("Running Tkinter mainloop without Twisted reactor integration (OpenApiPy client may not function).")
        app.mainloop()

if __name__ == "__main__":
    main()
